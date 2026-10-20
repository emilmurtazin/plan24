"""Планы: перепланирование, снапшоты, откат, сравнение жёсткости (FR-15…FR-17, BR-13, BR-20)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import audit, distribution, planning, snapshots
from .. import db as dbm
from ..db import DB
from ..deps import ALL, MANAGERS, READERS, STAFF, get_or_404, get_today, require, scoped
from ..models import DayPin, Plan, PlanItem, User, WorkCenter
from ..planner.compare import compare_strictness
from ..telegram import client as tgclient, events as tgevents

router = APIRouter(prefix="/plans", tags=["plans"])


class RunIn(BaseModel):
    """Параметры одного запуска; постоянные значения хранятся в /settings."""
    strictness: Optional[float] = Field(None, ge=0, le=100)
    by_level: Optional[dict[str, float]] = None
    fix_group_start: Optional[bool] = None
    work_center_id: Optional[int] = None       # перепланировать только участок (бригадир — всегда только свой)


def _scope(db, user: User, b: RunIn) -> Optional[int]:
    """Область перепланирования — всегда участок. Бригадир — только свой; руководитель выбирает участок.
    Единственное исключение: активного плана ещё нет — первый расчёт строится сразу по всем участкам."""
    if user.role == "foreman":
        if user.work_center_id is None:
            raise HTTPException(403, "Бригадиру не назначен участок")
        if b.work_center_id not in (None, user.work_center_id):
            raise HTTPException(403, "Бригадир перепланирует только свой участок")
        return user.work_center_id
    if b.work_center_id is not None:
        get_or_404(db, WorkCenter, b.work_center_id, user)
        return b.work_center_id
    has_plan = db.scalar(select(Plan.id).where(Plan.tenant_id == user.tenant_id, Plan.status == "active"))
    if has_plan:
        raise HTTPException(422, "Выберите участок: перепланирование выполняется по участкам")
    return None          # первый расчёт организации


def _overrides(b: RunIn, scope: Optional[int] = None) -> dict:
    o = {}
    if scope is not None:
        o["scope_center_id"] = scope
    if b.strictness is not None:
        o["priority_strictness"] = b.strictness
    if b.by_level is not None:
        o["strictness_by_level"] = b.by_level
    if b.fix_group_start is not None:
        o["fix_group_start"] = b.fix_group_start
    return o


def _meta(db, user, p: Plan, today: date, with_warnings: bool) -> dict:
    ov = {"priority_strictness": p.params.get("strictness"), "strictness_by_level": p.params.get("by_level") or {},
          "fix_group_start": p.params.get("fix_group_start"), "efficiency": p.params.get("efficiency"),
          "scope_center_id": p.params.get("scope_center_id")}
    ov = {k: v for k, v in ov.items() if v is not None}
    inp, _ = planning.build_input(db, user.tenant_id, today, ov)
    n = db.scalar(select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == p.id))
    st = p.summary.get("status", [])
    by: dict = {}
    for r in st:
        by[r["status"]] = by.get(r["status"], 0) + 1
    out = {"id": p.id, "status": p.status, "created_at": p.created_at.isoformat(), "created_by": p.created_by,
           "params": p.params, "items": n, "orders_by_status": by, "warnings_count": len(p.warnings),
           "loaded_today": p.summary.get("loaded_today", []),
           "stale": planning.input_hash(inp) != p.input_hash,       # данные изменились после расчёта
           "work_days": p.summary.get("work_days", [])[:1] + p.summary.get("work_days", [])[-1:]}
    if with_warnings:
        ws = p.warnings
        if user.role == "foreman":            # бригадир видит предупреждения только по позициям со своими операциями
            mine = distribution.order_ids_of_center(db, user, user.work_center_id)
            ws = [w for w in ws if w.get("order_line_id") is None or w["order_line_id"] in mine]
            out["warnings_count"] = len(ws)
        out["warnings"] = ws
    return out


@router.post("/replan", status_code=201)
def replan(bg: BackgroundTasks, body: RunIn = RunIn(), user: User = Depends(require(*STAFF)), db: Session = DB,
           today: date = Depends(get_today)):
    """Синхронный расчёт (на ТЗ-объёмах доли секунды). Асинхронный job — когда понадобится очередь (этап E)."""
    scope = _scope(db, user, body)
    tg_days = [today, today + timedelta(days=1)]
    tg_before = tgevents.plan_state(db, user.tenant_id, tg_days) if tgevents.enabled() else None
    try:
        # одно перепланирование организации за раз; чтения — один согласованный снимок; фиксация — до снятия блокировки
        with dbm.tenant_plan_lock(user.tenant_id):
            dbm.begin_snapshot(db)
            p = planning.run_and_save(db, user, today, _overrides(body, scope))
            audit.log(db, user, "plan", p.id, "replan", p.params)
            db.commit()
    except planning.NoActivePlan:
        raise HTTPException(409, "Сначала нужен общий план организации — его строит руководитель")
    if tg_before is not None:                  # работникам в Telegram — что изменилось в их плане на сегодня/завтра
        msgs: list = []
        tgevents.notify_plan_changes(db, user.tenant_id, tg_before, tgevents.plan_state(db, user.tenant_id, tg_days), msgs, today,
                                     who="Бригадир" if user.role == "foreman" else "Руководитель")
        if msgs:
            db.commit()
            bg.add_task(tgclient.deliver, msgs)
    return _meta(db, user, p, today, True)


@router.post("/compare")
def compare(body: RunIn = RunIn(), user: User = Depends(require(*STAFF)), db: Session = DB,
            today: date = Depends(get_today)):
    scope = _scope(db, user, body)
    try:
        dbm.begin_snapshot(db)
        inp, _ = planning.build_input(db, user.tenant_id, today, _overrides(body, scope))
    except planning.NoActivePlan:
        raise HTTPException(409, "Сначала нужен общий план организации — его строит руководитель")
    return compare_strictness(inp)


@router.get("/active")
def active(user: User = Depends(require(*ALL)), db: Session = DB, today: date = Depends(get_today)):
    p = db.scalar(select(Plan).where(Plan.tenant_id == user.tenant_id, Plan.status == "active"))
    if not p:
        return {"plan": None}
    return {"plan": _meta(db, user, p, today, user.role in READERS)}


@router.get("")
def history(limit: int = 30, user: User = Depends(require(*STAFF)), db: Session = DB):
    ps = db.scalars(scoped(Plan, user).order_by(Plan.id.desc()).limit(min(max(limit, 1), 100)))
    return {"items": [{"id": p.id, "status": p.status, "created_at": p.created_at.isoformat(),
                       "created_by": p.created_by, "params": p.params, "warnings_count": len(p.warnings)} for p in ps]}


@router.post("/{pid}/activate")
def activate(pid: int, user: User = Depends(require(*MANAGERS)), db: Session = DB, today: date = Depends(get_today)):
    """Откат: сделать архивный план активным (BR-13). Данные не изменяются."""
    p = get_or_404(db, Plan, pid, user)
    if p.status != "active":
        cur = db.scalar(scoped(Plan, user).where(Plan.status == "active"))
        if cur:
            cur.status = "archived"
            db.flush()
        p.status = "active"
        # закрепления дней соответствуют активному плану: берём их из его закреплённых строк
        db.execute(delete(DayPin).where(DayPin.tenant_id == user.tenant_id, DayPin.plan_date >= today))
        for wid, d in {(i.worker_id, i.plan_date) for i in db.scalars(select(PlanItem).where(
                PlanItem.plan_id == p.id, PlanItem.is_locked.is_(True), PlanItem.plan_date >= today))}:
            db.add(DayPin(tenant_id=user.tenant_id, worker_id=wid, plan_date=d))
        snapshots.sync(db, user.tenant_id, p, today)
        audit.log(db, user, "plan", p.id, "activate")
    return {"id": p.id, "status": p.status}
