"""Правка «Плана на день» (этап 4 доработок).

Ручные правки меняют строки активного плана, но день работника сами не закрепляют: закрепление — отдельное явное
действие (POST /plan-day/pins). Закреплённый день (DayPin) перепланирование участка не меняет; незакреплённый — пересчитывается.
Работы вне перечня операций — отдельная сущность ExtraTask (по заказу или не по заказу), учёт в часах."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import audit, facts_service, planning, snapshots
from ..telegram import client as tgclient, events as tgevents
from ..db import DB
from ..deps import STAFF, get_or_404, get_today, require, scoped
from ..models import (DayPin, ExtraTask, FactReason, Operation, OrderLine, OrderLineOperation, Plan, PlanItem,
                      PlannerSettings, User, WorkCenter, Worker, MAX_QUAL_LEVELS)

router = APIRouter(tags=["day-edit"])
ENTER = ("owner", "manager", "foreman", "worker")


# ------------------------------------------------------------------ общее
def _plan(db: Session, user: User) -> Plan:
    p = db.scalar(scoped(Plan, user).where(Plan.status == "active"))
    if p is None:
        raise HTTPException(409, "План ещё не построен: сначала выполните перепланирование")
    return p


def _future(d: date, today: date):
    if d < today:
        raise HTTPException(422, "Прошедший день изменить нельзя — за него вносится только факт")


def _worker(db: Session, user: User, wid: int) -> Worker:
    w = get_or_404(db, Worker, wid, user)
    facts_service.can_touch_worker(user, w)           # бригадир — только работники своего участка
    return w


def pin_day(db: Session, user: User, plan: Plan, worker_id: int, d: date) -> None:
    """Закрепить день работника: строки этого дня остаются как есть при перепланировании."""
    if db.get(DayPin, (worker_id, d)) is None:
        db.add(DayPin(tenant_id=user.tenant_id, worker_id=worker_id, plan_date=d))
    for it in db.scalars(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.worker_id == worker_id,
                                                PlanItem.plan_date == d)):
        it.is_locked = True


def day_pinned(db: Session, user: User, worker_id: int, d: date) -> bool:
    return db.scalar(select(DayPin.worker_id).where(DayPin.tenant_id == user.tenant_id, DayPin.worker_id == worker_id,
                                                    DayPin.plan_date == d)) is not None


def _tg_before(db: Session, user: User, days: list, worker_ids: list):
    """Состояние плана работников до правки — чтобы потом сообщить им в Telegram, что изменилось (только если бот включён)."""
    return tgevents.plan_state(db, user.tenant_id, days, worker_ids) if tgevents.enabled() else None


def _tg_after(db: Session, user: User, before, days: list, worker_ids: list, today: date, bg: BackgroundTasks):
    if before is None:
        return
    out: list = []
    tgevents.notify_plan_changes(db, user.tenant_id, before, tgevents.plan_state(db, user.tenant_id, days, worker_ids), out, today, who="Руководитель"
                                 if user.role != "foreman" else "Бригадир")
    if out:
        bg.add_task(tgclient.deliver, out)


def _offday_pin(db: Session, user: User, plan, worker_id: int, d: date) -> bool:
    """Работа в нерабочий день (выходной, праздник): планировщик в такие дни сам ничего не ставит, поэтому добавленное вручную
    нужно закрепить, иначе перепланирование его бы убрало. Такой день работника закрепляется сразу. → True, если закрепили."""
    if planning.is_work_day(db, user.tenant_id, d):
        return False
    pin_day(db, user, plan, worker_id, d)
    return True


def _check_task(db: Session, user: User, w: Worker, ol: OrderLine, op: Operation) -> list:
    """Проверки назначения операции работнику. Возвращает мягкие предупреждения; жёсткие — ошибкой."""
    if ol.status != "planned":
        raise HTTPException(422, "Позиция не в работе (черновик, отменена или завершена)")
    if op.routing_id != ol.routing_id:
        raise HTTPException(422, "Операция не входит в маршрут этой позиции")
    has_centers = db.scalar(select(WorkCenter.id).where(WorkCenter.tenant_id == user.tenant_id).limit(1)) is not None
    if has_centers:
        cs = set(db.scalars(select(OrderLineOperation.work_center_id).where(
            OrderLineOperation.order_line_id == ol.id, OrderLineOperation.operation_id == op.id)))
        if not cs:
            raise HTTPException(422, "Операция не распределена по участкам — сначала распределите её в «Проекты и Заказы → Заказы»")
        if w.work_center_id not in cs:
            raise HTTPException(422, "Эта операция назначена другим участкам, а не участку работника")
    warns = []
    if w.skill < op.min_skill:
        warns.append("Квалификация работника ниже требуемой для операции")
    if w.status != "available":
        warns.append("Работник помечен как недоступный")
    return warns


def _over_need(db: Session, user: User, plan: Plan, ol: OrderLine, op: Operation) -> list:
    """Мягкое предупреждение: вместе с ручной правкой по операции запланировано больше, чем нужно по заказу."""
    import math
    from sqlalchemy import func
    from ..models import Fact
    required = math.ceil(ol.qty * op.per_item - 1e-9)
    planned = db.scalar(select(func.coalesce(func.sum(PlanItem.qty), 0)).where(
        PlanItem.plan_id == plan.id, PlanItem.order_line_id == ol.id, PlanItem.operation_id == op.id)) or 0
    done = db.scalar(select(func.coalesce(func.sum(Fact.qty), 0.0)).where(
        Fact.tenant_id == user.tenant_id, Fact.order_line_id == ol.id, Fact.operation_id == op.id, Fact.deleted_at.is_(None))) or 0
    if planned + done > required + 1e-9:
        return [f"По операции «{op.name}» запланировано больше, чем нужно по заказу: нужно {required:g}, "
                f"уже сделано {done:g}, в плане {planned:g}. Лишнее можно убрать вручную"]
    return []


def _item_out(it: PlanItem) -> dict:
    return {"id": it.id, "plan_date": it.plan_date.isoformat(), "order_line_id": it.order_line_id,
            "operation_id": it.operation_id, "worker_id": it.worker_id, "qty": it.qty, "is_locked": it.is_locked,
            "reason_code": it.reason_code}


def _upsert(db: Session, user: User, plan: Plan, d: date, ol_id: int, op_id: int, wid: int, qty: int) -> PlanItem:
    it = db.scalar(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.plan_date == d,
                                          PlanItem.order_line_id == ol_id, PlanItem.operation_id == op_id,
                                          PlanItem.worker_id == wid))
    if it is None:
        it = PlanItem(tenant_id=user.tenant_id, plan_id=plan.id, plan_date=d, order_line_id=ol_id, operation_id=op_id,
                      worker_id=wid, qty=0)
        db.add(it)
    it.qty += qty
    it.is_locked, it.is_frozen, it.reason_code = day_pinned(db, user, wid, d), False, "MANUAL"    # блокируется только в закреплённом дне
    return it


# ------------------------------------------------------------------ строки плана
class ItemIn(BaseModel):
    plan_date: date
    worker_id: int
    order_line_id: int
    operation_id: int
    qty: int = Field(ge=1, le=1_000_000)


class ItemPatch(BaseModel):
    plan_date: Optional[date] = None
    worker_id: Optional[int] = None
    order_line_id: Optional[int] = None
    operation_id: Optional[int] = None
    qty: Optional[int] = Field(None, ge=1, le=1_000_000)


@router.post("/plan-day/items", status_code=201)
def add_item(body: ItemIn, bg: BackgroundTasks, user: User = Depends(require(*STAFF)), db: Session = DB, today: date = Depends(get_today)):
    """Добавить задание на день (можно несколько операций одному работнику). День работника не закрепляется сам."""
    plan = _plan(db, user)
    _future(body.plan_date, today)
    w = _worker(db, user, body.worker_id)
    ol = get_or_404(db, OrderLine, body.order_line_id, user)
    op = get_or_404(db, Operation, body.operation_id, user)
    warns = _check_task(db, user, w, ol, op)
    tg = _tg_before(db, user, [body.plan_date], [w.id])
    it = _upsert(db, user, plan, body.plan_date, ol.id, op.id, w.id, body.qty)
    db.flush()
    auto_pinned = _offday_pin(db, user, plan, w.id, body.plan_date)
    audit.log(db, user, "plan_item", it.id, "manual_add", {"date": body.plan_date, "worker_id": w.id, "order_line_id": ol.id,
                                                          "operation_id": op.id, "qty": body.qty})
    snapshots.sync(db, user.tenant_id, plan, today)
    _tg_after(db, user, tg, [body.plan_date], [w.id], today, bg)
    return {"item": _item_out(it), "warnings": warns + _over_need(db, user, plan, ol, op), "auto_pinned": auto_pinned}


@router.patch("/plan-day/items/{iid}")
def patch_item(iid: int, body: ItemPatch, bg: BackgroundTasks, user: User = Depends(require(*STAFF)), db: Session = DB,
               today: date = Depends(get_today)):
    """Изменить задание: количество, работника, заказ (а с ним изделие и проект), операцию или день."""
    plan = _plan(db, user)
    it = db.scalar(scoped(PlanItem, user).where(PlanItem.id == iid, PlanItem.plan_id == plan.id))
    if it is None:
        raise HTTPException(404, "Строка плана не найдена (возможно, план был пересчитан)")
    _future(it.plan_date, today)
    old = (it.plan_date, it.worker_id, it.order_line_id, it.operation_id, it.qty)
    d, wid = body.plan_date or it.plan_date, body.worker_id or it.worker_id
    olid, opid = body.order_line_id or it.order_line_id, body.operation_id or it.operation_id
    qty = body.qty if body.qty is not None else it.qty
    _future(d, today)
    _worker(db, user, it.worker_id)
    w = _worker(db, user, wid)
    ol = get_or_404(db, OrderLine, olid, user)
    op = get_or_404(db, Operation, opid, user)
    warns = _check_task(db, user, w, ol, op)
    tg_days, tg_workers = [old[0], d], [old[1], wid]
    tg = _tg_before(db, user, tg_days, tg_workers)
    if (d, wid, olid, opid) == old[:4]:                       # тот же ключ: меняется только количество
        it.qty, it.is_locked, it.reason_code, it.is_frozen = qty, day_pinned(db, user, wid, d), "MANUAL", False
        res = it
    else:
        db.delete(it)
        db.flush()
        res = _upsert(db, user, plan, d, olid, opid, wid, qty)
    db.flush()
    auto_pinned = _offday_pin(db, user, plan, wid, d)
    audit.log(db, user, "plan_item", res.id, "manual_edit", {"from": [x.isoformat() if isinstance(x, date) else x for x in old],
                                                            "to": [d.isoformat(), wid, olid, opid, qty]})
    snapshots.sync(db, user.tenant_id, plan, today)
    _tg_after(db, user, tg, tg_days, tg_workers, today, bg)
    return {"item": _item_out(res), "warnings": warns + _over_need(db, user, plan, ol, op), "auto_pinned": auto_pinned}


@router.delete("/plan-day/items/{iid}", status_code=204)
def delete_item(iid: int, bg: BackgroundTasks, user: User = Depends(require(*STAFF)), db: Session = DB, today: date = Depends(get_today)):
    """Убрать задание. Чтобы перепланирование не вернуло его обратно, день работника нужно закрепить вручную."""
    plan = _plan(db, user)
    it = db.scalar(scoped(PlanItem, user).where(PlanItem.id == iid, PlanItem.plan_id == plan.id))
    if it is None:
        raise HTTPException(404, "Строка плана не найдена (возможно, план был пересчитан)")
    _future(it.plan_date, today)
    _worker(db, user, it.worker_id)
    wid, d = it.worker_id, it.plan_date
    tg = _tg_before(db, user, [d], [wid])
    audit.log(db, user, "plan_item", it.id, "manual_delete", {"date": d, "worker_id": wid, "order_line_id": it.order_line_id,
                                                              "operation_id": it.operation_id, "qty": it.qty})
    db.delete(it)
    db.flush()
    snapshots.sync(db, user.tenant_id, plan, today)
    _tg_after(db, user, tg, [d], [wid], today, bg)


class PinIn(BaseModel):
    worker_id: int
    plan_date: date


@router.post("/plan-day/pins", status_code=201)
def pin(body: PinIn, user: User = Depends(require(*STAFF)), db: Session = DB, today: date = Depends(get_today)):
    """Закрепить день работника вручную: задания этого дня перепланирование не меняет (и не добавляет новых)."""
    plan = _plan(db, user)
    _future(body.plan_date, today)
    w = _worker(db, user, body.worker_id)
    pin_day(db, user, plan, w.id, body.plan_date)
    audit.log(db, user, "day_pin", w.id, "pin", {"date": body.plan_date})
    return {"worker_id": w.id, "plan_date": body.plan_date.isoformat(), "pinned": True}


@router.delete("/plan-day/pins", status_code=204)
def unpin(worker_id: int, plan_date: date, user: User = Depends(require(*STAFF)), db: Session = DB,
          today: date = Depends(get_today)):
    """Снять закрепление дня работника: при следующем перепланировании день снова считается автоматически."""
    _future(plan_date, today)
    _worker(db, user, worker_id)
    if db.scalar(select(ExtraTask.id).where(ExtraTask.tenant_id == user.tenant_id, ExtraTask.worker_id == worker_id,
                                            ExtraTask.plan_date == plan_date).limit(1)):
        raise HTTPException(409, "На этот день есть работы вне перечня — сначала удалите их, иначе планировщик их не учтёт")
    db.execute(delete(DayPin).where(DayPin.worker_id == worker_id, DayPin.plan_date == plan_date, DayPin.tenant_id == user.tenant_id))
    plan = db.scalar(scoped(Plan, user).where(Plan.status == "active"))
    if plan:
        for it in db.scalars(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.worker_id == worker_id,
                                                    PlanItem.plan_date == plan_date)):
            it.is_locked = False
    audit.log(db, user, "day_pin", worker_id, "unpin", {"date": plan_date})


# ------------------------------------------------------------------ временный работник
class TempWorkerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    work_center_id: Optional[int] = None
    perf: float = Field(1.0, gt=0, le=10)
    skill: float = Field(1, gt=0, le=10)


@router.post("/plan-day/workers", status_code=201)
def add_temp_worker(body: TempWorkerIn, user: User = Depends(require(*STAFF)), db: Session = DB):
    """Новый (например, временный) работник. Попадает в общий список работников, но автопланировщик заказов ему не даёт,
    пока это не включено в «Работники → Работники» (поле «Участвует в автопланировании»)."""
    cid = body.work_center_id
    if user.role == "foreman":
        if cid not in (None, user.work_center_id):
            raise HTTPException(403, "Бригадир добавляет работников только на свой участок")
        cid = user.work_center_id
    if cid is None:
        raise HTTPException(422, "Выберите участок работника")
    get_or_404(db, WorkCenter, cid, user)
    st = db.get(PlannerSettings, user.tenant_id)
    n = len(st.qualifications or []) if st else MAX_QUAL_LEVELS
    if body.skill > n:
        raise HTTPException(422, f"Квалификация работника: в организации {n} ур. квалификации")
    name = body.name.strip()
    if db.scalar(select(Worker.id).where(Worker.tenant_id == user.tenant_id, Worker.name == name)):
        raise HTTPException(409, "Работник с таким именем уже есть")
    w = Worker(tenant_id=user.tenant_id, name=name, work_center_id=cid, perf=body.perf, skill=body.skill,
               status="available", auto_plan=False, is_temporary=True)
    db.add(w)
    db.flush()
    audit.log(db, user, "worker", w.id, "create", {"name": name, "temporary": True, "work_center_id": cid})
    return {"id": w.id, "name": w.name, "work_center_id": w.work_center_id, "perf": w.perf, "skill": w.skill,
            "status": w.status, "auto_plan": w.auto_plan, "is_temporary": w.is_temporary}


# ------------------------------------------------------------------ работы вне перечня операций
class ExtraIn(BaseModel):
    plan_date: date
    worker_id: int
    order_line_id: Optional[int] = None          # None — работа не относится к заказу
    title: str = Field(min_length=1, max_length=200)
    hours: float = Field(gt=0, le=24)


class ExtraPatch(BaseModel):
    plan_date: Optional[date] = None
    worker_id: Optional[int] = None
    order_line_id: Optional[int] = None
    detach_order: bool = False                   # true — отвязать от заказа
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    hours: Optional[float] = Field(None, gt=0, le=24)


class ExtraFact(BaseModel):
    fact_hours: float = Field(ge=0, le=24)
    reason_id: Optional[int] = None
    note: str = Field("", max_length=400)


def extra_out(x: ExtraTask) -> dict:
    return {"id": x.id, "plan_date": x.plan_date.isoformat(), "worker_id": x.worker_id, "order_line_id": x.order_line_id,
            "title": x.title, "hours": x.hours, "fact_hours": x.fact_hours, "reason_id": x.reason_id, "note": x.note}


def _order_ok(db: Session, user: User, order_line_id: Optional[int]):
    if order_line_id is not None:
        ol = get_or_404(db, OrderLine, order_line_id, user)
        if ol.status == "cancelled" or ol.deleted_at is not None:
            raise HTTPException(422, "Позиция отменена")


@router.post("/extra-tasks", status_code=201)
def add_extra(body: ExtraIn, user: User = Depends(require(*STAFF)), db: Session = DB, today: date = Depends(get_today)):
    plan = _plan(db, user)
    _future(body.plan_date, today)
    w = _worker(db, user, body.worker_id)
    _order_ok(db, user, body.order_line_id)
    x = ExtraTask(tenant_id=user.tenant_id, plan_date=body.plan_date, worker_id=w.id, order_line_id=body.order_line_id,
                  title=body.title.strip(), hours=body.hours, created_by=user.id)
    db.add(x)
    db.flush()
    audit.log(db, user, "extra_task", x.id, "create", {"date": body.plan_date, "worker_id": w.id, "title": x.title, "hours": x.hours,
                                                      "order_line_id": x.order_line_id})
    return extra_out(x)


@router.patch("/extra-tasks/{xid}")
def patch_extra(xid: int, body: ExtraPatch, user: User = Depends(require(*STAFF)), db: Session = DB,
                today: date = Depends(get_today)):
    x = get_or_404(db, ExtraTask, xid, user)
    _future(x.plan_date, today)
    _worker(db, user, x.worker_id)
    if body.worker_id is not None:
        _worker(db, user, body.worker_id)
    if body.plan_date is not None:
        _future(body.plan_date, today)
    old = (x.plan_date, x.worker_id)
    if body.detach_order:
        x.order_line_id = None
    elif body.order_line_id is not None:
        _order_ok(db, user, body.order_line_id)
        x.order_line_id = body.order_line_id
    for k in ("plan_date", "worker_id", "title", "hours"):
        v = getattr(body, k)
        if v is not None:
            setattr(x, k, v.strip() if k == "title" else v)
    _plan(db, user)
    db.flush()
    audit.log(db, user, "extra_task", x.id, "update", {"title": x.title, "hours": x.hours})
    return extra_out(x)


@router.delete("/extra-tasks/{xid}", status_code=204)
def delete_extra(xid: int, user: User = Depends(require(*STAFF)), db: Session = DB, today: date = Depends(get_today)):
    x = get_or_404(db, ExtraTask, xid, user)
    _future(x.plan_date, today)
    _worker(db, user, x.worker_id)
    if x.fact_hours is not None:
        raise HTTPException(409, "По этой работе уже внесён факт — удалить её нельзя")
    audit.log(db, user, "extra_task", x.id, "delete", {"title": x.title, "date": x.plan_date})
    db.delete(x)


@router.put("/extra-tasks/{xid}/fact")
def extra_fact(xid: int, body: ExtraFact, user: User = Depends(require(*ENTER)), db: Session = DB,
               today: date = Depends(get_today)):
    """Сколько часов фактически потрачено. Меньше запланированного — нужна причина или комментарий."""
    x = get_or_404(db, ExtraTask, xid, user)
    if x.plan_date > today:
        raise HTTPException(422, "Факт на будущую дату вносить нельзя")
    if user.role == "worker" and x.plan_date != today:
        raise HTTPException(403, "Сборщик вносит факт только за сегодня. За прошлые дни — обратитесь к бригадиру")
    w = get_or_404(db, Worker, x.worker_id, user)
    facts_service.can_touch_worker(user, w)
    if body.reason_id is not None:
        facts_service.check_reason(db, user, body.reason_id, db.get(Worker, x.worker_id))
    if body.fact_hours < x.hours and body.reason_id is None and not body.note.strip():
        raise HTTPException(422, f"Сделано меньше запланированного ({body.fact_hours:g} из {x.hours:g} ч) — укажите причину или комментарий")
    old = x.fact_hours
    x.fact_hours, x.reason_id, x.note = body.fact_hours, body.reason_id, body.note.strip()
    audit.log(db, user, "extra_task", x.id, "fact", {"hours": [old, body.fact_hours]})
    return extra_out(x)
