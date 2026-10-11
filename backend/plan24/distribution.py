"""Распределение операций позиции заказа по участкам (этап 3 доработок).

Операция позиции выполняется работниками назначенных ей участков. Операция без участка — ошибка планирования
(NOT_DISTRIBUTED), пока её не распределят. Участок (бригадир) видит только свои операции."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import Operation, OrderLine, OrderLineOperation, User, WorkCenter


def centers_of(db: Session, user: User) -> list:
    return list(db.scalars(select(WorkCenter).where(WorkCenter.tenant_id == user.tenant_id).order_by(WorkCenter.name)))


def auto_assign(db: Session, user: User, order_line_id: int, routing_id: int) -> None:
    """Если в организации ровно один участок, все операции позиции сразу идут на него: выбирать не из чего."""
    cs = centers_of(db, user)
    if len(cs) != 1:
        return
    for op_id in db.scalars(select(Operation.id).where(Operation.routing_id == routing_id)):
        db.add(OrderLineOperation(tenant_id=user.tenant_id, order_line_id=order_line_id, operation_id=op_id,
                                  work_center_id=cs[0].id))


def clear(db: Session, user: User, order_line_id: int) -> None:
    db.execute(delete(OrderLineOperation).where(OrderLineOperation.order_line_id == order_line_id,
                                                OrderLineOperation.tenant_id == user.tenant_id))


def order_ids_of_center(db: Session, user: User, center_id) -> set:
    """Позиции, у которых есть хотя бы одна операция участка."""
    return set(db.scalars(select(OrderLineOperation.order_line_id).where(
        OrderLineOperation.tenant_id == user.tenant_id, OrderLineOperation.work_center_id == center_id)))


def view(db: Session, user: User, ol: OrderLine) -> dict:
    """Операции позиции с назначенными участками. Бригадир видит только операции своего участка и только его."""
    ops = list(db.scalars(select(Operation).where(Operation.routing_id == ol.routing_id)
                          .order_by(Operation.sequence, Operation.id)))
    by_op: dict = defaultdict(list)
    for a in db.scalars(select(OrderLineOperation).where(OrderLineOperation.order_line_id == ol.id,
                                                         OrderLineOperation.tenant_id == user.tenant_id)):
        by_op[a.operation_id].append(a.work_center_id)
    own = user.work_center_id if user.role == "foreman" else None
    items = []
    for o in ops:
        cs = sorted(by_op.get(o.id, []))
        if own is not None:
            if own not in cs:
                continue
            cs = [own]
        items.append({"operation_id": o.id, "name": o.name, "sequence": o.sequence, "norm_per_day": o.norm_per_day,
                      "min_skill": o.min_skill, "per_item": o.per_item, "work_center_ids": cs, "distributed": bool(cs)})
    centers = [c for c in centers_of(db, user) if own is None or c.id == own]
    return {"order_line_id": ol.id, "routing_id": ol.routing_id, "centers": [{"id": c.id, "name": c.name} for c in centers],
            "items": items, "total": len(items), "distributed": sum(1 for i in items if i["distributed"])}


def summary(db: Session, user: User) -> list:
    """Сколько операций каждой позиции распределено (для таблицы заказов)."""
    total = dict(db.execute(select(Operation.routing_id, func.count()).where(Operation.tenant_id == user.tenant_id)
                            .group_by(Operation.routing_id)).all())
    dist = dict(db.execute(select(OrderLineOperation.order_line_id, func.count(func.distinct(OrderLineOperation.operation_id)))
                           .where(OrderLineOperation.tenant_id == user.tenant_id)
                           .group_by(OrderLineOperation.order_line_id)).all())
    lines = db.execute(select(OrderLine.id, OrderLine.routing_id).where(OrderLine.tenant_id == user.tenant_id,
                                                                       OrderLine.deleted_at.is_(None))).all()
    return [{"order_line_id": i, "total": total.get(r, 0), "distributed": dist.get(i, 0)} for i, r in lines]
