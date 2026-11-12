"""«Карта потока»: операции маршрута карточками на доске, связи-предшественники, выполнение по заказу.

Читают все, кроме сборщика; связи, положение карточек, добавление и удаление карточек правят те, кто правит каталог
(администратор, РП, технолог, бригадир). Карточка — это операция версии маршрута (изменение касается всех заказов на этой версии).
Связь «A → B» = A в списке предшественников B (`operations.predecessors`). У операции без списка (NULL, «по порядку») связь
показывается пунктиром: она следует из порядка. При правке связей такой список превращается в явный."""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, distribution, progress
from ..db import DB
from ..deps import CATALOG, READERS, get_or_404, require
from ..models import Fact, Operation, OrderLine, OrderLineOperation, Product, Project, Routing, User, WorkCenter

router = APIRouter(prefix="/flow-map", tags=["flow-map"])
LIMIT_LINES = 600


def _implicit_preds(op: Operation, ops: list) -> list:
    """Операция без списка предшественников идёт после всего предыдущего порядка — рисуем связь с ближайшей группой."""
    lower = [o for o in ops if o.sequence < op.sequence]
    if not lower:
        return []
    top = max(o.sequence for o in lower)
    return [o.name for o in lower if o.sequence == top]


def _effective(op: Operation, ops: list) -> tuple:
    """(имена предшественников, неявные ли они)."""
    if op.predecessors is None:
        return _implicit_preds(op, ops), True
    return list(op.predecessors), False


def _routing_ops(db: Session, user: User, rid: int) -> tuple:
    r = get_or_404(db, Routing, rid, user)
    return r, sorted(r.operations, key=lambda o: (o.sequence, o.id))


@router.get("/targets")
def targets(user: User = Depends(require(*READERS)), db: Session = DB):
    """Что можно открыть на карте: позиции заказов (с выполнением) и версии маршрутов изделий (структура без выполнения)."""
    t = user.tenant_id
    q = (select(OrderLine, Project.name, Product.name).join(Project, Project.id == OrderLine.project_id)
         .join(Product, Product.id == OrderLine.product_id)
         .where(OrderLine.tenant_id == t, OrderLine.deleted_at.is_(None), OrderLine.status.in_(("planned", "done")))
         .order_by(Project.name, OrderLine.id))
    if user.role == "foreman":
        q = q.where(OrderLine.id.in_(distribution.order_ids_of_center(db, user, user.work_center_id) or [0]))
    lines = [{"id": l.id, "project": pn, "order": l.ext_id or f"№{l.id}", "product": prn, "qty": l.qty, "routing_id": l.routing_id}
             for l, pn, prn in db.execute(q.limit(LIMIT_LINES))]
    prods = {p.id: p.name for p in db.scalars(select(Product).where(Product.tenant_id == t))}
    routings = [{"id": r.id, "product": prods.get(r.product_id, ""), "version_no": r.version_no, "name": r.name, "current": r.is_current}
                for r in db.scalars(select(Routing).where(Routing.tenant_id == t).order_by(Routing.product_id, Routing.version_no))]
    return {"lines": lines, "routings": sorted(routings, key=lambda r: (r["product"], -r["version_no"]))}


@router.get("")
def flow_map(order_line_id: Optional[int] = None, routing_id: Optional[int] = None, user: User = Depends(require(*READERS)), db: Session = DB):
    """Карточки и связи версии маршрута. С order_line_id — ещё и выполнение по этой позиции заказа."""
    if (order_line_id is None) == (routing_id is None):
        raise HTTPException(422, "Укажите позицию заказа или версию маршрута")
    t = user.tenant_id
    ol = None
    if order_line_id is not None:
        ol = get_or_404(db, OrderLine, order_line_id, user)
        if user.role == "foreman" and ol.id not in distribution.order_ids_of_center(db, user, user.work_center_id):
            raise HTTPException(404, "У этой позиции нет операций вашего участка")
        routing_id = ol.routing_id
    r, ops = _routing_ops(db, user, routing_id)
    product = db.get(Product, r.product_id)
    used = db.scalar(select(OrderLine.id).where(OrderLine.tenant_id == t, OrderLine.routing_id == r.id, OrderLine.deleted_at.is_(None)).limit(1)) is not None
    centers = {c.id: c.name for c in db.scalars(select(WorkCenter).where(WorkCenter.tenant_id == t))}
    assign: dict = defaultdict(list)
    done: dict = {}
    prog = None
    if ol is not None:
        for a in db.scalars(select(OrderLineOperation).where(OrderLineOperation.order_line_id == ol.id)):
            assign[a.operation_id].append(a.work_center_id)
        done = {op: q for op, q in db.execute(select(Fact.operation_id, func.sum(Fact.qty)).where(
            Fact.tenant_id == t, Fact.order_line_id == ol.id, Fact.deleted_at.is_(None)).group_by(Fact.operation_id))}
        prog = progress.order_progress(ol.qty, ops, done)
    by_name = {o.name: o for o in ops}
    nodes, edges = [], []
    for o in ops:
        names, implicit = _effective(o, ops)
        for n in names:
            if n in by_name:
                edges.append({"from": by_name[n].id, "to": o.id, "implicit": implicit})
        node = {"id": o.id, "name": o.name, "unit": o.unit, "sequence": o.sequence, "norm_per_day": o.norm_per_day, "per_item": o.per_item,
                "min_skill": o.min_skill, "start_after_pct": o.start_after_pct, "x": o.map_x, "y": o.map_y, "version": o.version,
                "predecessors": names, "implicit": implicit}
        if ol is not None:
            req = progress.required(ol.qty, o)
            own = [centers.get(c, "") for c in assign.get(o.id, [])]
            foreign = user.role == "foreman" and user.work_center_id not in assign.get(o.id, [])
            node.update({"required": req, "sections": own,
                         "foreign": foreign,                                       # бригадир не видит выполнение чужих участков
                         "done": None if foreign else min(done.get(o.id, 0.0), req) if req else 0,
                         "pct": None if foreign else (prog["per_op"].get(o.id, 0.0) if req else 100.0)})
        nodes.append(node)
    return {"routing": {"id": r.id, "product": product.name if product else "", "version_no": r.version_no, "name": r.name, "used": used},
            "line": ({"id": ol.id, "order": ol.ext_id or f"№{ol.id}", "qty": ol.qty, "percent": prog["percent"],
                      "due": ol.due_date.isoformat() if ol.due_date else None} if ol else None),
            "nodes": nodes, "edges": edges, "can_edit": user.role in CATALOG}


class LinkIn(BaseModel):
    from_id: int
    to_id: int


def _reachable(start: Operation, ops: list) -> set:
    """Операции, которые зависят от start (прямо или по цепочке)."""
    succ: dict = defaultdict(list)
    for o in ops:
        for n in _effective(o, ops)[0]:
            succ[n].append(o)
    seen, stack = set(), [start]
    while stack:
        x = stack.pop()
        for y in succ.get(x.name, []):
            if y.id not in seen:
                seen.add(y.id)
                stack.append(y)
    return seen


def _fix_order(ops: list) -> list:
    """Порядок (sequence) предшественника не должен быть больше порядка последователя: при нарушении последователь сдвигается."""
    by_name = {o.name: o for o in ops}
    moved: set = set()
    for _ in range(len(ops) + 2):
        changed = False
        for o in ops:
            for n in (o.predecessors or []):
                p = by_name.get(n)
                if p is not None and p.sequence > o.sequence:
                    o.sequence = p.sequence + 1
                    moved.add(o.id)
                    changed = True
        if not changed:
            break
    return sorted(moved)


@router.post("/{rid}/links")
def add_link(rid: int, body: LinkIn, user: User = Depends(require(*CATALOG)), db: Session = DB):
    """Связь «из → в»: «из» становится предшественником «в». Замкнутые круги запрещены; порядок операций при необходимости сдвигается."""
    r, ops = _routing_ops(db, user, rid)
    by_id = {o.id: o for o in ops}
    if body.from_id not in by_id or body.to_id not in by_id:
        raise HTTPException(404, "Операция не найдена в этой версии маршрута")
    a, b = by_id[body.from_id], by_id[body.to_id]
    if a.id == b.id:
        raise HTTPException(422, "Операция не может быть своим предшественником")
    cur, implicit = _effective(b, ops)
    if a.name in cur and not implicit:
        raise HTTPException(409, "Такая связь уже есть")
    if a.id in _reachable(b, ops):
        raise HTTPException(422, f"«{a.name}» уже зависит от «{b.name}»: так получится замкнутый круг")
    old = list(b.predecessors) if b.predecessors is not None else None
    b.predecessors = cur if a.name in cur else cur + [a.name]
    moved = _fix_order(ops)
    b.version += 1
    for o in ops:
        if o.id in moved and o.id != b.id:
            o.version += 1
    audit.log(db, user, "operation", b.id, "update", {"predecessors": [old, b.predecessors], "sequence_shifted": moved})
    return {"ok": True, "shifted": moved}


@router.delete("/{rid}/links", status_code=200)
def delete_link(rid: int, from_id: int, to_id: int, user: User = Depends(require(*CATALOG)), db: Session = DB):
    r, ops = _routing_ops(db, user, rid)
    by_id = {o.id: o for o in ops}
    if from_id not in by_id or to_id not in by_id:
        raise HTTPException(404, "Операция не найдена в этой версии маршрута")
    a, b = by_id[from_id], by_id[to_id]
    cur, _ = _effective(b, ops)
    if a.name not in cur:
        raise HTTPException(404, "Такой связи нет")
    old = list(b.predecessors) if b.predecessors is not None else None
    b.predecessors = [n for n in cur if n != a.name]            # пустой список — «без предшественников»: операция может начинаться сразу
    b.version += 1
    audit.log(db, user, "operation", b.id, "update", {"predecessors": [old, b.predecessors]})
    return {"ok": True}


class Pos(BaseModel):
    id: int
    x: float = Field(ge=-100000, le=100000)
    y: float = Field(ge=-100000, le=100000)


class LayoutIn(BaseModel):
    positions: list[Pos] = Field(max_length=300)


@router.put("/{rid}/layout")
def save_layout(rid: int, body: LayoutIn, user: User = Depends(require(*CATALOG)), db: Session = DB):
    """Положение карточек на доске. Не меняет версию операций и не мешает правкам каталога."""
    r, ops = _routing_ops(db, user, rid)
    by_id = {o.id: o for o in ops}
    for p in body.positions:
        o = by_id.get(p.id)
        if o is None:
            raise HTTPException(404, "Операция не найдена в этой версии маршрута")
        o.map_x, o.map_y = round(p.x, 1), round(p.y, 1)
    return {"ok": True, "saved": len(body.positions)}
