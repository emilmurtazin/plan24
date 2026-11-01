"""Единый расчёт доли выполненного заказа — для карточки заказа, списка «Статус заказов» и дашборда.

Доля считается по трудоёмкости: каждая операция весит свой объём в нормо-днях (требуется ÷ норма за день), выполненная часть
операции ограничена требуемым объёмом. Так выполненный быстрый «Раскрой» не выглядит как почти половина заказа, если
основная работа — сборка. Заказ показывает 100% только когда выполнены все операции целиком."""
from __future__ import annotations

import math
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import order_cleanup
from .models import Fact, Operation, OrderLine

EPS = 1e-9
DONE = "Готово"


def required(qty: float, op) -> int:
    return math.ceil(qty * op.per_item - EPS)


def weight(op, req: float) -> float:
    """Трудоёмкость операции в нормо-днях; без нормы — просто объём."""
    n = op.norm_per_day or 0
    return req / n if n > 0 else float(req)


def order_progress(qty: float, ops: list, done: dict) -> dict:
    """done: id операции → внесённый объём. → процент (0…100), нормо-дни (сделано/всего), штуки (сделано/всего), процент по операциям."""
    total = dn = items_done = items_req = 0.0
    per_op, complete = {}, True
    for op in ops:
        r = required(qty, op)
        if r <= 0:
            continue
        d = min(max(done.get(op.id, 0.0), 0.0), r)
        w = weight(op, r)
        total, dn = total + w, dn + w * d / r
        items_done, items_req = items_done + d, items_req + r
        per_op[op.id] = round(d / r * 100, 1)
        complete = complete and d >= r - EPS
    percent = 100.0 if complete and total > 0 else min(99.9, round(dn / total * 100, 1)) if total > EPS else 0.0
    return {"percent": percent, "done_work": dn, "total_work": total, "done": items_done, "required": items_req, "per_op": per_op}


def for_orders(db: Session, t: int, ol_ids: list) -> dict:
    """Доля выполнения по позициям заказов и итог по ним. per_order: id → процент."""
    empty = {"percent": 0.0, "done": 0.0, "required": 0.0, "done_work": 0.0, "total_work": 0.0, "per_order": {}}
    if not ol_ids:
        return empty
    ols = list(db.scalars(select(OrderLine).where(OrderLine.id.in_(ol_ids))))
    routing_ops: dict = defaultdict(list)
    for op in db.scalars(select(Operation).where(Operation.tenant_id == t)):
        routing_ops[op.routing_id].append(op)
    done: dict = defaultdict(dict)
    for ol, op, q in db.execute(select(Fact.order_line_id, Fact.operation_id, func.coalesce(func.sum(Fact.qty), 0.0))
                                .where(Fact.tenant_id == t, Fact.deleted_at.is_(None), Fact.order_line_id.in_(ol_ids))
                                .group_by(Fact.order_line_id, Fact.operation_id)):
        done[ol][op] = q
    per, tw, dw, di, ri = {}, 0.0, 0.0, 0.0, 0.0
    for o in ols:
        p = order_progress(o.qty, routing_ops.get(o.routing_id, []), done.get(o.id, {}))
        per[o.id] = p["percent"]
        tw, dw, di, ri = tw + p["total_work"], dw + p["done_work"], di + p["done"], ri + p["required"]
    pct = 100.0 if tw > 0 and dw >= tw - EPS else min(99.9, round(dw / tw * 100, 1)) if tw > EPS else 0.0
    return {"percent": pct, "done": di, "required": ri, "done_work": dw, "total_work": tw, "per_order": per}


def refresh_rows(db: Session, t: int, rows: list) -> tuple:
    """Строки статуса из плана устаревают по мере внесения фактов и удаления заказов. Здесь они приводятся к текущему состоянию:
    удалённые заказы убираются, добавляется свежий процент выполнения, а полностью выполненный заказ получает статус «Готово».
    → (строки, итог for_orders)."""
    dead = order_cleanup.dead_ids(db, t)
    rows = [{**r} for r in rows if r.get("order_line_id") not in dead]
    prog = for_orders(db, t, [r["order_line_id"] for r in rows if r.get("order_line_id")])
    for r in rows:
        r["progress"] = prog["per_order"].get(r.get("order_line_id"), 0.0)
        if r["progress"] >= 100.0 and r.get("status") not in (DONE, "Нет операций"):
            r["status"], r["forecast_text"] = DONE, "Выполнено"
    return rows, prog
