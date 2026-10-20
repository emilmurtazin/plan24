"""Ввод факта (FR-21…FR-25): итог дня по исполнителю, идемпотентность, подтверждение бригадиром."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import facts_service
from ..telegram import client as tgclient, events as tgevents
from ..db import DB
from ..deps import STAFF, MANAGERS, get_or_404, get_today, require, scoped
from ..models import (Fact, FactReason, IdempotencyKey, Operation, OrderLine, PlannerSettings, Routing, User, Worker,
                      utcnow)

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
            "operation_id": f.operation_id, "worker_id": f.worker_id, "qty": f.qty, "status": f.status,
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
        return q.where(Fact.worker_id.in_(ids))
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
