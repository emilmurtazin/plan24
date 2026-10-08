"""Обработка обновлений Telegram. Работает с «сырыми» update-словарями, без привязки к библиотеке:
так её можно проверять без сети, а вебхук и long polling используют один код.

Все изменения данных идут через facts_service — те же права и правила, что и в веб-интерфейсе.
Тексты сообщений и chat_id в логи не пишутся.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import db as dbm
from .. import facts_service, reports_service, security
from ..facts_service import Actor, FactError
from ..models import (Fact, FactReason, TelegramCode, TelegramLink, TelegramState, TelegramUpdate, Tenant, User,
                      Worker, utcnow)
from ..timeutil import today_in

log = logging.getLogger("plan24.telegram")
PAGE = 6
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
bind_limiter = security.RateLimiter("bind", 5, 900)      # неверные коды привязки: 5 за 15 минут на чат (общий для процессов)
MAX_QTY = 1_000_000


# ------------------------------------------------------------------ вывод
def say(out: list, chat_id: int, text: str, markup: Optional[dict] = None):
    out.append({"kind": "message", "chat_id": chat_id, "text": text, **({"markup": markup} if markup else {})})


def answer(out: list, cb_id: str, text: Optional[str] = None):
    out.append({"kind": "answer", "callback_id": cb_id, **({"text": text} if text else {})})


def kb(rows: list) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def fnum(x) -> str:
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def fday(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d} ({WD[d.weekday()]})"


def parse_qty(text: str) -> Optional[float]:
    t = (text or "").strip().replace(",", ".").replace(" ", "")
    if not re.fullmatch(r"\d{1,7}(\.\d{1,3})?", t):
        return None
    v = float(t)
    return v if 0 <= v <= MAX_QTY else None


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


# ------------------------------------------------------------------ личность
def resolve_actor(db: Session, link: TelegramLink):
    """Кто пишет: исполнитель без учётной записи (роль worker) или пользователь-бригадир. None — доступ закрыт."""
    if link.worker_id:
        w = db.get(Worker, link.worker_id)
        return (Actor(tenant_id=link.tenant_id, worker_id=w.id), w.name) if w and w.tenant_id == link.tenant_id else (None, "")
    u = db.get(User, link.user_id)
    return (u, u.name) if u and u.is_active and u.tenant_id == link.tenant_id and u.role == "foreman" else (None, "")


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

    if cmd in ("/start", "/help"):
        return _help(db, chat_id, actor, name, out)
    if cmd in ("/tasks", "/zadaniya") or text.lower() in ("задания", "план", "мои задания"):
        return _tasks(db, chat_id, actor, name, 0, out)
    if cmd == "/undo":
        return _undo(db, chat_id, actor, out)
    if cmd == "/cancel":
        st = get_state(db, chat_id); st.pop("await", None); st.pop("confirm", None); put_state(db, chat_id, st)
        return say(out, chat_id, "Отменено.")
    if cmd == "/queue":
        return _queue(db, chat_id, actor, out)

    st = get_state(db, chat_id)
    if st.get("await"):
        return _number(db, chat_id, actor, st, text, out)
    say(out, chat_id, "Не понял. Нажмите «Мои задания» или отправьте /tasks.", kb([[("Мои задания", "l")]]))


def _help(db, chat_id, actor, name, out):
    foreman = getattr(actor, "role", "") == "foreman"
    lines = [f"Здравствуйте, {name}!", "", "/tasks — задания на сегодня и ввод факта", "/undo — отменить последний ввод"]
    if foreman:
        lines.append("/queue — факт, ожидающий вашего подтверждения")
    lines.append("/cancel — отменить текущий ввод")
    say(out, chat_id, "\n".join(lines), kb([[("Мои задания", "l")]]))


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
    say(out, chat_id, f"Готово! Вы — {name}. Теперь здесь можно смотреть задания и вносить факт.\nПароль не нужен.",
        kb([[("Мои задания", "l")]]))


# ------------------------------------------------------------------ задания
def _flat_tasks(report: dict) -> list:
    rows = []
    for w in report["workers"]:
        for t in w["tasks"]:
            rows.append({**t, "worker_id": w["worker_id"], "worker": w["worker"]})
    return rows


def _tasks(db, chat_id, actor, name, page: int, out: list):
    today = tenant_today(db, actor.tenant_id)
    rows = _flat_tasks(reports_service.day_report(db, actor, today, today))
    if not rows:
        return say(out, chat_id, f"На сегодня, {fday(today)}, заданий нет.")
    foreman = getattr(actor, "role", "") == "foreman"
    pages = (len(rows) + PAGE - 1) // PAGE
    page = max(0, min(page, pages - 1))
    lines, buttons, last_w = [f"Задания на {fday(today)}" + ("" if foreman else f" — {name}")], [], None
    for i, t in enumerate(rows[page * PAGE:(page + 1) * PAGE], start=page * PAGE + 1):
        if foreman and t["worker"] != last_w:
            lines.append(f"\n▸ {t['worker']}"); last_w = t["worker"]
        done = f", сделано {fnum(t['fact_qty'])}" if t["fact_qty"] is not None else ""
        plan = f"план {fnum(t['plan_qty'])}" if t["plan_qty"] else "вне плана"
        flag = " ✔" if t["state"] == "done" else ""
        lines.append(f"{i}. {t['operation']} · {t['product']} · {t['order']} — {plan}{done}{flag}")
        key = f"{t['order_line_id']}:{t['operation_id']}:{t['worker_id']}"
        row = [(f"✏ №{i}", f"n:{key}")]
        if t["plan_qty"] and t["state"] != "done":
            row.insert(0, (f"✔ №{i} всё ({fnum(t['plan_qty'])})", f"d:{key}"))
        buttons.append(row)
    nav = []
    if page > 0:
        nav.append(("◀ Назад", f"p:{page - 1}"))
    if page < pages - 1:
        nav.append(("Дальше ▶", f"p:{page + 1}"))
    if nav:
        buttons.append(nav)
    say(out, chat_id, "\n".join(lines), kb(buttons))


def _find_task(db, actor, key: str):
    """Задание строго из отчёта актора: подделанные идентификаторы чужих задач не пройдут."""
    try:
        ol, op, wk = (int(x) for x in key.split(":"))
    except ValueError:
        return None
    today = tenant_today(db, actor.tenant_id)
    for t in _flat_tasks(reports_service.day_report(db, actor, today, today)):
        if (t["order_line_id"], t["operation_id"], t["worker_id"]) == (ol, op, wk):
            return t
    return None


# ------------------------------------------------------------------ нажатия кнопок
def _on_callback(db: Session, cq: dict, chat_id: int, out: list, link: Optional[TelegramLink]):
    answer(out, cq["id"])
    if link is None:
        return say(out, chat_id, "Этот чат не привязан к Plan 24. Отправьте /start КОД.")
    actor, name = resolve_actor(db, link)
    if actor is None:
        return say(out, chat_id, "Доступ закрыт. Обратитесь к руководителю.")
    kind, _, rest = (cq.get("data") or "").partition(":")
    today = tenant_today(db, actor.tenant_id)
    try:
        if kind == "l":
            return _tasks(db, chat_id, actor, name, 0, out)
        if kind == "p":
            return _tasks(db, chat_id, actor, name, int(rest or 0), out)
        if kind in ("d", "n"):
            t = _find_task(db, actor, rest)
            if t is None:
                return say(out, chat_id, "Это задание уже недоступно. Обновите список: /tasks")
            label = f"{t['worker']} · {t['operation']} · {t['product']} · {t['order']}"
            if kind == "d":
                return _save(db, chat_id, actor, t, t["plan_qty"], label, out)
            st = get_state(db, chat_id)
            st["await"] = {"key": rest, "label": label, "plan": t["plan_qty"]}; st.pop("confirm", None)
            put_state(db, chat_id, st)
            return say(out, chat_id, f"{label}\nСколько сделано? Отправьте число (например 12 или 7,5).\nОтмена: /cancel")
        if kind == "y":
            st = get_state(db, chat_id); c = st.pop("confirm", None); put_state(db, chat_id, st)
            if not c:
                return say(out, chat_id, "Нечего подтверждать.")
            t = _find_task(db, actor, c["key"])
            if t is None:
                return say(out, chat_id, "Это задание уже недоступно. Обновите список: /tasks")
            return _save(db, chat_id, actor, t, c["qty"], c["label"], out)
        if kind == "x":
            st = get_state(db, chat_id); st.pop("confirm", None); st.pop("await", None); put_state(db, chat_id, st)
            return say(out, chat_id, "Отменено, ничего не записано.")
        if kind == "r":
            fid, _, rid = rest.partition(":")
            f = facts_service.own_fact(db, actor, int(fid))
            if int(rid):
                facts_service.set_reason(db, actor, f, int(rid))
                return say(out, chat_id, "Причина сохранена.")
            return say(out, chat_id, "Хорошо, без причины.")
        if kind == "u":
            return _undo(db, chat_id, actor, out, expect=int(rest or 0))
        if kind == "c":
            return _confirm(db, chat_id, actor, rest, out)
    except FactError as e:
        return say(out, chat_id, f"Не получилось: {e.detail}")
    except HTTPException as e:                                       # get_or_404 и т. п.
        return say(out, chat_id, "Не нашёл такую запись. Обновите список: /tasks" if e.status_code == 404 else f"Не получилось: {e.detail}")
    except ValueError:
        return say(out, chat_id, "Не понял кнопку. Обновите список: /tasks")


# ------------------------------------------------------------------ ввод числа и запись
def _number(db, chat_id, actor, st: dict, text: str, out: list):
    q = parse_qty(text)
    if q is None:
        return say(out, chat_id, "Нужно число от 0, например 12 или 7,5. Отмена: /cancel")
    aw = st.pop("await")
    st["confirm"] = {"key": aw["key"], "qty": q, "label": aw["label"]}
    put_state(db, chat_id, st)
    say(out, chat_id, f"Записать {fnum(q)}?\n{aw['label']}" + (f"\n(план {fnum(aw['plan'])})" if aw.get("plan") else ""),
        kb([[("✔ Да, записать", "y"), ("✖ Нет", "x")]]))


def _save(db, chat_id, actor, task: dict, qty: float, label: str, out: list):
    today = tenant_today(db, actor.tenant_id)
    f, old = facts_service.enter(db, actor, fact_date=today, order_line_id=task["order_line_id"],
                                 operation_id=task["operation_id"], worker_id=task["worker_id"], qty=qty,
                                 today=today, source="telegram")
    st = get_state(db, chat_id)
    st.pop("await", None); st.pop("confirm", None)
    st["last"] = {"fact_id": f.id, "previous": old, "label": label}
    put_state(db, chat_id, st)
    text = f"✔ Записано: {label} — {fnum(qty)}" + (f" (было {fnum(old)})" if old is not None else "")
    if f.status == "submitted":
        text += "\nБригадир подтвердит."
    rows = [[("↩ Отменить", f"u:{f.id}")]]
    if task["plan_qty"] and qty < task["plan_qty"]:
        text += f"\nМеньше плана ({fnum(task['plan_qty'])}). Что помешало?"
        reasons = db.scalars(select(FactReason).where(FactReason.tenant_id == actor.tenant_id).order_by(FactReason.id).limit(6)).all()
        for i in range(0, len(reasons), 2):
            rows.append([(r.name[:28], f"r:{f.id}:{r.id}") for r in reasons[i:i + 2]])
        rows.append([("Без причины", f"r:{f.id}:0")])
    say(out, chat_id, text, kb(rows))


def _undo(db, chat_id, actor, out: list, expect: Optional[int] = None):
    st = get_state(db, chat_id)
    last = st.get("last")
    if not last or (expect and last["fact_id"] != expect):
        return say(out, chat_id, "Отменять нечего: возвращается только самый последний ввод.")
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
        return say(out, chat_id, f"Не получилось отменить: {e.detail}")
    st.pop("last", None); put_state(db, chat_id, st)
    say(out, chat_id, msg)


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
