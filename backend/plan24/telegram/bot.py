"""Обработка обновлений Telegram. Работает с «сырыми» update-словарями, без привязки к библиотеке:
так её можно проверять без сети, а вебхук и long polling используют один код.

Все изменения данных идут через facts_service — те же права и правила, что и в веб-интерфейсе.
Тексты сообщений и chat_id в логи не пишутся.

Экраны (список заданий, карточка задания, «по людям») переписываются на месте — чат не превращается в ленту. Какой экран
сейчас открыт, запоминается в состоянии диалога (view), поэтому кнопки действий просто перерисовывают его.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from .. import db as dbm
from .. import facts_service, planning, reports_service, security
from ..facts_service import Actor, FactError
from ..models import (Fact, FactReason, TelegramCode, TelegramLink, TelegramState, TelegramUpdate, Tenant, User,
                      Worker, WorkerAbsence, utcnow)
from ..timeutil import today_in
from . import events
from .ui import (MAX_QTY, WD, answer, copy_message, fday, fnum, kb, menu_action, menu_keyboard, parse_qty, say, set_commands,
                 show)

log = logging.getLogger("plan24.telegram")
PAGE = 6
bind_limiter = security.RateLimiter("bind", 5, 900)      # неверные коды привязки: 5 за 15 минут на чат (общий для процессов)
NUDGE_EVERY = timedelta(hours=1)                        # «Напомнить» — не чаще раза в час
PROBLEMS = {"mat": "📦 Нет материала", "tool": "🔧 Поломка, инструмент", "bad": "✖ Брак", "oth": "💬 Другое"}
ABS_PERIODS = {"t": "сегодня", "m": "завтра", "tm": "сегодня и завтра", "w7": "7 дней"}
ABS_REASONS = {"ill": "Болезнь", "vac": "Отпуск", "fam": "Личные обстоятельства", "oth": "Другое"}
MEDIA = ("photo", "voice", "video", "video_note", "document", "audio", "animation")


# ------------------------------------------------------------------ состояние диалога
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


def _clear_pending(db: Session, chat_id: int):
    st = get_state(db, chat_id)
    for k in ("await", "confirm", "problem"):
        st.pop(k, None)
    put_state(db, chat_id, st)


# ------------------------------------------------------------------ личность
def resolve_actor(db: Session, link: TelegramLink):
    """Кто пишет: исполнитель без учётной записи (роль worker) или пользователь-бригадир. None — доступ закрыт."""
    if link.worker_id:
        w = db.get(Worker, link.worker_id)
        return (Actor(tenant_id=link.tenant_id, worker_id=w.id), w.name) if w and w.tenant_id == link.tenant_id else (None, "")
    u = db.get(User, link.user_id)
    return (u, u.name) if u and u.is_active and u.tenant_id == link.tenant_id and u.role == "foreman" else (None, "")


def _role(actor) -> str:
    return "foreman" if getattr(actor, "role", "") == "foreman" else "worker"


def tenant_today(db: Session, tenant_id: int) -> date:
    return today_in(db.get(Tenant, tenant_id).timezone)


# ------------------------------------------------------------------ точка входа
def handle_update(db: Session, upd: dict, out: list) -> None:
    uid = upd.get("update_id")
    if uid is not None:
        try:
            with db.begin_nested():
                db.add(TelegramUpdate(update_id=uid))
                db.flush()
        except IntegrityError:
            return                                                    # повторная доставка — игнорируем
        db.execute(delete(TelegramUpdate).where(TelegramUpdate.created_at < utcnow() - timedelta(days=7)))
    chat_id = _chat_id(upd)
    if chat_id is None:
        return
    # Организация неизвестна, пока не найдена привязка чата: поиск — в режиме «только чтение до входа».
    # Дальше все обращения к данным идут уже в контексте организации привязанного человека.
    dbm.auth_scope(db, True)
    link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
    if link is not None:
        dbm.set_tenant(db, link.tenant_id)
    try:
        with db.begin_nested():
            if "callback_query" in upd:
                _on_callback(db, upd["callback_query"], chat_id, out, link)
            else:
                _on_message(db, upd["message"], chat_id, out, link)
    except Exception as e:                                           # noqa: BLE001 — один плохой апдейт не должен ронять бота
        log.error("bot update failed: %s", type(e).__name__)
        say(out, chat_id, "Что-то пошло не так. Попробуйте ещё раз чуть позже.")


def _chat_id(upd: dict) -> Optional[int]:
    m = upd.get("message") or (upd.get("callback_query") or {}).get("message") or {}
    chat = m.get("chat") or {}
    return chat.get("id") if chat.get("type") == "private" else None      # группы и каналы игнорируем


# ------------------------------------------------------------------ сообщения
SLASH = {"/start": "help", "/help": "help", "/tasks": "tasks", "/zadaniya": "tasks", "/people": "tasks", "/undo": "undo",
         "/cancel": "cancel", "/queue": "queue", "/closeday": "closeday", "/problem": "problem", "/absent": "absent",
         "/tomorrow": "tomorrow", "/settings": "settings"}


def _on_message(db: Session, msg: dict, chat_id: int, out: list, link: Optional[TelegramLink]):
    text = (msg.get("text") or "").strip()
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower().split("@")[0] if cmd.startswith("/") else ""
    arg = arg.strip()

    if link is None:
        code = arg if cmd == "/start" else (text if re.fullmatch(r"[A-Za-z0-9\- ]{6,14}", text) else "")
        if code:
            return _link(db, chat_id, code, out)
        return say(out, chat_id, "Этот чат пока не привязан к Plan 24.\nПопросите руководителя выдать одноразовый код и отправьте:\n/start КОД")

    actor, name = resolve_actor(db, link)
    if actor is None:
        return say(out, chat_id, "Доступ закрыт. Обратитесь к руководителю.")
    if cmd == "/start" and arg:
        return say(out, chat_id, f"Этот чат уже привязан к «{name}». Чтобы привязать другого человека, руководитель должен сначала отвязать этот чат.")

    action = SLASH.get(cmd) if cmd else menu_action(text)
    if action:
        _clear_pending(db, chat_id)                               # нажали меню — незаконченный ввод сбрасывается
        return _dispatch(db, chat_id, actor, name, link, action, out)

    st = get_state(db, chat_id)
    if st.get("problem"):
        return _problem_message(db, chat_id, msg, actor, st, out)
    if st.get("await") and text:
        return _number(db, chat_id, actor, st, text, out)
    if any(k in msg for k in MEDIA):
        return say(out, chat_id, "Фото и файлы я принимаю только в разделе «⚠ Проблема».", menu_keyboard(_role(actor)))
    say(out, chat_id, "Не понял. Выберите действие в меню внизу.", menu_keyboard(_role(actor)))


def _dispatch(db, chat_id, actor, name, link, action: str, out: list):
    role = _role(actor)
    if action == "help":
        return _help(db, chat_id, actor, name, out)
    if action == "tasks":
        return _open(db, chat_id, None, actor, name, {"s": "people" if role == "foreman" else "list", "page": 0, "wid": None}, out)
    if action == "undo":
        return _undo(db, chat_id, actor, out)
    if action == "cancel":
        return say(out, chat_id, "Отменено.", menu_keyboard(role))
    if action == "queue":
        return _queue(db, chat_id, actor, out)
    if action == "closeday":
        return _closeday_ask(db, chat_id, None, actor, 0, out)
    if action == "problem":
        return _problem_start(db, chat_id, actor, out)
    if action == "absent":
        return _absent_start(db, chat_id, actor, out)
    if action == "tomorrow":
        return _tomorrow(db, chat_id, actor, name, out)
    if action == "settings":
        return _settings(db, chat_id, None, link, actor, out)


def _help(db, chat_id, actor, name, out):
    role = _role(actor)
    lines = [f"Здравствуйте, {name}!", "", "Всё нужное — в меню внизу экрана. Кнопки в сообщениях обновляют их на месте."]
    if role == "foreman":
        lines += ["", "👥 По людям — кто что делает; факт можно внести за любого работника участка",
                  "✅ Закрыть день — внести плановое по всем заданиям без факта",
                  "📥 Очередь — факт, который ждёт вашего подтверждения",
                  "🤒 Отсутствие — отметить, что работник не выйдет, и сразу перепланировать участок"]
    else:
        lines += ["", "📋 Задания — задания на сегодня; «+1», «+5», «Всё» — быстро внести сделанное",
                  "✅ Закрыть день — внести плановое по всем заданиям одним нажатием",
                  "⚠ Проблема — сообщить бригадиру (можно с фото)", "🤒 Не выйду — предупредить, что не сможете выйти"]
    lines += ["📅 Завтра — что планируется завтра", "↩ /undo — отменить последний ввод", "⚙ Настройки — время и вид уведомлений"]
    say(out, chat_id, "\n".join(lines), menu_keyboard(role))
    set_commands(out, chat_id, role)


# ------------------------------------------------------------------ привязка
def _norm_code(c: str) -> str:
    return re.sub(r"[\s\-]", "", c).upper()


def code_hash(c: str) -> str:
    return hashlib.sha256(_norm_code(c).encode()).hexdigest()


def _link(db: Session, chat_id: int, raw: str, out: list):
    key = f"tg{chat_id}"
    if bind_limiter.blocked(key):
        return say(out, chat_id, "Слишком много неверных кодов. Попробуйте позже или попросите руководителя выдать новый.")
    code = db.scalar(select(TelegramCode).where(TelegramCode.code_hash == code_hash(raw)))
    if code is None or code.used_at is not None or code.expires_at < utcnow():
        bind_limiter.fail(key)
        return say(out, chat_id, "Код не подошёл: он неверный, уже использован или истёк. Попросите руководителя выдать новый.")
    dbm.set_tenant(db, code.tenant_id)            # дальше — запись, только в организации, выдавшей код
    # одна личность — один чат: прежняя привязка (например, со старого телефона) заменяется
    db.execute(delete(TelegramLink).where((TelegramLink.worker_id == code.worker_id) if code.worker_id
                                          else (TelegramLink.user_id == code.user_id)))
    db.flush()
    db.add(TelegramLink(tenant_id=code.tenant_id, chat_id=chat_id, worker_id=code.worker_id, user_id=code.user_id))
    code.used_at = utcnow()
    db.flush()
    bind_limiter.reset(key)
    link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
    actor, name = resolve_actor(db, link)
    put_state(db, chat_id, {})
    role = _role(actor)
    say(out, chat_id, f"Готово! Вы — {name}. Теперь здесь можно смотреть задания и вносить факт.\nПароль не нужен.\n"
                      "Всё нужное — в меню внизу экрана.", menu_keyboard(role))
    set_commands(out, chat_id, role)


# ------------------------------------------------------------------ задания: экраны
def _flat_tasks(report: dict) -> list:
    rows = []
    for w in report["workers"]:
        for t in w["tasks"]:
            rows.append({**t, "worker_id": w["worker_id"], "worker": w["worker"]})
    return rows


def _rows(db, actor, wid: Optional[int] = None, day: Optional[date] = None) -> list:
    today = tenant_today(db, actor.tenant_id)
    rows = _flat_tasks(reports_service.day_report(db, actor, day or today, today))
    return [t for t in rows if not wid or t["worker_id"] == wid]


def _key(t: dict) -> str:
    return f"{t['order_line_id']}:{t['operation_id']}:{t['worker_id']}"


def _find_task(db, actor, key: str):
    """Задание строго из отчёта актора: подделанные идентификаторы чужих задач не пройдут."""
    try:
        ol, op, wk = (int(x) for x in key.split(":"))
    except ValueError:
        return None
    for t in _rows(db, actor):
        if (t["order_line_id"], t["operation_id"], t["worker_id"]) == (ol, op, wk):
            return t
    return None


def _label(t: dict) -> str:
    return f"{t['worker']} · {t['operation']} · {t['product']} · {t['order']}"


def _pending(rows: list, wid: int = 0) -> list:
    """Задания, по которым факта ещё нет, а на сегодня что-то запланировано."""
    return [t for t in rows if t["plan_qty"] and t["fact_qty"] is None and (not wid or t["worker_id"] == wid)]


def _footer(st: dict, rows: list, role: str, wid: int) -> list:
    out = []
    if _pending(rows, wid):
        target = rows[0]["worker"] if (role == "foreman" and wid and rows) else ""
        out.append([("✅ Закрыть день по плану" + (f" — {target}" if target else ""), f"cd:{wid}")])
    last = st.get("last")
    if last:
        out.append([("↩ Отменить последнее", f"u:{last['fact_id']}")])
    return out


def _screen(db, actor, name, view: dict, st: dict):
    """(текст, кнопки) экрана по описанию view: people — участок по людям; list — список заданий; card — одно задание."""
    role = _role(actor)
    today = tenant_today(db, actor.tenant_id)
    s, wid = view.get("s", "list"), int(view.get("wid") or 0)
    if s == "card":
        t = _find_task(db, actor, view["key"])
        if t is not None:
            return _card(t, st, role)
        s, wid = ("list", 0) if role == "worker" else ("people", 0)
    rows = _rows(db, actor, wid or None)
    if not rows:
        if wid:
            return f"У этого работника на сегодня, {fday(today)}, заданий нет.", kb([[("◀ К участку", "l")]])
        return f"На сегодня, {fday(today)}, заданий нет.", None
    if s == "people":
        return _people(rows, today, st, role)
    page = int(view.get("page") or 0)
    pages = (len(rows) + PAGE - 1) // PAGE
    page = max(0, min(page, pages - 1))
    head = f"{rows[0]['worker']} · задания на {fday(today)}" if (role == "foreman" and wid) else \
        (f"Задания на {fday(today)}" + (f" — {name}" if role == "worker" else ""))
    lines, buttons = [head], []
    for i, t in enumerate(rows[page * PAGE:(page + 1) * PAGE], start=page * PAGE + 1):
        done = f", сделано {fnum(t['fact_qty'])}" if t["fact_qty"] is not None else ""
        plan = f"план {fnum(t['plan_qty'])}" if t["plan_qty"] else "вне плана"
        flag = " ✔" if t["state"] == "done" else ""
        lines.append(f"{i}. {t['operation']} · {t['product']} · {t['order']} — {plan}{done}{flag}")
        row = [(f"🔢 №{i}", f"k:{_key(t)}")]
        if t["plan_qty"] and t["state"] != "done":
            row.insert(0, (f"✔ №{i} всё ({fnum(t['plan_qty'])})", f"d:{_key(t)}"))
        buttons.append(row)
    nav = []
    if page > 0:
        nav.append(("◀ Назад", f"p:{page - 1}:{wid}"))
    if wid and role == "foreman":
        nav.append(("◀ К участку", "l"))
    if page < pages - 1:
        nav.append(("Дальше ▶", f"p:{page + 1}:{wid}"))
    if nav:
        buttons.append(nav)
    return "\n".join(lines), kb(buttons + _footer(st, rows, role, wid))


def _people(rows: list, today: date, st: dict, role: str):
    by: dict = {}
    for t in rows:
        w = by.setdefault(t["worker_id"], {"name": t["worker"], "n": 0, "done": 0, "planned": 0})
        w["n"] += 1
        w["done"] += 1 if t["fact_qty"] is not None else 0
        w["planned"] += 1 if t["plan_qty"] else 0
    lines = [f"Участок · {fday(today)}"]
    btns = []
    for wid, w in list(by.items())[:24]:
        mark = "✔" if w["done"] == w["n"] else ("…" if w["done"] else "•")
        lines.append(f"{mark} {w['name']} — факт {w['done']} из {w['n']}")
        btns.append((f"{mark} {w['name'][:22]} {w['done']}/{w['n']}", f"w:{wid}"))
    rows_b = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    pend = _pending(rows)
    if pend:
        rows_b.append([("🔔 Напомнить тем, кто не внёс", "rem")])
    return "\n".join(lines), kb(rows_b + _footer(st, rows, role, 0))


def _card(t: dict, st: dict, role: str):
    plan, fact = t["plan_qty"], t["fact_qty"]
    key = _key(t)
    lines = [f"{t['operation']} · {t['product']} · {t['order']}"]
    if role == "foreman":
        lines.append(f"Исполнитель: {t['worker']}")
    lines.append(f"План: {fnum(plan) if plan else 'вне плана'}   Сделано: {fnum(fact) if fact is not None else 'ещё не внесено'}"
                 + ("  ✔" if t["state"] == "done" else ""))
    rows = [[("＋1", f"a:{key}:1"), ("＋5", f"a:{key}:5"), ("＋10", f"a:{key}:10")]]
    quick = []
    if plan and (fact is None or fact < plan):
        quick.append((f"✔ Всё ({fnum(plan)})", f"d:{key}"))
    quick.append(("0 — не делал", f"z:{key}"))
    rows.append(quick)
    rows.append([("✏ Ввести число", f"n:{key}"), ("◀ К списку", "l")])
    if plan and fact is not None and fact < plan and t.get("fact_id") and not t.get("reason_id") and t["fact_id"] not in st.get("nr", []):
        lines.append(f"Меньше плана ({fnum(plan)}). Что помешало?")
        rs = st.get("reasons") or []
        for i in range(0, len(rs), 2):
            rows.append([(r["name"][:28], f"r:{t['fact_id']}:{r['id']}") for r in rs[i:i + 2]])
        rows.append([("Без причины", f"r:{t['fact_id']}:0")])
    last = st.get("last")
    if last:
        rows.append([("↩ Отменить последнее", f"u:{last['fact_id']}")])
    return "\n".join(lines), kb(rows)


def _open(db, chat_id, mid, actor, name, view: dict, out: list):
    """Открыть/перерисовать экран и запомнить его как текущий."""
    st = get_state(db, chat_id)
    st["view"] = view
    if view.get("s") == "card":                              # причины отклонения — участка работника и общие
        t = _find_task(db, actor, view["key"])
        if t is not None:
            st["reasons"] = _reasons_for(db, actor.tenant_id, t["worker_id"])
    put_state(db, chat_id, st)
    text, markup = _screen(db, actor, name, view, st)
    show(out, chat_id, mid, text, markup)


def _reasons_for(db, tenant_id: int, worker_id: int) -> list:
    wc = db.get(Worker, worker_id).work_center_id
    rs = db.scalars(select(FactReason).where(FactReason.tenant_id == tenant_id,
                                             or_(FactReason.work_center_id.is_(None), FactReason.work_center_id == wc))
                    .order_by(FactReason.id).limit(6)).all()
    return [{"id": r.id, "name": r.name} for r in rs]


def _rerender(db, chat_id, mid, actor, name, out):
    st = get_state(db, chat_id)
    _open(db, chat_id, mid, actor, name, st.get("view") or {"s": "list"}, out)


# ------------------------------------------------------------------ запись факта
def _write(db, chat_id, actor, task: dict, qty: float):
    today = tenant_today(db, actor.tenant_id)
    f, old = facts_service.enter(db, actor, fact_date=today, order_line_id=task["order_line_id"],
                                 operation_id=task["operation_id"], worker_id=task["worker_id"], qty=qty,
                                 today=today, source="telegram")
    st = get_state(db, chat_id)
    st.pop("await", None); st.pop("confirm", None)
    st["last"] = {"fact_id": f.id, "previous": old, "label": _label(task)}
    put_state(db, chat_id, st)
    return f, old


# ------------------------------------------------------------------ нажатия кнопок
def _on_callback(db: Session, cq: dict, chat_id: int, out: list, link: Optional[TelegramLink]):
    mid = (cq.get("message") or {}).get("message_id")
    toast: Optional[str] = None
    try:
        if link is None:
            return say(out, chat_id, "Этот чат не привязан к Plan 24. Отправьте /start КОД.")
        actor, name = resolve_actor(db, link)
        if actor is None:
            return say(out, chat_id, "Доступ закрыт. Обратитесь к руководителю.")
        kind, _, rest = (cq.get("data") or "").partition(":")
        try:
            toast = _route(db, chat_id, mid, actor, name, link, kind, rest, out)
        except FactError as e:
            toast = None
            say(out, chat_id, f"Не получилось: {e.detail}")
        except HTTPException as e:                                       # get_or_404 и т. п.
            say(out, chat_id, "Не нашёл такую запись. Обновите список: /tasks" if e.status_code == 404 else f"Не получилось: {e.detail}")
        except ValueError:
            say(out, chat_id, "Не понял кнопку. Обновите список: /tasks")
    finally:
        answer(out, cq["id"], toast)


def _route(db, chat_id, mid, actor, name, link, kind: str, rest: str, out: list) -> Optional[str]:
    role = _role(actor)
    gone = "Это задание уже недоступно. Обновите список: /tasks"
    if kind == "l":
        wid = int(rest or 0)
        return _open(db, chat_id, mid, actor, name, {"s": "people" if (role == "foreman" and not wid) else "list", "page": 0, "wid": wid or None}, out)
    if kind == "p":
        page, _, wid = rest.partition(":")
        return _open(db, chat_id, mid, actor, name, {"s": "list", "page": int(page or 0), "wid": int(wid or 0) or None}, out)
    if kind == "w" and role == "foreman":
        return _open(db, chat_id, mid, actor, name, {"s": "list", "page": 0, "wid": int(rest)}, out)
    if kind == "k":
        if _find_task(db, actor, rest) is None:
            return say(out, chat_id, gone)
        return _open(db, chat_id, mid, actor, name, {"s": "card", "key": rest}, out)
    if kind in ("d", "a", "z"):
        key, _, delta = rest.rpartition(":") if kind == "a" else (rest, "", "")
        t = _find_task(db, actor, key)
        if t is None:
            return say(out, chat_id, gone)
        qty = t["plan_qty"] if kind == "d" else (0 if kind == "z" else min((t["fact_qty"] or 0) + float(delta or 0), MAX_QTY))
        f, _ = _write(db, chat_id, actor, t, qty)
        _rerender(db, chat_id, mid, actor, name, out)
        return f"Записано: {fnum(qty)}" + (". Бригадир подтвердит" if f.status == "submitted" else "")
    if kind == "n":
        t = _find_task(db, actor, rest)
        if t is None:
            return say(out, chat_id, gone)
        st = get_state(db, chat_id)
        st["await"] = {"key": rest, "label": _label(t), "plan": t["plan_qty"]}; st.pop("confirm", None)
        put_state(db, chat_id, st)
        return say(out, chat_id, f"{_label(t)}\nСколько сделано? Отправьте число (например 12 или 7,5).", kb([[("✖ Отмена", "x")]]))
    if kind == "y":
        st = get_state(db, chat_id); c = st.pop("confirm", None); put_state(db, chat_id, st)
        if not c:
            return say(out, chat_id, "Нечего подтверждать.")
        t = _find_task(db, actor, c["key"])
        if t is None:
            return say(out, chat_id, gone)
        f, _ = _write(db, chat_id, actor, t, c["qty"])
        _open(db, chat_id, mid, actor, name, {"s": "card", "key": c["key"]}, out)
        return f"Записано: {fnum(c['qty'])}" + (". Бригадир подтвердит" if f.status == "submitted" else "")
    if kind == "x":
        st = get_state(db, chat_id); st.pop("confirm", None); st.pop("await", None); put_state(db, chat_id, st)
        return show(out, chat_id, mid, "Отменено, ничего не записано.")
    if kind == "r":
        fid, _, rid = rest.partition(":")
        f = facts_service.own_fact(db, actor, int(fid))
        if int(rid):
            facts_service.set_reason(db, actor, f, int(rid))
        else:
            st = get_state(db, chat_id); st["nr"] = (st.get("nr", []) + [f.id])[-30:]; put_state(db, chat_id, st)
        events.alert_shortfall(db, f, out, skip_user_id=actor.id if role == "foreman" else None)
        _rerender(db, chat_id, mid, actor, name, out)
        return "Причина сохранена" if int(rid) else "Хорошо, без причины"
    if kind == "u":
        msg = _undo(db, chat_id, actor, out, expect=int(rest or 0), quiet=True)
        _rerender(db, chat_id, mid, actor, name, out)
        return msg
    if kind == "c":
        return _confirm(db, chat_id, actor, rest, out)
    if kind == "cd":
        return _closeday_ask(db, chat_id, mid, actor, int(rest or 0), out)
    if kind == "cdy":
        return _closeday_do(db, chat_id, mid, actor, name, int(rest or 0), out)
    if kind == "rem":
        return _remind(db, chat_id, mid, actor, out)
    if kind == "pb":
        return _problem_type(db, chat_id, mid, rest, out)
    if kind == "pn":
        return _problem_send(db, chat_id, actor, get_state(db, chat_id), "", None, out)
    if kind == "pc":
        _clear_pending(db, chat_id)
        return show(out, chat_id, mid, "Отменено.")
    if kind == "ab":
        return _absent_reason(chat_id, mid, rest, out, worker_id=0)
    if kind == "abr":
        p, _, r = rest.partition(":")
        return _absent_create(db, chat_id, mid, actor, actor.worker_id, p, r, out)
    if kind == "fa" and role == "foreman":
        return _absent_period(db, chat_id, mid, actor, int(rest), out)
    if kind == "fp" and role == "foreman":
        wid, _, p = rest.partition(":")
        return _absent_reason(chat_id, mid, p, out, worker_id=int(wid))
    if kind == "fr" and role == "foreman":
        wid, p, r = rest.split(":")
        return _absent_create(db, chat_id, mid, actor, int(wid), p, r, out)
    if kind == "rp" and role == "foreman":
        return _replan(db, chat_id, mid, actor, out)
    if kind == "ak" and role == "foreman":
        return _ack_problem(db, chat_id, mid, actor, name, int(rest), out)
    if kind == "s":
        return _settings(db, chat_id, mid, link, actor, out, op=rest)
    return None


# ------------------------------------------------------------------ ввод числа
def _number(db, chat_id, actor, st: dict, text: str, out: list):
    q = parse_qty(text)
    if q is None:
        return say(out, chat_id, "Нужно число от 0, например 12 или 7,5. Отмена: /cancel")
    aw = st.pop("await")
    st["confirm"] = {"key": aw["key"], "qty": q, "label": aw["label"]}
    put_state(db, chat_id, st)
    say(out, chat_id, f"Записать {fnum(q)}?\n{aw['label']}" + (f"\n(план {fnum(aw['plan'])})" if aw.get("plan") else ""),
        kb([[("✔ Да, записать", "y"), ("✖ Нет", "x")]]))


def _undo(db, chat_id, actor, out: list, expect: Optional[int] = None, quiet: bool = False):
    """quiet=True (из кнопки): вернуть текст для всплывающей подсказки, не присылая отдельное сообщение."""
    def reply(m):
        if quiet:
            return m
        say(out, chat_id, m)
    st = get_state(db, chat_id)
    last = st.get("last")
    if not last or (expect and last["fact_id"] != expect):
        return reply("Отменять нечего: возвращается только самый последний ввод.")
    try:
        f = facts_service.own_fact(db, actor, last["fact_id"])
        if last["previous"] is None:
            facts_service.remove(db, actor, f)
            msg = f"↩ Ввод отменён: {last['label']}"
        else:
            today = tenant_today(db, actor.tenant_id)
            facts_service.enter(db, actor, fact_date=f.fact_date, order_line_id=f.order_line_id, operation_id=f.operation_id,
                                worker_id=f.worker_id, qty=last["previous"], today=today, source="telegram")
            msg = f"↩ Возвращено прежнее значение: {last['label']} — {fnum(last['previous'])}"
    except HTTPException as e:
        return reply(f"Не получилось отменить: {e.detail}")
    st.pop("last", None); put_state(db, chat_id, st)
    return reply(msg)


# ------------------------------------------------------------------ закрыть день по плану
def _closeday_ask(db, chat_id, mid, actor, wid: int, out: list):
    role = _role(actor)
    rows = _rows(db, actor, wid or None) if role == "foreman" else _rows(db, actor)
    pend = _pending(rows)
    if not pend:
        return show(out, chat_id, mid, "Заданий без факта нет — на сегодня всё внесено ✔", kb([[("📋 Задания", "l")]]))
    who = f" у работника {pend[0]['worker']}" if (role == "foreman" and wid) else ""
    lines = [f"Внести по плану {len(pend)} {'задание' if len(pend) == 1 else 'заданий'}{who}?"]
    for t in pend[:10]:
        lines.append(f"• {(t['worker'] + ' · ') if role == 'foreman' and not wid else ''}{t['operation']} · {t['product']} · {t['order']} — {fnum(t['plan_qty'])}")
    if len(pend) > 10:
        lines.append(f"… и ещё {len(pend) - 10}")
    lines.append("Внесётся ровно плановое количество. Если было иначе — откройте задание и поправьте.")
    show(out, chat_id, mid, "\n".join(lines), kb([[("✔ Да, внести по плану", f"cdy:{wid}"), ("✖ Нет", "x")]]))


def _closeday_do(db, chat_id, mid, actor, name, wid: int, out: list):
    role = _role(actor)
    pend = _pending(_rows(db, actor, wid or None) if role == "foreman" else _rows(db, actor))
    ok, fails = 0, []
    for t in pend:
        try:
            _write(db, chat_id, actor, t, t["plan_qty"])
            ok += 1
        except FactError as e:
            fails.append(f"{t['operation']}: {e.detail}")
    st = get_state(db, chat_id); st.pop("last", None); put_state(db, chat_id, st)            # групповую запись одной кнопкой не откатить
    text = f"✔ Внесено по плану: {ok}." + ("" if not fails else "\nНе внесено:\n" + "\n".join(fails[:5]))
    show(out, chat_id, mid, text, kb([[("📋 Задания", "l")]]))
    return f"Внесено: {ok}"


# ------------------------------------------------------------------ бригадир: напомнить не внёсшим
def _remind(db, chat_id, mid, actor, out: list):
    if _role(actor) != "foreman":
        return None
    st = get_state(db, chat_id)
    last = st.get("nudge")
    if last:
        ago = utcnow() - datetime.fromisoformat(last)
        if ago < NUDGE_EVERY:
            return f"Напоминали {max(1, int(ago.total_seconds() // 60))} мин назад — подождите."
    pend = _pending(_rows(db, actor))
    if not pend:
        return "У всех факт внесён ✔"
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
    st["nudge"] = utcnow().isoformat(); put_state(db, chat_id, st)
    text = f"🔔 Напоминание отправлено: {sent} из {len(by)}."
    if nolink:
        text += "\nНет Telegram у: " + ", ".join(nolink[:10]) + " — скажите лично."
    say(out, chat_id, text)
    return "Отправлено"


# ------------------------------------------------------------------ что завтра
def _tomorrow(db, chat_id, actor, name, out: list):
    today = tenant_today(db, actor.tenant_id)
    role = _role(actor)
    for n in range(1, 8):
        d = today + timedelta(days=n)
        rows = [t for t in _rows(db, actor, None, d) if t["plan_qty"]]
        if rows:
            break
    else:
        return say(out, chat_id, "На ближайшую неделю заданий в плане нет.", menu_keyboard(role))
    head = f"Завтра, {fday(d)}:" if n == 1 else f"Завтра ({fday(today + timedelta(days=1))}) заданий нет. Ближайший день с заданиями — {fday(d)}:"
    lines = [head]
    if role == "foreman":
        by: dict = {}
        for t in rows:
            by.setdefault(t["worker"], 0)
            by[t["worker"]] += 1
        lines += [f"▸ {w} — заданий: {c}" for w, c in by.items()]
    else:
        lines += [f"• {t['operation']} · {t['product']} · {t['order']} — {fnum(t['plan_qty'])}" for t in rows[:15]]
    say(out, chat_id, "\n".join(lines))


# ------------------------------------------------------------------ сообщить о проблеме (сборщик → бригадир)
def _problem_start(db, chat_id, actor, out: list):
    if _role(actor) == "foreman":
        return say(out, chat_id, "Эту кнопку используют сборщики. Вы получаете их сообщения здесь же.", menu_keyboard("foreman"))
    rows = [[(v, f"pb:{k}") for k, v in list(PROBLEMS.items())[i:i + 2]] for i in range(0, len(PROBLEMS), 2)]
    say(out, chat_id, "Что случилось?", kb(rows + [[("✖ Отмена", "pc")]]))


def _problem_type(db, chat_id, mid, ptype: str, out: list):
    if ptype not in PROBLEMS:
        return None
    st = get_state(db, chat_id); st["problem"] = {"type": ptype}; put_state(db, chat_id, st)
    show(out, chat_id, mid, f"{PROBLEMS[ptype]}\nОпишите коротко текстом или пришлите фото (можно с подписью). Бригадир получит это сразу.",
         kb([[("Отправить без деталей", "pn")], [("✖ Отмена", "pc")]]))


def _problem_message(db, chat_id, msg, actor, st: dict, out: list):
    text = (msg.get("text") or msg.get("caption") or "").strip()
    has_media = any(k in msg for k in MEDIA)
    if not text and not has_media:
        return say(out, chat_id, "Напишите текстом или пришлите фото. Отмена: /cancel")
    _problem_send(db, chat_id, actor, st, text, msg.get("message_id") if has_media else None, out)


def _problem_send(db, chat_id, actor, st: dict, comment: str, media_message_id: Optional[int], out: list):
    p = st.get("problem")
    if not p or _role(actor) != "worker":
        return None
    w = db.get(Worker, actor.worker_id)
    links = events.foreman_links(db, actor.tenant_id, w.work_center_id, only_alerts=False)       # о проблеме сообщаем всегда
    st.pop("problem", None); put_state(db, chat_id, st)
    audit.log(db, actor, "problem", w.id, "report", {"type": p["type"], "media": media_message_id is not None, "comment": comment[:200]})
    if not links:
        return say(out, chat_id, "Бригадир участка пока не подключён к Telegram — позвоните ему или скажите лично.", menu_keyboard("worker"))
    tasks = [t for t in _rows(db, actor) if t["plan_qty"]][:4]
    text = f"⚠ Проблема: {PROBLEMS[p['type']]}\nОт: {w.name}" + (f"\n{comment}" if comment else "") + \
        ("\nСегодня у него: " + "; ".join(f"{t['operation']} · {t['order']}" for t in tasks) if tasks else "")
    for l in links:
        say(out, l.chat_id, text, kb([[("👍 Принял", f"ak:{w.id}"), (f"👤 {w.name[:24]}", f"w:{w.id}")]]))
        if media_message_id:
            copy_message(out, l.chat_id, chat_id, media_message_id)
    say(out, chat_id, f"Передал бригадиру ({len(links)}). Если срочно — позвоните.", menu_keyboard("worker"))


def _ack_problem(db, chat_id, mid, actor, name, worker_id: int, out: list):
    w = db.get(Worker, worker_id)
    if w is None or w.tenant_id != actor.tenant_id or w.work_center_id != actor.work_center_id:
        return "Это работник другого участка"
    l = events.worker_link(db, actor.tenant_id, worker_id)
    if l:
        say(out, l.chat_id, f"👍 {name} принял ваше сообщение и в курсе.")
    return "Работник уведомлён" if l else "Принято (у работника нет Telegram)"


# ------------------------------------------------------------------ отсутствие (сборщик «не выйду», бригадир «отметить»)
def _absent_start(db, chat_id, actor, out: list):
    if _role(actor) == "foreman":
        ws = list(db.scalars(select(Worker).where(Worker.tenant_id == actor.tenant_id, Worker.work_center_id == actor.work_center_id)
                             .order_by(Worker.name).limit(40)))
        if not ws:
            return say(out, chat_id, "На вашем участке нет работников.", menu_keyboard("foreman"))
        btns = [(w.name[:26], f"fa:{w.id}") for w in ws]
        return say(out, chat_id, "Кто не выйдет?", kb([btns[i:i + 2] for i in range(0, len(btns), 2)]))
    say(out, chat_id, "Когда вы не сможете выйти?", kb([[(v.capitalize(), f"ab:{k}") for k, v in list(ABS_PERIODS.items())[i:i + 2]] for i in range(0, 4, 2)]
                                                       + [[("✖ Отмена", "pc")]]))


def _absent_period(db, chat_id, mid, actor, worker_id: int, out: list):
    w = db.get(Worker, worker_id)
    if w is None or w.tenant_id != actor.tenant_id or w.work_center_id != actor.work_center_id:
        return "Это работник другого участка"
    show(out, chat_id, mid, f"{w.name}: на какой срок?", kb([[(v.capitalize(), f"fp:{worker_id}:{k}") for k, v in list(ABS_PERIODS.items())[i:i + 2]] for i in range(0, 4, 2)]))


def _absent_reason(chat_id, mid, period: str, out: list, worker_id: int):
    if period not in ABS_PERIODS:
        return None
    pre = f"fr:{worker_id}:{period}:" if worker_id else f"abr:{period}:"
    rs = list(ABS_REASONS.items())
    show(out, chat_id, mid, f"Причина ({ABS_PERIODS[period]})?", kb([[(v, pre + k) for k, v in rs[i:i + 2]] for i in range(0, len(rs), 2)]))


def _absent_create(db, chat_id, mid, actor, worker_id: int, period: str, reason: str, out: list):
    if period not in ABS_PERIODS or reason not in ABS_REASONS:
        return None
    w = db.get(Worker, worker_id)
    role = _role(actor)
    if w is None or w.tenant_id != actor.tenant_id or (role == "worker" and w.id != actor.worker_id) \
            or (role == "foreman" and w.work_center_id != actor.work_center_id):
        return "Нет доступа к этому работнику"
    today = tenant_today(db, actor.tenant_id)
    d1, d2 = {"t": (today, today), "m": (today + timedelta(days=1),) * 2, "tm": (today, today + timedelta(days=1)),
              "w7": (today, today + timedelta(days=6))}[period]
    a = WorkerAbsence(tenant_id=actor.tenant_id, worker_id=w.id, date_from=d1, date_to=d2, reason=ABS_REASONS[reason])
    db.add(a)
    db.flush()
    audit.log(db, actor, "absence", a.id, "create", {"worker": w.name, "from": d1, "to": d2, "reason": a.reason, "source": "telegram"})
    when = ABS_PERIODS[period]
    if role == "worker":
        show(out, chat_id, mid, f"Записал: вы не выйдете {when} ({ABS_REASONS[reason].lower()}). Бригадир предупреждён. Выздоравливайте!" if reason == "ill"
             else f"Записал: вы не выйдете {when} ({ABS_REASONS[reason].lower()}). Бригадир предупреждён.")
        for l in events.foreman_links(db, actor.tenant_id, w.work_center_id, only_alerts=False):
            say(out, l.chat_id, f"🤒 {w.name}: не выйдет {when} ({ABS_REASONS[reason].lower()}).\nЗадания этого работника в плане остались — перепланируйте участок.",
                kb([[("🔄 Перепланировать участок", "rp"), ("Позже", "x")]]))
    else:
        show(out, chat_id, mid, f"✔ Отмечено: {w.name} не выйдет {when} ({ABS_REASONS[reason].lower()}).\nПерепланировать участок сейчас?",
             kb([[("🔄 Перепланировать участок", "rp"), ("Позже", "x")]]))
        l = events.worker_link(db, actor.tenant_id, w.id)
        if l:
            say(out, l.chat_id, f"Бригадир отметил ваше отсутствие: {when} ({ABS_REASONS[reason].lower()}).")
    return "Записано"


# ------------------------------------------------------------------ перепланирование участка из бота
def _replan(db, chat_id, mid, actor, out: list):
    today = tenant_today(db, actor.tenant_id)
    track = events.enabled()
    before = events.plan_state(db, actor.tenant_id, [today, today + timedelta(days=1)]) if track else {}
    try:
        with dbm.tenant_plan_lock(actor.tenant_id):
            p = planning.run_and_save(db, actor, today, {"scope_center_id": actor.work_center_id})
            audit.log(db, actor, "plan", p.id, "replan", {**p.params, "source": "telegram"})
    except planning.NoActivePlan:
        return show(out, chat_id, mid, "Сначала нужен общий план организации — его строит руководитель проекта.")
    if track:
        events.notify_plan_changes(db, actor.tenant_id, before, events.plan_state(db, actor.tenant_id, [today, today + timedelta(days=1)]),
                                   out, today, who="Бригадир")
    rows = [t for t in _rows(db, actor) if t["plan_qty"]]
    text = f"✔ План участка пересчитан.\nСегодня: заданий — {len(rows)}, работников — {len({t['worker_id'] for t in rows})}."
    if p.warnings:
        text += f"\nПредупреждений планировщика: {len(p.warnings)} (подробно — в веб-версии)."
    show(out, chat_id, mid, text, kb([[("👥 По людям", "l")]]))
    return "План пересчитан"


# ------------------------------------------------------------------ настройки уведомлений
def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _shift(at: str, delta: int, lo: int, hi: int) -> str:
    h, m = at.split(":")
    return _hhmm(max(lo, min(hi, int(h) * 60 + int(m) + delta)))


def _settings(db, chat_id, mid, link, actor, out: list, op: str = ""):
    s = events.link_settings(link)
    new = dict(link.settings or {})
    if op == "m-" or op == "m+":
        new["m_at"] = _shift(s["m_at"], -30 if op == "m-" else 30, 5 * 60, 11 * 60)
    elif op == "e-" or op == "e+":
        new["e_at"] = _shift(s["e_at"], -30 if op == "e-" else 30, 14 * 60, 21 * 60)
    elif op == "mt":
        new["m_on"] = not s["m_on"]
    elif op == "et":
        new["e_on"] = not s["e_on"]
    elif op == "al":
        new["alerts"] = not s["alerts"]
    if op:
        link.settings = new
        db.flush()
        s = events.link_settings(link)
    foreman = _role(actor) == "foreman"
    text = "\n".join([
        "⚙ Настройки уведомлений",
        f"🌅 Утро: {s['m_at'] if s['m_on'] else 'выкл'} — " + ("сводка по участку" if foreman else "задания на день"),
        f"🌆 Вечер: {s['e_at'] if s['e_on'] else 'выкл'} — " + ("кто не внёс факт" if foreman else "напоминание внести факт"),
        f"🔔 Оповещения: {'вкл' if s['alerts'] else 'выкл'} — " + ("отклонения от плана на участке" if foreman else "изменения в моём плане"),
        "Время — по часовому поясу организации. Сообщения приходят только в рабочие дни, когда есть задания."
        + ("\nО проблемах и отсутствии работников бригадир получает сообщения всегда." if foreman else "")])
    rows = [[("🌅 −30 мин", "s:m-"), ("🌅 +30 мин", "s:m+"), ("🌅 выкл" if s["m_on"] else "🌅 вкл", "s:mt")],
            [("🌆 −30 мин", "s:e-"), ("🌆 +30 мин", "s:e+"), ("🌆 выкл" if s["e_on"] else "🌆 вкл", "s:et")],
            [("🔔 выключить оповещения" if s["alerts"] else "🔔 включить оповещения", "s:al")]]
    show(out, chat_id, mid, text, kb(rows))


# ------------------------------------------------------------------ очередь подтверждений бригадира (FR-22a)
def _queue_rows(db, actor):
    ws = {w.id: w.name for w in db.scalars(select(Worker).where(Worker.tenant_id == actor.tenant_id,
                                                                  Worker.work_center_id == actor.work_center_id))}
    fs = db.scalars(select(Fact).where(Fact.tenant_id == actor.tenant_id, Fact.status == "submitted",
                                       Fact.deleted_at.is_(None), Fact.worker_id.in_(ws or [0])).order_by(Fact.id).limit(30)).all()
    return fs, ws


def _queue(db, chat_id, actor, out: list):
    if getattr(actor, "role", "") != "foreman":
        return say(out, chat_id, "Очередь подтверждения доступна бригадиру.")
    fs, ws = _queue_rows(db, actor)
    if not fs:
        return say(out, chat_id, "Неподтверждённого факта нет.")
    lines = ["Ждёт подтверждения:"] + [f"{i}. {ws[f.worker_id]} — {fnum(f.qty)} ({f.fact_date.day:02d}.{f.fact_date.month:02d})" for i, f in enumerate(fs, 1)]
    rows = [[(f"✔ №{i}", f"c:{f.id}") for i, f in list(enumerate(fs, 1))[j:j + 4]] for j in range(0, len(fs), 4)]
    rows.append([("✔ Подтвердить все", "c:all")])
    say(out, chat_id, "\n".join(lines), kb(rows))


def _confirm(db, chat_id, actor, rest: str, out: list):
    if getattr(actor, "role", "") != "foreman":
        return say(out, chat_id, "Подтверждать факт может бригадир.")
    if rest == "all":
        fs, _ = _queue_rows(db, actor)
        for f in fs:
            facts_service.confirm(db, actor, f)
        return say(out, chat_id, f"Подтверждено: {len(fs)}.")
    f = facts_service.own_fact(db, actor, int(rest))
    facts_service.confirm(db, actor, f)
    say(out, chat_id, f"✔ Подтверждено: {fnum(f.qty)}.")
