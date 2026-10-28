"""Табличное назначение работников на заказ: строки — операции заказа, столбцы — дни ближайшей недели, в ячейках — работники.

Назначение работника в ячейку создаёт строку плана (количество по умолчанию — норма операции с учётом производительности работника
и остатка потребности) и **сразу закрепляет день работника**: перепланирование эту строку не изменит. Всё остальное — те же правила,
проверки и оповещения в Telegram, что и у «Плана на день» (общие функции из dayedit).
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import audit, distribution, planning, snapshots
from ..db import DB
from ..deps import STAFF, get_or_404, get_today, require, scoped
from ..models import (DayPin, ExtraTask, Fact, Operation, OrderLine, OrderLineOperation, PlanItem, PlannerSettings, Product, Project,
                      User, WorkCenter, Worker, WorkerAbsence)
from . import dayedit as de

router = APIRouter(tags=["plan-grid"])
GRID_DAYS = 7
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _eff(db: Session, tenant_id: int) -> float:
    st = db.get(PlannerSettings, tenant_id)
    return (st.efficiency if st else 1.0) or 1.0


def _capacity(op: Operation, w: Worker, eff: float) -> float:
    """Сколько единиц операции работник делает за полный день."""
    return op.norm_per_day * w.perf * eff


def _centers_of(db: Session, ol_id: int, op_id: int) -> set:
    return set(db.scalars(select(OrderLineOperation.work_center_id).where(OrderLineOperation.order_line_id == ol_id,
                                                                        OrderLineOperation.operation_id == op_id)))


def _visible_ops(db: Session, user: User, ol: OrderLine) -> list:
    """Операции маршрута позиции, которые пользователь вправе назначать (бригадир — только своего участка)."""
    ops = list(db.scalars(select(Operation).where(Operation.routing_id == ol.routing_id).order_by(Operation.sequence, Operation.id)))
    if user.role == "foreman":
        ops = [o for o in ops if user.work_center_id in _centers_of(db, ol.id, o.id)]
    return ops


def _order_or_404(db: Session, user: User, order_line_id: int) -> OrderLine:
    ol = get_or_404(db, OrderLine, order_line_id, user)
    if user.role == "foreman" and ol.id not in distribution.order_ids_of_center(db, user, user.work_center_id):
        raise HTTPException(404, "У этой позиции нет операций вашего участка")
    return ol


def _label(db: Session, ol: OrderLine) -> dict:
    pr, pj = db.get(Product, ol.product_id), db.get(Project, ol.project_id)
    return {"order_line_id": ol.id, "order": ol.ext_id or f"#{ol.id}", "product": pr.name if pr else "", "project": pj.name if pj else "",
            "qty": ol.qty, "due_date": ol.due_date.isoformat() if ol.due_date else None}


# ------------------------------------------------------------------ какие заказы можно назначать
@router.get("/plan-grid/orders")
def grid_orders(user: User = Depends(require(*STAFF)), db: Session = DB):
    """Позиции в работе, по которым есть что назначать (бригадиру — только с операциями его участка)."""
    lines = [o for o in db.scalars(scoped(OrderLine, user).where(OrderLine.status == "planned", OrderLine.deleted_at.is_(None))
                                   .order_by(OrderLine.due_date, OrderLine.id))]
    if user.role == "foreman":
        mine = distribution.order_ids_of_center(db, user, user.work_center_id)
        lines = [o for o in lines if o.id in mine]
    return {"items": [_label(db, o) for o in lines]}


# ------------------------------------------------------------------ сетка
def _day_loads(db: Session, user: User, worker_ids: list, days: list, eff: float, plan_id: int) -> dict:
    """Загрузка работников по дням (доля рабочего дня) — по всем их строкам плана, а не только по этому заказу."""
    if not worker_ids:
        return {}
    ops = {o.id: o for o in db.scalars(select(Operation).where(Operation.tenant_id == user.tenant_id))}
    ws = {w.id: w for w in db.scalars(select(Worker).where(Worker.id.in_(worker_ids)))}
    load: dict = defaultdict(float)
    for it in db.scalars(select(PlanItem).where(PlanItem.plan_id == plan_id, PlanItem.worker_id.in_(worker_ids), PlanItem.plan_date.in_(days))):
        op, w = ops.get(it.operation_id), ws.get(it.worker_id)
        if op and w:
            cap = _capacity(op, w, eff)
            load[(it.worker_id, it.plan_date)] += it.qty / cap if cap else 0
    return load


@router.get("/plan-grid")
def grid(order_line_id: int, start: Optional[date] = None, user: User = Depends(require(*STAFF)), db: Session = DB,
         today: date = Depends(get_today)):
    ol = _order_or_404(db, user, order_line_id)
    if ol.status != "planned":
        raise HTTPException(422, "Позиция не в работе (черновик, отменена или завершена)")
    plan = de._plan(db, user)
    base = start or today
    start = base - timedelta(days=base.weekday())                   # неделя всегда с понедельника (прошедшие дни показываются, но не правятся)
    days = [start + timedelta(days=i) for i in range(GRID_DAYS)]
    eff = _eff(db, user.tenant_id)
    ops = _visible_ops(db, user, ol)
    has_centers = db.scalar(select(WorkCenter.id).where(WorkCenter.tenant_id == user.tenant_id).limit(1)) is not None
    centers = {c.id: c.name for c in db.scalars(select(WorkCenter).where(WorkCenter.tenant_id == user.tenant_id))}

    # работники, которых можно назначать (бригадир — свой участок)
    workers = [w for w in db.scalars(scoped(Worker, user).order_by(Worker.name)) if user.role != "foreman" or w.work_center_id == user.work_center_id]
    absent = defaultdict(set)
    for a in db.scalars(scoped(WorkerAbsence, user).where(WorkerAbsence.date_to >= days[0], WorkerAbsence.date_from <= days[-1])):
        for d in days:
            if a.date_from <= d <= a.date_to:
                absent[a.worker_id].add(d.isoformat())
    loads = _day_loads(db, user, [w.id for w in workers], days, eff, plan.id)

    # строки плана этого заказа в окне
    cells: dict = defaultdict(list)
    wname = {w.id: w for w in workers}
    for it in db.scalars(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.order_line_id == ol.id, PlanItem.plan_date.in_(days))
                         .order_by(PlanItem.id)):
        if it.worker_id not in wname and user.role == "foreman":
            continue                                           # чужие работники бригадиру не показываются
        w = wname.get(it.worker_id) or db.get(Worker, it.worker_id)
        op = next((o for o in ops if o.id == it.operation_id), None)
        if op is None:
            continue
        cells[(it.operation_id, it.plan_date.isoformat())].append(
            {"item_id": it.id, "worker_id": it.worker_id, "worker": w.name if w else "?", "qty": it.qty, "locked": it.is_locked,
             "capacity": round(_capacity(op, w, eff), 1) if w else None})

    out_ops = []
    for op in ops:
        required = math.ceil(ol.qty * op.per_item - 1e-9)
        done = db.scalar(select(func.coalesce(func.sum(Fact.qty), 0.0)).where(
            Fact.tenant_id == user.tenant_id, Fact.order_line_id == ol.id, Fact.operation_id == op.id, Fact.deleted_at.is_(None))) or 0
        planned = db.scalar(select(func.coalesce(func.sum(PlanItem.qty), 0)).where(
            PlanItem.plan_id == plan.id, PlanItem.order_line_id == ol.id, PlanItem.operation_id == op.id, PlanItem.plan_date >= today)) or 0
        cs = _centers_of(db, ol.id, op.id)
        eligible = [w.id for w in workers if (not has_centers) or w.work_center_id in cs]
        out_ops.append({"operation_id": op.id, "name": op.name, "sequence": op.sequence, "norm_per_day": op.norm_per_day, "per_item": op.per_item,
                        "min_skill": op.min_skill, "required": required, "done": done, "planned": planned,
                        "unplanned": max(0, required - done - planned),
                        "over": max(0, done + planned - required),            # запланировано больше, чем нужно
                        "complete": done + planned >= required,               # весь объём выполнен или полностью в плане
                        "sections": sorted(centers[c] for c in cs if c in centers),
                        "eligible": eligible, "distributed": bool(cs) or not has_centers,
                        "cells": {d.isoformat(): cells.get((op.id, d.isoformat()), []) for d in days}})
    return {"order": _label(db, ol), "start": start.isoformat(), "today": today.isoformat(), "efficiency": eff, "plan_id": plan.id,
            "days": [{"date": d.isoformat(), "wd": WD[d.weekday()], "workday": planning.is_work_day(db, user.tenant_id, d), "past": d < today} for d in days],
            "operations": out_ops,
            "workers": [{"id": w.id, "name": w.name, "skill": w.skill, "perf": w.perf, "status": w.status, "center": centers.get(w.work_center_id),
                         "absent": sorted(absent.get(w.id, [])), "load": {d.isoformat(): round(loads.get((w.id, d), 0.0), 3) for d in days}}
                        for w in workers]}


# ------------------------------------------------------------------ назначить / убрать
class AssignIn(BaseModel):
    order_line_id: int
    operation_id: int
    plan_date: date
    worker_id: int
    qty: Optional[int] = Field(None, ge=1, le=100000)          # по умолчанию — норма


@router.post("/plan-grid/assign", status_code=201)
def assign(body: AssignIn, bg: BackgroundTasks, user: User = Depends(require(*STAFF)), db: Session = DB, today: date = Depends(get_today)):
    """Назначить работника на операцию в день. Количество по умолчанию — норма: норма операции × производительность × эффективность,
    не больше невыполненного остатка по заказу и не больше свободной части дня работника. День работника закрепляется."""
    plan = de._plan(db, user)
    de._future(body.plan_date, today)
    w = de._worker(db, user, body.worker_id)
    ol = _order_or_404(db, user, body.order_line_id)
    op = get_or_404(db, Operation, body.operation_id, user)
    warns = de._check_task(db, user, w, ol, op)
    eff = _eff(db, user.tenant_id)
    cap = _capacity(op, w, eff)
    required = math.ceil(ol.qty * op.per_item - 1e-9)
    done = db.scalar(select(func.coalesce(func.sum(Fact.qty), 0.0)).where(
        Fact.tenant_id == user.tenant_id, Fact.order_line_id == ol.id, Fact.operation_id == op.id, Fact.deleted_at.is_(None))) or 0
    planned = db.scalar(select(func.coalesce(func.sum(PlanItem.qty), 0)).where(
        PlanItem.plan_id == plan.id, PlanItem.order_line_id == ol.id, PlanItem.operation_id == op.id)) or 0
    load_before = _day_loads(db, user, [w.id], [body.plan_date], eff, plan.id).get((w.id, body.plan_date), 0.0)
    existing = db.scalar(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.plan_date == body.plan_date,
                                                PlanItem.order_line_id == ol.id, PlanItem.operation_id == op.id,
                                                PlanItem.worker_id == w.id))
    if body.qty is not None:
        qty = body.qty
    else:
        qty = max(1, round(cap))                                           # норма
        unplanned = required - done - planned
        if unplanned >= 1:
            qty = min(qty, int(unplanned))                                 # не больше остатка потребности
        free_qty = math.floor(max(0.0, 1 - load_before) * cap + 1e-9)
        if free_qty >= 1:
            qty = min(qty, free_qty)                                       # не больше свободной части дня
        qty = max(1, qty)
    if db.scalar(select(WorkerAbsence.id).where(WorkerAbsence.tenant_id == user.tenant_id, WorkerAbsence.worker_id == w.id,
                                                WorkerAbsence.date_from <= body.plan_date, WorkerAbsence.date_to >= body.plan_date).limit(1)):
        warns.append("У работника в этот день отпуск или отсутствие")
    tg = de._tg_before(db, user, [body.plan_date], [w.id])
    if existing is not None:
        existing.qty = qty
        existing.reason_code, existing.is_frozen = "MANUAL", False
        it = existing
    else:
        it = de._upsert(db, user, plan, body.plan_date, ol.id, op.id, w.id, qty)
    db.flush()
    de.pin_day(db, user, plan, w.id, body.plan_date)                       # назначил — день закреплён
    db.flush()
    after = _day_loads(db, user, [w.id], [body.plan_date], eff, plan.id).get((w.id, body.plan_date), 0.0)
    if after > 1.0 + 1e-9:
        warns.append(f"Перегрузка: у работника в этот день {round(after * 100)}% рабочего дня")
    if not planning.is_work_day(db, user.tenant_id, body.plan_date):
        warns.append("Нерабочий день: планировщик в такие дни сам никого не загружает, назначение остаётся как вы его задали")
    warns += de._over_need(db, user, plan, ol, op)
    audit.log(db, user, "plan_item", it.id, "manual_add", {"date": body.plan_date, "worker_id": w.id, "qty": qty, "source": "plan_grid"})
    snapshots.sync(db, user.tenant_id, plan, today)
    de._tg_after(db, user, tg, [body.plan_date], [w.id], today, bg)
    return {"item": de._item_out(it), "warnings": warns, "capacity": round(cap, 1), "load": round(after, 3), "default_qty": qty}


@router.delete("/plan-grid/assign/{iid}")
def unassign(iid: int, bg: BackgroundTasks, user: User = Depends(require(*STAFF)), db: Session = DB, today: date = Depends(get_today)):
    """Убрать назначение. Если у работника в этот день больше ничего нет (ни строк плана, ни работ вне перечня), закрепление дня снимается."""
    plan = de._plan(db, user)
    it = db.scalar(select(PlanItem).where(PlanItem.id == iid, PlanItem.plan_id == plan.id, PlanItem.tenant_id == user.tenant_id))
    if it is None:
        raise HTTPException(404, "Не найдено")
    de._worker(db, user, it.worker_id)
    de._future(it.plan_date, today)
    wid, d = it.worker_id, it.plan_date
    tg = de._tg_before(db, user, [d], [wid])
    audit.log(db, user, "plan_item", it.id, "manual_delete", {"date": d, "worker_id": wid, "source": "plan_grid"})
    db.delete(it)
    db.flush()
    left = db.scalar(select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.worker_id == wid, PlanItem.plan_date == d))
    extras = db.scalar(select(func.count()).select_from(ExtraTask).where(ExtraTask.tenant_id == user.tenant_id, ExtraTask.worker_id == wid,
                                                                         ExtraTask.plan_date == d))
    unpinned = False
    if not left and not extras and de.day_pinned(db, user, wid, d):
        db.execute(delete(DayPin).where(DayPin.tenant_id == user.tenant_id, DayPin.worker_id == wid, DayPin.plan_date == d))
        unpinned = True
    snapshots.sync(db, user.tenant_id, plan, today)
    de._tg_after(db, user, tg, [d], [wid], today, bg)
    return {"removed": True, "unpinned": unpinned}
