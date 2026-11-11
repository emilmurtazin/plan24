"""Ввод факта (FR-21…FR-25): итог дня по исполнителю, идемпотентность, подтверждение бригадиром."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field
import math

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import facts_service
from ..telegram import client as tgclient, events as tgevents
from ..db import DB
from ..deps import READERS, STAFF, MANAGERS, get_or_404, get_today, require, scoped
from ..models import (DayPlanSnapshot, Fact, FactReason, IdempotencyKey, Operation, OrderLine, OrderLineOperation, PlannerSettings, Product, Project,
                      Routing, User, WorkCenter, Worker, utcnow)

router = APIRouter(prefix="/facts", tags=["facts"])
ENTER = ("owner", "manager", "foreman", "worker")


class FactIn(BaseModel):
    fact_date: date
    order_line_id: int
    operation_id: int
    worker_id: int
    qty: float = Field(ge=0, le=10_000_000)
    reason_id: Optional[int] = None
    note: str = Field("", max_length=400)


def out(f: Fact) -> dict:
    return {"id": f.id, "fact_date": f.fact_date.isoformat(), "order_line_id": f.order_line_id,
            "operation_id": f.operation_id, "worker_id": f.worker_id, "work_center_id": f.work_center_id, "qty": f.qty, "status": f.status,
            "reason_id": f.reason_id, "note": f.note, "source": f.source,
            "updated_at": f.updated_at.isoformat()}


@router.post("")
def enter_fact(body: FactIn, bg: BackgroundTasks, user: User = Depends(require(*ENTER)), db: Session = DB,
               today: date = Depends(get_today), idempotency_key: Optional[str] = Header(None)):
    if idempotency_key:
        k = db.get(IdempotencyKey, (user.tenant_id, idempotency_key))
        if k:
            return k.response                         # повтор запроса (мобильная сеть) не создаёт дубль
    f, old = facts_service.enter(db, user, fact_date=body.fact_date, order_line_id=body.order_line_id,
                                 operation_id=body.operation_id, worker_id=body.worker_id, qty=body.qty,
                                 today=today, reason_id=body.reason_id, note=body.note)
    if tgevents.enabled():                      # меньше плана — бригадир участка узнаёт сразу (в Telegram)
        msgs: list = []
        tgevents.alert_shortfall(db, f, msgs, skip_user_id=user.id if user.role == "foreman" else None)
        if msgs:
            bg.add_task(tgclient.deliver, msgs)
    resp = {**out(f), "replaced": old is not None, "previous_qty": old}
    if idempotency_key:
        try:
            with db.begin_nested():
                db.add(IdempotencyKey(tenant_id=user.tenant_id, key=idempotency_key, response=resp))
                db.flush()
        except IntegrityError:                           # тот же запрос пришёл параллельно: отдаём сохранённый ответ
            k = db.get(IdempotencyKey, (user.tenant_id, idempotency_key))
            if k:
                return k.response
    return resp


def _visible_filter(q, user: User, db: Session):
    if user.role == "worker":
        return q.where(Fact.worker_id == user.worker_id)
    if user.role == "foreman":
        ids = select(Worker.id).where(Worker.tenant_id == user.tenant_id, Worker.work_center_id == user.work_center_id)
        return q.where(or_(Fact.worker_id.in_(ids), and_(Fact.worker_id.is_(None), Fact.work_center_id == user.work_center_id)))
    return q


@router.get("")
def list_facts(date_from: Optional[date] = None, date_to: Optional[date] = None, worker_id: Optional[int] = None,
               order_line_id: Optional[int] = None, status: Optional[str] = None, limit: int = 500,
               user: User = Depends(require(*ENTER)), db: Session = DB):
    q = _visible_filter(scoped(Fact, user), user, db)
    if date_from:
        q = q.where(Fact.fact_date >= date_from)
    if date_to:
        q = q.where(Fact.fact_date <= date_to)
    if worker_id:
        q = q.where(Fact.worker_id == worker_id)
    if order_line_id:
        q = q.where(Fact.order_line_id == order_line_id)
    if status:
        q = q.where(Fact.status == status)
    return {"items": [out(f) for f in db.scalars(q.order_by(Fact.fact_date.desc(), Fact.id.desc()).limit(min(limit, 2000)))]}


@router.post("/{fid}/confirm")
def confirm(fid: int, user: User = Depends(require(*STAFF)), db: Session = DB):
    return out(facts_service.confirm(db, user, facts_service.own_fact(db, user, fid)))


@router.delete("/{fid}", status_code=204)
def delete_fact(fid: int, user: User = Depends(require(*STAFF)), db: Session = DB):
    facts_service.remove(db, user, facts_service.own_fact(db, user, fid))


# ------------------------------------------------------------------ факт «на заказ» (без исполнителя)
class OrderFactIn(BaseModel):
    fact_date: date
    order_line_id: int
    operation_id: int
    qty: float = Field(ge=0, le=10_000_000)
    work_center_id: Optional[int] = None          # участок; бригадиру подставляется его участок
    reason_id: Optional[int] = None
    note: str = Field("", max_length=400)


@router.post("/order")
def enter_order_fact(body: OrderFactIn, user: User = Depends(require(*STAFF)), db: Session = DB, today: date = Depends(get_today)):
    """Выполнено по операции заказа за день — без привязки к работникам. Вносят бригадир (свой участок), РП и администратор."""
    f, old = facts_service.enter_for_order(db, user, fact_date=body.fact_date, order_line_id=body.order_line_id, operation_id=body.operation_id,
                                           qty=body.qty, today=today, work_center_id=body.work_center_id, reason_id=body.reason_id, note=body.note)
    return {**out(f), "replaced": old is not None, "previous_qty": old}


@router.get("/order-options")
def order_options(user: User = Depends(require(*STAFF)), db: Session = DB, today: date = Depends(get_today)):
    """Позиции заказов и их операции для окна «Внести факт на заказ». Бригадиру — только операции его участка."""
    t = user.tenant_id
    lines = db.execute(select(OrderLine, Project.name, Product.name).join(Project, Project.id == OrderLine.project_id)
                       .join(Product, Product.id == OrderLine.product_id)
                       .where(OrderLine.tenant_id == t, OrderLine.deleted_at.is_(None), OrderLine.status.in_(("planned", "done")))
                       .order_by(Project.name, OrderLine.id).limit(600)).all()
    ids = [l.id for l, _, _ in lines]
    dist: dict = {}
    for ol, op, wc in db.execute(select(OrderLineOperation.order_line_id, OrderLineOperation.operation_id, OrderLineOperation.work_center_id)
                                 .where(OrderLineOperation.tenant_id == t, OrderLineOperation.order_line_id.in_(ids or [0]))):
        dist.setdefault((ol, op), []).append(wc)
    done = {(a, b): q for a, b, q in db.execute(select(Fact.order_line_id, Fact.operation_id, func.coalesce(func.sum(Fact.qty), 0.0)).where(
        Fact.tenant_id == t, Fact.deleted_at.is_(None), Fact.order_line_id.in_(ids or [0])).group_by(Fact.order_line_id, Fact.operation_id))}
    ops_by_routing: dict = {}
    for o in db.scalars(select(Operation).where(Operation.tenant_id == t, Operation.routing_id.in_({l.routing_id for l, _, _ in lines} or {0}))
                        .order_by(Operation.sequence, Operation.id)):
        ops_by_routing.setdefault(o.routing_id, []).append(o)
    items = []
    for l, pname, prname in lines:
        ops = []
        for o in ops_by_routing.get(l.routing_id, []):
            centers = dist.get((l.id, o.id), [])
            if user.role == "foreman" and user.work_center_id not in centers:
                continue
            ops.append({"id": o.id, "name": o.name, "unit": o.unit, "required": math.ceil(l.qty * o.per_item - 1e-9),
                        "done": done.get((l.id, o.id), 0.0), "centers": centers})
        if ops:
            items.append({"id": l.id, "project": pname, "order": l.ext_id or f"№{l.id}", "product": prname, "qty": l.qty,
                          "due": l.due_date.isoformat() if l.due_date else None, "operations": ops})
    return {"items": items, "today": today.isoformat(),
            "sections": [{"id": c.id, "name": c.name} for c in db.scalars(scoped(WorkCenter, user).order_by(WorkCenter.name))],
            "reasons": [{"id": r.id, "name": r.name, "work_center_id": r.work_center_id} for r in db.scalars(scoped(FactReason, user).order_by(FactReason.name))]}


# ------------------------------------------------------------------ единое окно контроля факта
@router.get("/journal")
def journal(date_from: Optional[date] = None, date_to: Optional[date] = None, work_center_id: Optional[int] = None,
            worker_id: Optional[int] = None, no_worker: bool = False, project_id: Optional[int] = None, order_line_id: Optional[int] = None,
            operation: str = "", status: Optional[str] = None, source: Optional[str] = None, deviations: bool = False,
            limit: int = 200, offset: int = 0, user: User = Depends(require(*READERS)), db: Session = DB, today: date = Depends(get_today)):
    """Все внесённые факты (работниками и «на заказ») одним списком: фильтры по датам, участку, работнику, проекту, заказу, операции,
    статусу и источнику; итоги по выбранному. Бригадир видит только свой участок."""
    section = func.coalesce(Worker.work_center_id, Fact.work_center_id)
    cond = [Fact.tenant_id == user.tenant_id, Fact.deleted_at.is_(None), OrderLine.deleted_at.is_(None),
            Fact.fact_date >= (date_from or today - timedelta(days=6)), Fact.fact_date <= (date_to or today)]
    if user.role == "foreman":
        cond.append(section == user.work_center_id)
    elif work_center_id is not None:
        cond.append(section == work_center_id)
    if no_worker:
        cond.append(Fact.worker_id.is_(None))
    elif worker_id is not None:
        cond.append(Fact.worker_id == worker_id)
    if project_id is not None:
        cond.append(OrderLine.project_id == project_id)
    if order_line_id is not None:
        cond.append(Fact.order_line_id == order_line_id)
    if operation.strip():
        needle = operation.strip().lower()                       # регистр кириллицы учитываем в Python: lower() в SQLite работает только для латиницы
        cond.append(Fact.operation_id.in_([i for i, n in db.execute(select(Operation.id, Operation.name).where(Operation.tenant_id == user.tenant_id))
                                           if needle in n.lower()] or [0]))
    if status in ("submitted", "confirmed"):
        cond.append(Fact.status == status)
    if source in ("web", "telegram", "import"):
        cond.append(Fact.source == source)
    if deviations:
        cond.append(or_(Fact.reason_id.is_not(None), Fact.note != ""))

    def joined(q):
        return (q.select_from(Fact).join(OrderLine, OrderLine.id == Fact.order_line_id).join(Operation, Operation.id == Fact.operation_id)
                .outerjoin(Worker, Worker.id == Fact.worker_id))

    tot = db.execute(joined(select(func.count(Fact.id), func.coalesce(func.sum(Fact.qty), 0.0),
                                   func.coalesce(func.sum(case((Fact.worker_id.is_(None), 1), else_=0)), 0),
                                   func.coalesce(func.sum(case((Fact.status == "submitted", 1), else_=0)), 0))).where(*cond)).one()
    rows = db.execute(joined(select(Fact, OrderLine, Operation, Worker, section.label("sec"))).where(*cond)
                      .order_by(Fact.fact_date.desc(), Fact.id.desc()).limit(max(1, min(limit, 500))).offset(max(0, offset))).all()
    t = user.tenant_id
    projects = {p.id: p.name for p in db.scalars(select(Project).where(Project.tenant_id == t))}
    products = {p.id: p.name for p in db.scalars(select(Product).where(Product.tenant_id == t))}
    centers = {c.id: c.name for c in db.scalars(select(WorkCenter).where(WorkCenter.tenant_id == t))}
    users = {u.id: u.name for u in db.scalars(select(User).where(User.tenant_id == t))}
    reasons = {r.id: r.name for r in db.scalars(select(FactReason).where(FactReason.tenant_id == t))}
    dates = {f.fact_date for f, *_ in rows}
    plans = {(d, a, b, w): q for d, a, b, w, q in db.execute(
        select(DayPlanSnapshot.plan_date, DayPlanSnapshot.order_line_id, DayPlanSnapshot.operation_id, DayPlanSnapshot.worker_id, DayPlanSnapshot.qty)
        .where(DayPlanSnapshot.tenant_id == t, DayPlanSnapshot.plan_date.in_(dates or {date.min})))}
    items = []
    for f, ol, op, w, sec in rows:
        items.append({"id": f.id, "date": f.fact_date.isoformat(), "project_id": ol.project_id, "project": projects.get(ol.project_id, ""),
                      "order_line_id": ol.id, "order": ol.ext_id or f"№{ol.id}", "product": products.get(ol.product_id, ""),
                      "operation_id": op.id, "operation": op.name, "unit": op.unit, "worker_id": f.worker_id, "worker": w.name if w else None,
                      "section_id": sec, "section": centers.get(sec, "—") if sec else "—", "qty": f.qty,
                      "plan_qty": plans.get((f.fact_date, f.order_line_id, f.operation_id, f.worker_id)) if f.worker_id else None,
                      "status": f.status, "reason": reasons.get(f.reason_id), "note": f.note, "source": f.source,
                      "entered_by": users.get(f.entered_by), "updated_at": f.updated_at.isoformat() + "Z"})
    return {"items": items, "total": tot[0], "date_from": (date_from or today - timedelta(days=6)).isoformat(), "date_to": (date_to or today).isoformat(), "summary": {"count": tot[0], "qty": tot[1], "unlinked": int(tot[2]), "submitted": int(tot[3])},
            "limit": max(1, min(limit, 500)), "offset": max(0, offset)}
