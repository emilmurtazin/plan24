"""Регулярные отчёты в группы Telegram: настраивает только администратор организации (роль owner)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, config, report_schedules as svc
from ..db import DB
from ..deps import get_or_404, require, scoped, tenant_of
from ..models import ReportSchedule, User, WorkCenter, utcnow

router = APIRouter(prefix="/report-schedules", tags=["report-schedules"])
OWNER = require("owner")


class ReportIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    chat_ref: str = Field(min_length=1, max_length=200)
    send_time: str
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)))
    metrics: list[str] = Field(default_factory=lambda: list(svc.DEFAULT_METRICS))
    section_ids: list[int] = Field(default_factory=list)


class ReportPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    enabled: Optional[bool] = None
    chat_ref: Optional[str] = Field(default=None, min_length=1, max_length=200)
    send_time: Optional[str] = None
    weekdays: Optional[list[int]] = None
    metrics: Optional[list[str]] = None
    section_ids: Optional[list[int]] = None


def _clean(db: Session, user: User, data: dict) -> dict:
    """Проверка и нормализация полей; ошибки — 422 с понятным текстом."""
    out = dict(data)
    if "name" in out:
        out["name"] = out["name"].strip()
        if not out["name"]:
            raise HTTPException(422, "Укажите название отчёта")
    if "chat_ref" in out:
        try:
            out["chat_ref"] = svc.normalize_chat(out["chat_ref"])
        except ValueError as e:
            raise HTTPException(422, str(e))
    if "send_time" in out and not svc.TIME_RE.fullmatch(out["send_time"] or ""):
        raise HTTPException(422, "Время — в формате ЧЧ:ММ, например 08:30 (московское)")
    if "weekdays" in out:
        days = sorted(set(out["weekdays"]))
        if not days or any(d < 0 or d > 6 for d in days):
            raise HTTPException(422, "Выберите хотя бы один день недели")
        out["weekdays"] = days
    if "metrics" in out:
        if not out["metrics"] or any(m not in svc.METRIC_KEYS for m in out["metrics"]):
            raise HTTPException(422, "Выберите хотя бы одну метрику из списка")
        out["metrics"] = [k for k in svc.METRIC_KEYS if k in set(out["metrics"])]       # порядок — как в каталоге
    if "section_ids" in out:
        ids = sorted(set(out["section_ids"]))
        have = {c for c in db.scalars(select(WorkCenter.id).where(WorkCenter.tenant_id == user.tenant_id, WorkCenter.id.in_(ids)))} if ids else set()
        if set(ids) - have:
            raise HTTPException(422, "Участок не найден")
        out["section_ids"] = ids
    return out


def _view(rs: ReportSchedule) -> dict:
    return {"id": rs.id, "name": rs.name, "enabled": rs.enabled, "chat_ref": rs.chat_ref, "send_time": rs.send_time,
            "weekdays": rs.weekdays, "metrics": rs.metrics, "section_ids": rs.section_ids,
            "last_status": rs.last_status, "last_error": rs.last_error,
            "last_sent_at": rs.last_sent_at.isoformat() + "Z" if rs.last_sent_at else None,
            "last_attempt_at": rs.last_attempt_at.isoformat() + "Z" if rs.last_attempt_at else None}


@router.get("")
def list_reports(user: User = Depends(OWNER), db: Session = DB):
    cfg = config.telegram_settings()
    centers = [{"id": c.id, "name": c.name} for c in db.scalars(scoped(WorkCenter, user).order_by(WorkCenter.name))]
    items = [_view(r) for r in db.scalars(scoped(ReportSchedule, user).order_by(ReportSchedule.id))]
    return {"items": items, "metrics": svc.catalog(), "sections": centers, "default_metrics": svc.DEFAULT_METRICS,
            "bot": {"configured": cfg["enabled"], "username": cfg["bot_username"]}, "timezone": "Europe/Moscow",
            "limit": svc.MAX_PER_TENANT}


@router.post("", status_code=201)
def create_report(body: ReportIn, user: User = Depends(OWNER), db: Session = DB):
    if len(list(db.scalars(scoped(ReportSchedule, user)))) >= svc.MAX_PER_TENANT:
        raise HTTPException(409, f"Не больше {svc.MAX_PER_TENANT} отчётов")
    data = _clean(db, user, body.model_dump())
    if db.scalar(scoped(ReportSchedule, user).where(ReportSchedule.name == data["name"])):
        raise HTTPException(409, "Отчёт с таким названием уже есть")
    rs = ReportSchedule(tenant_id=user.tenant_id, created_by=user.id, **data)
    db.add(rs)
    db.flush()
    audit.log(db, user, "report_schedule", rs.id, "created", {k: data[k] for k in ("name", "chat_ref", "send_time", "weekdays", "metrics", "section_ids")})
    return _view(rs)


@router.post("/preview")
def preview(body: ReportIn, user: User = Depends(OWNER), db: Session = DB):
    """Как будет выглядеть отчёт с такими настройками (без сохранения и отправки)."""
    data = _clean(db, user, body.model_dump())
    msgs, err = svc.compose(db, tenant_of(db, user), data["name"], data["metrics"], data["section_ids"], utcnow())
    if err:
        raise HTTPException(422, err)
    return {"messages": msgs}


@router.patch("/{rid}")
def update_report(rid: int, body: ReportPatch, user: User = Depends(OWNER), db: Session = DB):
    rs = get_or_404(db, ReportSchedule, rid, user)
    data = _clean(db, user, body.model_dump(exclude_unset=True, exclude_none=True))
    if "name" in data and data["name"] != rs.name and db.scalar(scoped(ReportSchedule, user).where(ReportSchedule.name == data["name"])):
        raise HTTPException(409, "Отчёт с таким названием уже есть")
    for k, v in data.items():
        setattr(rs, k, v)
    if {"chat_ref", "metrics", "section_ids", "send_time", "enabled"} & set(data):
        rs.last_status = rs.last_error = None                    # прежняя ошибка относилась к старым настройкам
    audit.log(db, user, "report_schedule", rs.id, "updated", data)
    return _view(rs)


@router.delete("/{rid}", status_code=204)
def delete_report(rid: int, user: User = Depends(OWNER), db: Session = DB):
    rs = get_or_404(db, ReportSchedule, rid, user)
    audit.log(db, user, "report_schedule", rs.id, "deleted", {"name": rs.name})
    db.delete(rs)


@router.post("/{rid}/preview")
def preview_saved(rid: int, user: User = Depends(OWNER), db: Session = DB):
    rs = get_or_404(db, ReportSchedule, rid, user)
    msgs, err = svc.compose(db, tenant_of(db, user), rs.name, rs.metrics, rs.section_ids, utcnow())
    if err:
        raise HTTPException(422, err)
    return {"messages": msgs}


@router.post("/{rid}/send")
def send_now(rid: int, user: User = Depends(OWNER), db: Session = DB):
    """Отправить отчёт в группу прямо сейчас (проверка настройки). Расписание это не меняет."""
    rs = get_or_404(db, ReportSchedule, rid, user)
    err = svc.send_now(db, tenant_of(db, user), rs)
    audit.log(db, user, "report_schedule", rs.id, "sent_now", {"ok": err is None})
    if err:
        db.commit()                                              # запись об ошибке должна сохраниться, несмотря на ответ 502
        raise HTTPException(502, err)
    return _view(rs)
