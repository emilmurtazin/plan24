"""Удалённые позиции заказов. Позиция удаляется «мягко» (deleted_at), но её следы не должны попадать в историю и показатели:
снимки плана (из них считается недовыполнение), строки планов и невнесённый факт. Окончательно позиции стираются, когда
удаляют изделие или проект, на которые они ссылались, — иначе ссылки не давали бы их удалить."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .models import (DayPlanSnapshot, ExtraTask, Fact, OrderLine, OrderLineOperation, PlanItem, WorkerOrderLine)


def dead_ids(db: Session, tenant_id: int) -> set:
    """Идентификаторы удалённых позиций заказов организации."""
    return set(db.scalars(select(OrderLine.id).where(OrderLine.tenant_id == tenant_id, OrderLine.deleted_at.is_not(None))))


def detach_plan(db: Session, tenant_id: int, order_line_id: int) -> None:
    """Удалённая позиция исчезает из снимков и строк планов: не считается недовыполнением и не выдаётся в заданиях."""
    db.execute(delete(DayPlanSnapshot).where(DayPlanSnapshot.tenant_id == tenant_id, DayPlanSnapshot.order_line_id == order_line_id))
    db.execute(delete(PlanItem).where(PlanItem.tenant_id == tenant_id, PlanItem.order_line_id == order_line_id))


def purge(db: Session, tenant_id: int, ids: list) -> None:
    """Стереть позиции и всё, что на них ссылается (факты, строки планов, снимки, распределение, допуски)."""
    if not ids:
        return
    for model in (Fact, PlanItem, DayPlanSnapshot, OrderLineOperation, WorkerOrderLine):
        db.execute(delete(model).where(model.tenant_id == tenant_id, model.order_line_id.in_(ids)))
    db.execute(update(ExtraTask).where(ExtraTask.tenant_id == tenant_id, ExtraTask.order_line_id.in_(ids)).values(order_line_id=None))
    db.execute(delete(OrderLine).where(OrderLine.tenant_id == tenant_id, OrderLine.id.in_(ids)))


def purge_dead_for(db: Session, tenant_id: int, column, value: int, what: str) -> None:
    """Перед удалением изделия/проекта: живые позиции мешают (409 с понятным текстом), удалённые — стираются."""
    rows = db.execute(select(OrderLine.id, OrderLine.deleted_at).where(OrderLine.tenant_id == tenant_id, column == value)).all()
    alive = [i for i, d in rows if d is None]
    if alive:
        raise HTTPException(409, f"Нельзя удалить {what}: по нему есть заказы ({len(alive)}). Сначала удалите эти заказы")
    purge(db, tenant_id, [i for i, _ in rows])
