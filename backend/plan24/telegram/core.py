"""Общие действия бота и мини-приложения Telegram: одна логика, два интерфейса (чат и приложение).

Здесь нет ничего про сообщения-ответы в чате: функции возвращают результат, а оповещения других людей складывают в `out`
(их отправляет вызывающий через client.deliver). Права — те же, что и везде: через facts_service и данные организации.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import audit
from .. import db as dbm
from .. import facts_service, planning, reports_service
from ..facts_service import Actor, FactError
from ..models import (Fact, FactReason, TelegramLink, TelegramState, Tenant, User, Worker, WorkerAbsence, utcnow)
from ..timeutil import today_in
from . import events
from .ui import copy_message, fnum, kb, say

NUDGE_EVERY = timedelta(hours=1)                        # «Напомнить» — не чаще раза в час
PROBLEMS = {"mat": "📦 Нет материала", "tool": "🔧 Поломка, инструмент", "bad": "✖ Брак", "oth": "💬 Другое"}
ABS_PERIODS = {"t": "сегодня", "m": "завтра", "tm": "сегодня и завтра", "w7": "7 дней"}
ABS_REASONS = {"ill": "Болезнь", "vac": "Отпуск", "fam": "Личные обстоятельства", "oth": "Другое"}


# ------------------------------------------------------------------ состояние диалога (по чату)
def get_state(db: Session, chat_id: int) -> dict:
    row = db.get(TelegramState, chat_id)
    return dict(row.state) if row else {}


def put_state(db: Session, chat_id: int, state: dict):
    row = db.get(TelegramState, chat_id)
    if row is None:
        db.add(TelegramState(chat_id=chat_id, state=state, tenant_id=db.info["tenant_id"]))
    else:
        row.state = state
        row.updated_at = utcnow()


# ------------------------------------------------------------------ личность
def resolve_actor(db: Session, link: TelegramLink):
    """Кто пишет: исполнитель без учётной записи (роль worker) или пользователь-бригадир. None — доступ закрыт."""
    if link.worker_id:
        w = db.get(Worker, link.worker_id)
        return (Actor(tenant_id=link.tenant_id, worker_id=w.id), w.name) if w and w.tenant_id == link.tenant_id else (None, "")
    u = db.get(User, link.user_id)
    return (u, u.name) if u and u.is_active and u.tenant_id == link.tenant_id and u.role == "foreman" else (None, "")


def role_of(actor) -> str:
    return "foreman" if getattr(actor, "role", "") == "foreman" else "worker"


def tenant_today(db: Session, tenant_id: int) -> date:
    return today_in(db.get(Tenant, tenant_id).timezone)


# ------------------------------------------------------------------ задания
def flat_tasks(report: dict) -> list:
    rows = []
    for w in report["workers"]:
        for t in w["tasks"]:
            rows.append({**t, "worker_id": w["worker_id"], "worker": w["worker"]})
    return rows


def rows(db: Session, actor, wid: Optional[int] = None, day: Optional[date] = None) -> list:
    today = tenant_today(db, actor.tenant_id)
    r = flat_tasks(reports_service.day_report(db, actor, day or today, today))
    return [t for t in r if not wid or t["worker_id"] == wid]


def task_key(t: dict) -> str:
    return f"{t['order_line_id']}:{t['operation_id']}:{t['worker_id']}"


def find_task(db: Session, actor, key: str):
    """Задание строго из отчёта актора: подделанные идентификаторы чужих задач не пройдут."""
    try:
        ol, op, wk = (int(x) for x in key.split(":"))
    except ValueError:
        return None
    for t in rows(db, actor):
        if (t["order_line_id"], t["operation_id"], t["worker_id"]) == (ol, op, wk):
            return t
    return None


def find_task_ids(db: Session, actor, ol: int, op: int, wk: int):
    return find_task(db, actor, f"{ol}:{op}:{wk}")


def task_label(t: dict) -> str:
    return f"{t['worker']} · {t['operation']} · {t['product']} · {t['order']}"


def pending(r: list, wid: int = 0) -> list:
    """Задания, по которым факта ещё нет, а на сегодня что-то запланировано."""
    return [t for t in r if t["plan_qty"] and t["fact_qty"] is None and (not wid or t["worker_id"] == wid)]


def reasons_for(db: Session, tenant_id: int, worker_id: int) -> list:
    """Причины отклонения — участка работника и общие."""
    wc = db.get(Worker, worker_id).work_center_id
    rs = db.scalars(select(FactReason).where(FactReason.tenant_id == tenant_id,
                                             or_(FactReason.work_center_id.is_(None), FactReason.work_center_id == wc))
                    .order_by(FactReason.id).limit(8)).all()
    return [{"id": r.id, "name": r.name} for r in rs]


# ------------------------------------------------------------------ запись факта
def write_fact(db: Session, actor, task: dict, qty: float):
    today = tenant_today(db, actor.tenant_id)
    return facts_service.enter(db, actor, fact_date=today, order_line_id=task["order_line_id"], operation_id=task["operation_id"],
                               worker_id=task["worker_id"], qty=qty, today=today, source="telegram")


def close_day(db: Session, actor, wid: int = 0) -> dict:
    """Внести плановое по заданиям без факта (исполнитель — себе; бригадир — работнику или всему участку)."""
    r = rows(db, actor, wid or None) if role_of(actor) == "foreman" else rows(db, actor)
    ok, fails = 0, []
    for t in pending(r):
        try:
            write_fact(db, actor, t, t["plan_qty"])
            ok += 1
        except FactError as e:
            fails.append(f"{t['operation']}: {e.detail}")
    return {"ok": ok, "fails": fails}


# ------------------------------------------------------------------ бригадир: напомнить не внёсшим
def remind(db: Session, actor, chat_id: int, out: list) -> dict:
    if role_of(actor) != "foreman":
        return {"status": "denied"}
    st = get_state(db, chat_id)
    last = st.get("nudge")
    if last:
        ago = utcnow() - datetime.fromisoformat(last)
        if ago < NUDGE_EVERY:
            return {"status": "throttled", "minutes": max(1, int(ago.total_seconds() // 60))}
    pend = pending(rows(db, actor))
    if not pend:
        return {"status": "nobody"}
    by: dict = {}
    for t in pend:
        by.setdefault(t["worker_id"], [t["worker"], 0])[1] += 1
    links = {l.worker_id: l for l in db.scalars(select(TelegramLink).where(TelegramLink.tenant_id == actor.tenant_id,
                                                                          TelegramLink.worker_id.in_(list(by))))}
    sent, nolink = 0, []
    for wid, (nm, n) in by.items():
        if wid in links:
            say(out, links[wid].chat_id, f"🔔 {nm}, внесите, пожалуйста, факт: заданий без факта — {n}.",
                kb([[("📋 Задания", "l"), ("✅ Закрыть день", "cd:0")]]))
            sent += 1
        else:
            nolink.append(nm)
    st["nudge"] = utcnow().isoformat()
    put_state(db, chat_id, st)
    return {"status": "sent", "sent": sent, "total": len(by), "nolink": nolink}


# ------------------------------------------------------------------ проблема (сборщик → бригадир)
def send_problem(db: Session, actor, worker_chat_id: int, ptype: str, comment: str, media_message_id: Optional[int], out: list) -> int:
    """→ сколько бригадиров получили сообщение (0 — бригадир не подключён к Telegram). Обращение пишется в журнал."""
    w = db.get(Worker, actor.worker_id)
    links = events.foreman_links(db, actor.tenant_id, w.work_center_id, only_alerts=False)       # о проблеме сообщаем всегда
    audit.log(db, actor, "problem", w.id, "report", {"type": ptype, "media": media_message_id is not None, "comment": comment[:200]})
    if not links:
        return 0
    tasks = [t for t in rows(db, actor) if t["plan_qty"]][:4]
    text = f"⚠ Проблема: {PROBLEMS[ptype]}\nОт: {w.name}" + (f"\n{comment}" if comment else "") + \
        ("\nСегодня у него: " + "; ".join(f"{t['operation']} · {t['order']}" for t in tasks) if tasks else "")
    for l in links:
        say(out, l.chat_id, text, kb([[("👍 Принял", f"ak:{w.id}"), (f"👤 {w.name[:24]}", f"w:{w.id}")]]))
        if media_message_id:
            copy_message(out, l.chat_id, worker_chat_id, media_message_id)
    return len(links)


def ack_problem(db: Session, actor, name: str, worker_id: int, out: list) -> str:
    w = db.get(Worker, worker_id)
    if w is None or w.tenant_id != actor.tenant_id or w.work_center_id != actor.work_center_id:
        return "Это работник другого участка"
    l = events.worker_link(db, actor.tenant_id, worker_id)
    if l:
        say(out, l.chat_id, f"👍 {name} принял ваше сообщение и в курсе.")
    return "Работник уведомлён" if l else "Принято (у работника нет Telegram)"


# ------------------------------------------------------------------ отсутствие
def create_absence(db: Session, actor, worker_id: int, period: str, reason: str, out: list) -> dict:
    """→ {"error": …} или {"worker": имя, "when": «сегодня», "reason": «болезнь»}. Оповещает бригадира/работника."""
    if period not in ABS_PERIODS or reason not in ABS_REASONS:
        return {"error": "Не понял период или причину"}
    w = db.get(Worker, worker_id)
    role = role_of(actor)
    if w is None or w.tenant_id != actor.tenant_id or (role == "worker" and w.id != actor.worker_id) \
            or (role == "foreman" and w.work_center_id != actor.work_center_id):
        return {"error": "Нет доступа к этому работнику"}
    today = tenant_today(db, actor.tenant_id)
    d1, d2 = {"t": (today, today), "m": (today + timedelta(days=1),) * 2, "tm": (today, today + timedelta(days=1)),
              "w7": (today, today + timedelta(days=6))}[period]
    a = WorkerAbsence(tenant_id=actor.tenant_id, worker_id=w.id, date_from=d1, date_to=d2, reason=ABS_REASONS[reason])
    db.add(a)
    db.flush()
    audit.log(db, actor, "absence", a.id, "create", {"worker": w.name, "from": d1, "to": d2, "reason": a.reason, "source": "telegram"})
    when, why = ABS_PERIODS[period], ABS_REASONS[reason].lower()
    if role == "worker":
        for l in events.foreman_links(db, actor.tenant_id, w.work_center_id, only_alerts=False):
            say(out, l.chat_id, f"🤒 {w.name}: не выйдет {when} ({why}).\nЗадания этого работника в плане остались — перепланируйте участок.",
                kb([[("🔄 Перепланировать участок", "rp"), ("Позже", "x")]]))
    else:
        l = events.worker_link(db, actor.tenant_id, w.id)
        if l:
            say(out, l.chat_id, f"Бригадир отметил ваше отсутствие: {when} ({why}).")
    return {"worker": w.name, "when": when, "reason": why, "ill": reason == "ill"}


# ------------------------------------------------------------------ перепланирование участка
def replan_center(db: Session, actor, out: list) -> dict:
    """Пересчитать план участка бригадира. Поднимает planning.NoActivePlan, если общего плана ещё нет."""
    today = tenant_today(db, actor.tenant_id)
    days = [today, today + timedelta(days=1)]
    track = events.enabled()
    before = events.plan_state(db, actor.tenant_id, days) if track else {}
    with dbm.tenant_plan_lock(actor.tenant_id):
        p = planning.run_and_save(db, actor, today, {"scope_center_id": actor.work_center_id})
        audit.log(db, actor, "plan", p.id, "replan", {**p.params, "source": "telegram"})
    if track:
        events.notify_plan_changes(db, actor.tenant_id, before, events.plan_state(db, actor.tenant_id, days), out, today, who="Бригадир")
    r = [t for t in rows(db, actor) if t["plan_qty"]]
    return {"tasks": len(r), "workers": len({t["worker_id"] for t in r}), "warnings": len(p.warnings)}


# ------------------------------------------------------------------ очередь подтверждений бригадира (FR-22a)
def queue_rows(db: Session, actor):
    """(факты со статусом «ждёт подтверждения» работников участка бригадира, имена работников)."""
    ws = {w.id: w.name for w in db.scalars(select(Worker).where(Worker.tenant_id == actor.tenant_id,
                                                                  Worker.work_center_id == actor.work_center_id))}
    fs = db.scalars(select(Fact).where(Fact.tenant_id == actor.tenant_id, Fact.status == "submitted", Fact.deleted_at.is_(None),
                                       Fact.worker_id.in_(ws or [0])).order_by(Fact.id).limit(30)).all()
    return fs, ws
