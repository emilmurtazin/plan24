"""Группы Telegram для проблем с участков: настраивает только администратор организации."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, config, report_schedules as svc
from ..db import DB
from ..deps import get_or_404, require, scoped
from ..models import ProblemRoute, User, WorkCenter

router = APIRouter(prefix="/problem-routes", tags=["problem-routes"])
OWNER = require("owner")
LIMIT = 20


class RouteIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    chat_ref: str = Field(min_length=1, max_length=200)
    section_ids: list[int] = Field(default_factory=list)


class RoutePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    enabled: Optional[bool] = None
    chat_ref: Optional[str] = Field(default=None, min_length=1, max_length=200)
    section_ids: Optional[list[int]] = None


def _clean(db: Session, user: User, data: dict) -> dict:
    out = dict(data)
    if "name" in out:
        out["name"] = out["name"].strip()
        if not out["name"]:
            raise HTTPException(422, "Укажите название")
    if "chat_ref" in out:
        try:
            out["chat_ref"] = svc.normalize_chat(out["chat_ref"])
        except ValueError as e:
            raise HTTPException(422, str(e))
    if "section_ids" in out:
        ids = sorted(set(out["section_ids"]))
        have = set(db.scalars(select(WorkCenter.id).where(WorkCenter.tenant_id == user.tenant_id, WorkCenter.id.in_(ids)))) if ids else set()
        if set(ids) - have:
            raise HTTPException(422, "Участок не найден")
        out["section_ids"] = ids
    return out


def _view(r: ProblemRoute) -> dict:
    return {"id": r.id, "name": r.name, "enabled": r.enabled, "chat_ref": r.chat_ref, "section_ids": r.section_ids,
            "last_status": r.last_status, "last_error": r.last_error}


@router.get("")
def list_routes(user: User = Depends(OWNER), db: Session = DB):
    cfg = config.telegram_settings()
    return {"items": [_view(r) for r in db.scalars(scoped(ProblemRoute, user).order_by(ProblemRoute.id))],
            "sections": [{"id": c.id, "name": c.name} for c in db.scalars(scoped(WorkCenter, user).order_by(WorkCenter.name))],
            "bot": {"configured": cfg["enabled"], "username": cfg["bot_username"]}, "limit": LIMIT}


@router.post("", status_code=201)
def create_route(body: RouteIn, user: User = Depends(OWNER), db: Session = DB):
    if len(list(db.scalars(scoped(ProblemRoute, user)))) >= LIMIT:
        raise HTTPException(409, f"Не больше {LIMIT} групп")
    data = _clean(db, user, body.model_dump())
    r = ProblemRoute(tenant_id=user.tenant_id, **data)
    db.add(r)
    db.flush()
    audit.log(db, user, "problem_route", r.id, "created", data)
    return _view(r)


@router.patch("/{rid}")
def update_route(rid: int, body: RoutePatch, user: User = Depends(OWNER), db: Session = DB):
    r = get_or_404(db, ProblemRoute, rid, user)
    data = _clean(db, user, body.model_dump(exclude_unset=True, exclude_none=True))
    for k, v in data.items():
        setattr(r, k, v)
    if "chat_ref" in data:
        r.last_status = r.last_error = None
    audit.log(db, user, "problem_route", r.id, "updated", data)
    return _view(r)


@router.delete("/{rid}", status_code=204)
def delete_route(rid: int, user: User = Depends(OWNER), db: Session = DB):
    r = get_or_404(db, ProblemRoute, rid, user)
    audit.log(db, user, "problem_route", r.id, "deleted", {"name": r.name})
    db.delete(r)


@router.post("/{rid}/test")
def test_route(rid: int, user: User = Depends(OWNER), db: Session = DB):
    """Пробное сообщение в группу: видно, что бот в группе и может писать."""
    r = get_or_404(db, ProblemRoute, rid, user)
    try:
        svc.deliver(r.chat_ref, [f"✅ Проверка связи Plan 24. Сюда будут приходить проблемы работников: «{r.name}»."])
        r.last_status, r.last_error = "ok", None
        err = None
    except svc.SendError as e:
        r.last_status, r.last_error, err = "error", e.text[:300], e.text
    if err:
        db.commit()
        raise HTTPException(502, err)
    return _view(r)
