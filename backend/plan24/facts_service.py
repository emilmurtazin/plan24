"""Ввод факта: одни и те же правила для веб-интерфейса и Telegram (FR-21…FR-25, BR-13).

«Актор» — любой объект с атрибутами tenant_id, id, role, worker_id, work_center_id:
пользователь (User) или исполнитель, привязанный к Telegram без учётной записи (Actor).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import math

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import audit
from .deps import get_or_404, scoped
from .models import Fact, FactReason, Operation, OrderLine, Plan, PlanItem, PlannerSettings, Worker, utcnow
from fastapi import HTTPException


@dataclass
class Actor:
    """Исполнитель, работающий только через Telegram: без пароля и учётной записи (ТЗ FR-02)."""
    tenant_id: int
    worker_id: int
    id: Optional[int] = None
    role: str = "worker"
    work_center_id: Optional[int] = None


class FactError(HTTPException):
    """Ошибка бизнес-правил с готовым текстом для пользователя (веб — как есть, бот — в сообщении)."""


def _deny(code: int, msg: str):
    raise FactError(code, msg)


def can_touch_worker(actor, worker: Worker):
    """Сборщик — только он сам, бригадир — только свой участок."""
    if actor.role == "worker" and actor.worker_id != worker.id:
        _deny(403, "Можно вносить только свой факт")
    if actor.role == "foreman" and (actor.work_center_id is None or worker.work_center_id != actor.work_center_id):
        _deny(403, "Это исполнитель другого участка")


def _g(x: float) -> str:
    return f"{x:g}"


def deviation(db: Session, actor, *, fact_date: date, ol: OrderLine, op: Operation, worker: Worker, qty: float):
    """Что требует объяснения: «under» — меньше плана на день, «over» — больше, чем нужно по заказу. → (код, текст) или None."""
    total_other = db.scalar(select(func.coalesce(func.sum(Fact.qty), 0.0)).where(
        Fact.tenant_id == actor.tenant_id, Fact.order_line_id == ol.id, Fact.operation_id == op.id, Fact.deleted_at.is_(None),
        ~((Fact.fact_date == fact_date) & (Fact.worker_id == worker.id)))) or 0.0
    required = math.ceil(ol.qty * op.per_item - 1e-9)
    if total_other + qty > required + 1e-9:
        return "over", (f"Выполнено больше, чем нужно по заказу (нужно {_g(required)}, с этим фактом будет {_g(total_other + qty)}) — "
                        "укажите причину или комментарий")
    plan = db.scalar(select(Plan).where(Plan.tenant_id == actor.tenant_id, Plan.status == "active"))
    if plan:
        planned = db.scalar(select(func.coalesce(func.sum(PlanItem.qty), 0)).where(
            PlanItem.plan_id == plan.id, PlanItem.plan_date == fact_date, PlanItem.order_line_id == ol.id,
            PlanItem.operation_id == op.id, PlanItem.worker_id == worker.id)) or 0
        if planned and qty < planned:
            return "under", f"Сделано меньше плана ({_g(qty)} из {_g(planned)}) — укажите причину или комментарий"
    return None


def enter(db: Session, actor, *, fact_date: date, order_line_id: int, operation_id: int, worker_id: int, qty: float,
          today: date, reason_id: Optional[int] = None, note: str = "", source: str = "web"):
    """Записывает итог дня по исполнителю; повторный ввод заменяет значение. → (факт, прежнее значение или None)."""
    if fact_date > today:
        _deny(422, "Факт на будущую дату вносить нельзя")
    if actor.role == "worker" and fact_date != today:
        _deny(403, "Сборщик вносит факт только за сегодня. За прошлые дни — обратитесь к бригадиру")
    worker = get_or_404(db, Worker, worker_id, actor)
    can_touch_worker(actor, worker)
    ol = get_or_404(db, OrderLine, order_line_id, actor)
    if ol.status in ("draft", "cancelled"):
        _deny(422, "По этой позиции факт вносить нельзя (черновик или отменена)")
    op = get_or_404(db, Operation, operation_id, actor)
    if op.routing_id != ol.routing_id:
        _deny(422, "Операция не входит в маршрут этой позиции")
    if reason_id is not None:
        get_or_404(db, FactReason, reason_id, actor)

    if source == "web" and reason_id is None and not (note or "").strip():
        dev = deviation(db, actor, fact_date=fact_date, ol=ol, op=op, worker=worker, qty=qty)
        if dev:                                    # причину просим только при отклонении; в Telegram её спрашивает бот после записи
            _deny(422, dev[1])
    st = db.get(PlannerSettings, actor.tenant_id)
    needs_confirm = st.fact_confirmation and actor.role == "worker"
    key = (Fact.tenant_id == actor.tenant_id, Fact.fact_date == fact_date, Fact.order_line_id == ol.id,
           Fact.operation_id == op.id, Fact.worker_id == worker.id)
    f = db.scalar(select(Fact).where(*key))
    old = None
    if f is None:
        try:
            with db.begin_nested():                       # одновременный ввод того же итога: проигравший заменяет значение
                f = Fact(tenant_id=actor.tenant_id, fact_date=fact_date, order_line_id=ol.id, operation_id=op.id,
                         worker_id=worker.id, qty=qty)
                db.add(f)
                db.flush()
        except IntegrityError:
            f = db.scalar(select(Fact).where(*key))
            old = None if f.deleted_at else f.qty
            f.qty, f.deleted_at = qty, None
    else:
        old = None if f.deleted_at else f.qty
        f.qty, f.deleted_at = qty, None
    f.reason_id, f.note, f.entered_by, f.source = reason_id, note, actor.id, source
    f.status = "submitted" if needs_confirm else "confirmed"
    f.confirmed_by, f.confirmed_at = (None, None) if needs_confirm else (actor.id, utcnow())
    db.flush()
    audit.log(db, actor, "fact", f.id, "update" if old is not None else "create",
              {"qty": [old, qty], "worker_id": worker.id, "date": fact_date, "source": source})
    return f, old


def own_fact(db: Session, actor, fact_id: int) -> Fact:
    f = get_or_404(db, Fact, fact_id, actor)
    can_touch_worker(actor, get_or_404(db, Worker, f.worker_id, actor))
    return f


def confirm(db: Session, actor, fact: Fact) -> Fact:
    if fact.status != "confirmed":
        fact.status, fact.confirmed_by, fact.confirmed_at = "confirmed", actor.id, utcnow()
        audit.log(db, actor, "fact", fact.id, "confirm", {"source": "telegram" if fact.source == "telegram" else "web"})
    return fact


def remove(db: Session, actor, fact: Fact):
    fact.deleted_at = utcnow()
    audit.log(db, actor, "fact", fact.id, "delete", {"qty": fact.qty})


def set_reason(db: Session, actor, fact: Fact, reason_id: int):
    get_or_404(db, FactReason, reason_id, actor)
    fact.reason_id = reason_id
    audit.log(db, actor, "fact", fact.id, "reason", {"reason_id": reason_id})
