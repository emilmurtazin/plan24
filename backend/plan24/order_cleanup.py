"""Удалённые позиции заказов. Позиция удаляется «мягко» (deleted_at), но её следы не должны попадать в историю и показатели:
снимки плана (из них считается недовыполнение), строки планов и невнесённый факт. Окончательно позиции стираются, когда
удаляют изделие или проект, на которые они ссылались, — иначе ссылки не давали бы их удалить."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .models import (DayPlanSnapshot, ExtraTask, Fact, OperationAssignee, OrderLine, OrderLineOperation, PlanItem, WorkerOrderLine)


def dead_ids(db: Session, tenant_id: int) -> set:
    """Идентификаторы удалённых позиций заказов организации."""
    return set(db.scalars(select(OrderLine.id).where(OrderLine.tenant_id == tenant_id, OrderLine.deleted_at.is_not(None))))


def detach_plan(db: Session, tenant_id: int, order_line_id: int) -> None:
    """Удалённая позиция исчезает из снимков и строк планов: не считается недовыполнением и не выдаётся в заданиях."""
    db.execute(delete(DayPlanSnapshot).where(DayPlanSnapshot.tenant_id == tenant_id, DayPlanSnapshot.order_line_id == order_line_id))
    db.execute(delete(PlanItem).where(PlanItem.tenant_id == tenant_id, PlanItem.order_line_id == order_line_id))


def drop_operations(db: Session, tenant_id: int, op_ids: list) -> None:
    """Перед удалением операций: убрать то, что ссылается на них (строки планов, снимки, участки и исполнители позиций).
    Факты живых заказов сюда не доходят — удаление операции с фактом запрещено; факты удалённых заказов стираются."""
    if not op_ids:
        return
    for model in (PlanItem, DayPlanSnapshot, OrderLineOperation, OperationAssignee):
        db.execute(delete(model).where(model.tenant_id == tenant_id, model.operation_id.in_(op_ids)))
    dead = dead_ids(db, tenant_id)
    if dead:
        db.execute(delete(Fact).where(Fact.tenant_id == tenant_id, Fact.operation_id.in_(op_ids), Fact.order_line_id.in_(dead)))


def purge(db: Session, tenant_id: int, ids: list) -> None:
    """Стереть позиции и всё, что на них ссылается (факты, строки планов, снимки, распределение, допуски)."""
    if not ids:
        return
    for model in (Fact, PlanItem, DayPlanSnapshot, OrderLineOperation, WorkerOrderLine, OperationAssignee):
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


def transfer_facts(db: Session, tenant_id: int, from_op, to_op) -> dict:
    """Перед удалением операции: перенести внесённый по ней факт живых заказов на другую операцию той же версии маршрута.
    Если на целевой операции за тот же день, позицию и исполнителя (участок) уже есть факт — количества складываются.
    → {"moved": n, "merged": n, "qty": сумма, "fact_ids": [...]}. Удалённые («мягко») факты операции стираются."""
    live = select(OrderLine.id).where(OrderLine.tenant_id == tenant_id, OrderLine.deleted_at.is_(None))
    db.execute(delete(Fact).where(Fact.tenant_id == tenant_id, Fact.operation_id == from_op.id, Fact.deleted_at.is_not(None)))
    moved = merged = 0
    qty = 0.0
    ids: list = []
    for f in list(db.scalars(select(Fact).where(Fact.tenant_id == tenant_id, Fact.operation_id == from_op.id, Fact.order_line_id.in_(live))
                             .order_by(Fact.fact_date, Fact.id))):
        t = db.scalar(select(Fact).where(
            Fact.tenant_id == tenant_id, Fact.fact_date == f.fact_date, Fact.order_line_id == f.order_line_id, Fact.operation_id == to_op.id,
            Fact.worker_id.is_(None) if f.worker_id is None else Fact.worker_id == f.worker_id,
            Fact.work_center_id.is_(None) if f.work_center_id is None else Fact.work_center_id == f.work_center_id))
        qty += f.qty
        ids.append(f.id)
        if t is not None and t.deleted_at is not None:
            db.delete(t)
            db.flush()
            t = None
        if t is None:
            f.operation_id = to_op.id
            db.flush()
            moved += 1
            continue
        t.qty += f.qty
        mark = f"перенесено с «{from_op.name}»: {f.qty:g}"
        t.note = (f"{t.note}; {mark}" if t.note else mark)[:400]
        t.reason_id = t.reason_id or f.reason_id
        if f.status == "submitted":
            t.status, t.confirmed_by, t.confirmed_at = "submitted", None, None
        db.delete(f)
        db.flush()
        merged += 1
    return {"moved": moved, "merged": merged, "qty": qty, "fact_ids": ids[:200]}

