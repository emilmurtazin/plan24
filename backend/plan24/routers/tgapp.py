"""API мини-приложения Telegram (веб-страница внутри Telegram для сборщика и бригадира).

Вход без пароля: приложение присылает данные запуска (initData), сервер проверяет подпись Telegram (ключ — токен бота) и ищет
привязку чата (та же, что у бота). Дальше — токен сессии (typ=tgapp) на 8 часов; при каждом запросе привязка проверяется заново:
отвязали чат или закрыли доступ — приложение перестаёт работать сразу. Права те же, что у бота: сборщик — только своё и только
сегодня, бригадир — только свой участок. Вся логика — в telegram/core.py, общем с ботом.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, facts_service, planning, reports_service, security
from .. import db as dbm
from ..db import DB
from ..models import TelegramLink, Worker
from ..telegram import client as tgclient
from ..telegram import core, events, webapp

router = APIRouter(prefix="/tgapp", tags=["tgapp"])


# ------------------------------------------------------------------ контекст запроса
class Ctx:
    def __init__(self, db: Session, link: TelegramLink, actor, name: str):
        self.db, self.link, self.actor, self.name = db, link, actor, name
        self.role = core.role_of(actor)

    @property
    def today(self) -> date:
        return core.tenant_today(self.db, self.actor.tenant_id)


def tg_ctx(request: Request, db: Session = DB) -> Ctx:
    auth = request.headers.get("authorization", "")
    ids = security.decode_tgapp(auth[7:]) if auth.lower().startswith("bearer ") else None
    if ids is None:
        raise HTTPException(401, "Сессия истекла — откройте приложение из Telegram заново")
    lid, tid = ids
    dbm.set_tenant(db, tid)
    link = db.get(TelegramLink, lid)
    if link is None or link.tenant_id != tid:
        raise HTTPException(401, "Чат отвязан — попросите руководителя выдать новый код")
    actor, name = core.resolve_actor(db, link)
    if actor is None:
        raise HTTPException(401, "Доступ закрыт. Обратитесь к руководителю")
    return Ctx(db, link, actor, name)


def _send(bg: BackgroundTasks, out: list):
    if out:
        bg.add_task(tgclient.deliver, out)


# ------------------------------------------------------------------ вход
class AuthIn(BaseModel):
    init_data: str = Field(max_length=webapp.MAX_LEN)


@router.post("/auth")
def auth(body: AuthIn, request: Request, db: Session = DB):
    cfg = config.telegram_settings()
    if not cfg["enabled"]:
        raise HTTPException(404, "Не найдено")
    key = (request.client.host if request.client else "?")
    if security.tgapp_limiter.blocked(key):
        raise HTTPException(429, "Слишком много неудачных попыток. Подождите несколько минут")
    try:
        info = webapp.validate(body.init_data, cfg["token"], config.tgapp_max_age())
    except webapp.InitDataError:
        security.tgapp_limiter.fail(key)
        raise HTTPException(401, "Не удалось подтвердить вход из Telegram. Откройте приложение кнопкой в чате с ботом")
    dbm.auth_scope(db, True)                      # организация до привязки неизвестна — поиск только чтением
    link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == info["user"]["id"]))
    if link is None:
        raise HTTPException(403, "Этот Telegram не привязан к Plan 24. Отправьте боту /start и одноразовый код от руководителя")
    dbm.set_tenant(db, link.tenant_id)
    actor, name = core.resolve_actor(db, link)
    if actor is None:
        raise HTTPException(403, "Доступ закрыт. Обратитесь к руководителю")
    c = Ctx(db, link, actor, name)
    return {"token": security.make_tgapp(link.id, link.tenant_id), "expires_in": config.TGAPP_TTL_MIN * 60, "role": c.role,
            "name": name, "worker_id": getattr(actor, "worker_id", None), "today": c.today.isoformat()}


# ------------------------------------------------------------------ задания
def _task_out(t: dict) -> dict:
    return {k: t.get(k) for k in ("order_line_id", "operation_id", "operation", "product", "order", "plan_qty", "fact_qty", "fact_id",
                                  "fact_status", "state", "reason_id", "reason")}


def _workers_out(rep: dict, only_planned: bool = False) -> list:
    out = []
    for w in rep["workers"]:
        tasks = [_task_out(t) for t in w["tasks"] if (t["plan_qty"] or not only_planned)]
        extras = [] if only_planned else [{k: x.get(k) for k in ("id", "title", "hours", "fact_hours", "order", "project", "product")} for x in w["extras"]]
        if tasks or extras or not only_planned:
            out.append({"worker_id": w["worker_id"], "worker": w["worker"], "tasks": tasks, "extras": extras})
    return out


@router.get("/day")
def day(c: Ctx = Depends(tg_ctx)):
    """Сегодняшние задания: сборщику — его, бригадиру — работников его участка. Плюс причины отклонений по работникам."""
    rep = reports_service.day_report(c.db, c.actor, c.today, c.today)
    workers = _workers_out(rep)
    out = {"date": c.today.isoformat(), "today": c.today.isoformat(), "role": c.role, "name": c.name,
           "worker_id": getattr(c.actor, "worker_id", None), "can_enter": bool(rep["can_enter"]), "is_workday": rep["is_workday"],
           "workers": workers, "reasons": {w["worker_id"]: core.reasons_for(c.db, c.actor.tenant_id, w["worker_id"]) for w in workers}}
    if c.role == "foreman":
        out["queue"] = len(core.queue_rows(c.db, c.actor)[0])
        # весь участок (а не только тех, у кого сегодня есть задания): отсутствие можно отметить у любого
        out["team"] = [{"worker_id": w.id, "worker": w.name} for w in c.db.scalars(
            select(Worker).where(Worker.tenant_id == c.actor.tenant_id, Worker.work_center_id == c.actor.work_center_id).order_by(Worker.name))]
    return out


@router.get("/tomorrow")
def tomorrow(c: Ctx = Depends(tg_ctx)):
    """Ближайший день с заданиями (завтра, а если выходной — следующий рабочий): только план, без ввода факта."""
    today = c.today
    for n in range(1, 8):
        d = today + timedelta(days=n)
        rep = reports_service.day_report(c.db, c.actor, d, today)
        workers = _workers_out(rep, only_planned=True)
        if workers:
            return {"date": d.isoformat(), "is_tomorrow": n == 1, "role": c.role, "workers": workers}
    return {"date": (today + timedelta(days=1)).isoformat(), "is_tomorrow": True, "role": c.role, "workers": []}


class FactIn(BaseModel):
    order_line_id: int
    operation_id: int
    worker_id: int
    qty: float = Field(ge=0, le=1_000_000)


@router.post("/fact")
def put_fact(body: FactIn, c: Ctx = Depends(tg_ctx)):
    """Записать итог дня по заданию (повторный ввод заменяет значение). Только сегодня; задание должно быть в отчёте актора."""
    t = core.find_task(c.db, c.actor, f"{body.order_line_id}:{body.operation_id}:{body.worker_id}")
    if t is None:
        raise HTTPException(404, "Задание недоступно. Обновите список")
    f, old = core.write_fact(c.db, c.actor, t, body.qty)
    return {"fact_id": f.id, "qty": f.qty, "previous": old, "status": f.status, "plan_qty": t["plan_qty"]}


class UndoIn(BaseModel):
    previous: Optional[float] = Field(None, ge=0, le=1_000_000)


@router.post("/fact/{fid}/undo")
def undo_fact(fid: int, body: UndoIn, c: Ctx = Depends(tg_ctx)):
    """Вернуть прежнее значение (если оно было) или убрать запись."""
    f = facts_service.own_fact(c.db, c.actor, fid)
    if body.previous is None:
        facts_service.remove(c.db, c.actor, f)
        return {"removed": True}
    t = core.find_task(c.db, c.actor, f"{f.order_line_id}:{f.operation_id}:{f.worker_id}")
    if t is None:
        raise HTTPException(404, "Задание недоступно. Обновите список")
    f2, _ = core.write_fact(c.db, c.actor, t, body.previous)
    return {"removed": False, "fact_id": f2.id, "qty": f2.qty}


class ReasonIn(BaseModel):
    reason_id: Optional[int] = None            # None — «без причины»


@router.post("/fact/{fid}/reason")
def fact_reason(fid: int, body: ReasonIn, bg: BackgroundTasks, c: Ctx = Depends(tg_ctx)):
    """Причина отклонения (из причин участка работника и общих). Бригадир участка получает оповещение, если сделано меньше плана."""
    f = facts_service.own_fact(c.db, c.actor, fid)
    if body.reason_id is not None:
        facts_service.set_reason(c.db, c.actor, f, body.reason_id)
    out: list = []
    events.alert_shortfall(c.db, f, out, skip_user_id=c.actor.id if c.role == "foreman" else None)
    _send(bg, out)
    return {"ok": True}


class WorkerIn(BaseModel):
    worker_id: int = 0                         # бригадир: 0 — весь участок


@router.post("/closeday")
def close_day(body: WorkerIn, c: Ctx = Depends(tg_ctx)):
    return core.close_day(c.db, c.actor, body.worker_id)


# ------------------------------------------------------------------ проблема и отсутствие
class ProblemIn(BaseModel):
    type: str = Field(pattern="^(mat|tool|bad|oth)$")
    comment: str = Field("", max_length=500)


@router.post("/problem")
def problem(body: ProblemIn, bg: BackgroundTasks, c: Ctx = Depends(tg_ctx)):
    if c.role != "worker":
        raise HTTPException(403, "Сообщение о проблеме отправляет сборщик")
    out: list = []
    n = core.send_problem(c.db, c.actor, c.link.chat_id, body.type, body.comment.strip(), None, out)
    _send(bg, out)
    return {"delivered": n}


class AbsenceIn(BaseModel):
    worker_id: Optional[int] = None            # сборщик — себя (поле игнорируется); бригадир — работника своего участка
    period: str = Field(pattern="^(t|m|tm|w7)$")
    reason: str = Field(pattern="^(ill|vac|fam|oth)$")


@router.post("/absence")
def absence(body: AbsenceIn, bg: BackgroundTasks, c: Ctx = Depends(tg_ctx)):
    wid = c.actor.worker_id if c.role == "worker" else body.worker_id
    if not wid:
        raise HTTPException(422, "Выберите работника")
    out: list = []
    r = core.create_absence(c.db, c.actor, wid, body.period, body.reason, out)
    if "error" in r:
        raise HTTPException(403, r["error"])
    _send(bg, out)
    return r


# ------------------------------------------------------------------ бригадир
def _foreman(c: Ctx):
    if c.role != "foreman":
        raise HTTPException(403, "Доступно бригадиру")


@router.post("/replan")
def replan(bg: BackgroundTasks, c: Ctx = Depends(tg_ctx)):
    _foreman(c)
    out: list = []
    try:
        r = core.replan_center(c.db, c.actor, out)
    except planning.NoActivePlan:
        raise HTTPException(409, "Сначала нужен общий план организации — его строит руководитель проекта")
    _send(bg, out)
    return r


@router.post("/remind")
def remind(bg: BackgroundTasks, c: Ctx = Depends(tg_ctx)):
    _foreman(c)
    out: list = []
    r = core.remind(c.db, c.actor, c.link.chat_id, out)
    _send(bg, out)
    return r


@router.get("/queue")
def queue(c: Ctx = Depends(tg_ctx)):
    _foreman(c)
    fs, ws = core.queue_rows(c.db, c.actor)
    return {"items": [{"fact_id": f.id, "worker": ws[f.worker_id], "qty": f.qty, "date": f.fact_date.isoformat()} for f in fs]}


class ConfirmIn(BaseModel):
    fact_id: Optional[int] = None              # None — подтвердить всё


@router.post("/confirm")
def confirm(body: ConfirmIn, c: Ctx = Depends(tg_ctx)):
    _foreman(c)
    if body.fact_id is None:
        fs, _ = core.queue_rows(c.db, c.actor)
        for f in fs:
            facts_service.confirm(c.db, c.actor, f)
        return {"confirmed": len(fs)}
    facts_service.confirm(c.db, c.actor, facts_service.own_fact(c.db, c.actor, body.fact_id))
    return {"confirmed": 1}


# ------------------------------------------------------------------ другая работа (смена проекта, изделия, операции) и работа вне перечня
@router.get("/catalog")
def catalog(c: Ctx = Depends(tg_ctx)):
    """Позиции заказов в работе: на любую из них работника могли переключить. Выбор — проект → заказ → операция."""
    return {"orders": core.work_orders(c.db, c.actor)}


@router.get("/catalog/ops")
def catalog_ops(order_line_id: int, c: Ctx = Depends(tg_ctx)):
    return {"operations": core.order_operations(c.db, c.actor, order_line_id)}


class OtherFactIn(BaseModel):
    order_line_id: int
    operation_id: int
    worker_id: Optional[int] = None            # сборщик — себе (поле игнорируется); бригадир — работнику своего участка
    qty: float = Field(ge=0, le=1_000_000)


@router.post("/fact/other")
def other_fact(body: OtherFactIn, c: Ctx = Depends(tg_ctx)):
    """Факт по любой операции любой позиции — в том числе не из плана. Можно вносить несколько операций подряд."""
    if c.role == "foreman" and not body.worker_id:
        raise HTTPException(422, "Выберите работника")
    f, old = core.write_fact_any(c.db, c.actor, body.worker_id or 0, body.order_line_id, body.operation_id, body.qty)
    return {"fact_id": f.id, "qty": f.qty, "previous": old, "status": f.status, "worker_id": f.worker_id}


class ExtraIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    hours: float = Field(gt=0, le=24)
    order_line_id: Optional[int] = None        # None — работа не относится к заказу
    worker_id: Optional[int] = None


class ExtraHoursIn(BaseModel):
    fact_hours: float = Field(ge=0, le=24)


def _extra_json(x) -> dict:
    return {"id": x.id, "title": x.title, "hours": x.hours, "fact_hours": x.fact_hours, "worker_id": x.worker_id, "order_line_id": x.order_line_id}


@router.post("/extra", status_code=201)
def add_extra(body: ExtraIn, c: Ctx = Depends(tg_ctx)):
    return _extra_json(core.add_extra(c.db, c.actor, body.worker_id, body.title, body.hours, body.order_line_id))


@router.put("/extra/{xid}")
def put_extra(xid: int, body: ExtraHoursIn, c: Ctx = Depends(tg_ctx)):
    return _extra_json(core.set_extra_hours(c.db, c.actor, xid, body.fact_hours))


@router.delete("/extra/{xid}")
def del_extra(xid: int, c: Ctx = Depends(tg_ctx)):
    core.delete_extra(c.db, c.actor, xid)
    return {"removed": True}
