"""Ввод факта: одни и те же правила для веб-интерфейса и Telegram (FR-21…FR-25, BR-13).

«Актор» — любой объект с атрибутами tenant_id, id, role, worker_id, work_center_id:
пользователь (User) или исполнитель, привязанный к Telegram без учётной записи (Actor).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import audit
from .deps import get_or_404, scoped
from .models import Fact, FactReason, Operation, OrderLine, PlannerSettings, Worker, utcnow
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
