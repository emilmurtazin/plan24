"""Регулярные отчёты в группы Telegram (администратор настраивает: группа, время по Москве, метрики, участки).

Каталог метрик (`METRICS`), сборка текста (`compose`), отправка (`send`) и запуск по расписанию (`run_due`, его вызывает
`python -m plan24.telegram scheduler`). Каждый отчёт отправляется не чаще раза за запланированный день (NotificationLog);
при временном сбое Telegram отправка повторяется каждые 5 минут в течение 3 часов, при постоянной ошибке (бота нет в группе,
группа не найдена) — не повторяется, причина видна администратору в списке отчётов.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import dashboard, db as dbm, reports_service
from .models import DayPlanSnapshot, NotificationLog, ReportSchedule, Tenant, User, WorkCenter, utcnow
from .telegram import client as tgclient
from .telegram.core import flat_tasks
from .telegram.ui import fnum
from .timeutil import today_in

log = logging.getLogger("plan24.reports")

MSK = ZoneInfo("Europe/Moscow")
WINDOW = timedelta(hours=3)          # «догнать» пропущенную отправку (сервис был выключен) не позже чем через 3 часа
RETRY_EVERY = timedelta(minutes=5)
MAX_PER_TENANT = 20
CHUNK = 3800                         # лимит сообщения Telegram — 4096 знаков (клиент режет по 4000)
WD_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

# ключ → (название, пояснение). Порядок — порядок в форме и в отчёте.
METRICS = [
    ("progress", "Выполнение", "Общий процент выполнения заказов, сколько заказов в работе и готово"),
    ("order_statuses", "Статусы заказов", "Сколько заказов в работе, в риске опоздания, без плана, готово"),
    ("risk_orders", "Заказы с риском опоздания", "До 5 заказов: срок и прогнозная дата завершения"),
    ("due_soon", "Ближайшие сроки", "До 5 незавершённых заказов с ближайшим сроком"),
    ("today_plan", "План на сегодня", "Сколько работников получили задания, сколько заданий и какой объём"),
    ("today_fact", "Факт за сегодня", "План и внесённый факт на момент отправки, кто ещё не внёс факт"),
    ("prev_day", "Итоги предыдущего рабочего дня", "План, факт и процент выполнения плана за последний день с планом"),
    ("missing_fact", "Не внесён факт", "Задания прошедших дней, по которым нет факта"),
    ("adherence", "Выполнение плана за 4 недели", "Доля выполненного плана за последние 4 недели и динамика"),
    ("load", "Загрузка", "Средняя загрузка на 3 месяца вперёд, по участкам — ближайшая неделя и пик"),
    ("workers", "Работники", "Всего, с заданиями сегодня, не работают сегодня; по участкам"),
    ("resources", "Потребность в ресурсах", "Где нужно добавить людей, недели перегрузки"),
    ("reasons", "Причины отклонений", "Самые частые причины за 90 дней"),
]
METRIC_KEYS = [m[0] for m in METRICS]
METRIC_TITLE = {k: t for k, t, _ in METRICS}
DASH_METRICS = {"progress", "order_statuses", "risk_orders", "due_soon", "missing_fact", "adherence", "load", "workers",
                "resources", "reasons"}
DEFAULT_METRICS = ["progress", "order_statuses", "risk_orders", "today_fact"]


def catalog() -> list:
    return [{"key": k, "title": t, "hint": h} for k, t, h in METRICS]


# ------------------------------------------------------------------ проверка полей
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
CHAT_ID_RE = re.compile(r"^-?\d{5,20}$")
CHAT_NAME_RE = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$")


def normalize_chat(raw: str) -> str:
    """Номер группы (-1001234567890) или @имя; ссылки вида t.me/имя приводятся к @имя. Пустая строка — ошибка."""
    s = (raw or "").strip()
    m = re.fullmatch(r"(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{4,31})/?", s)
    if m:
        s = "@" + m.group(1)
    if CHAT_ID_RE.fullmatch(s):
        return s
    if CHAT_NAME_RE.fullmatch(s):
        return s
    raise ValueError("Укажите номер группы (например, -1001234567890) или @имя публичной группы")


def chat_target(ref: str):
    return int(ref) if CHAT_ID_RE.fullmatch(ref) else ref


# ------------------------------------------------------------------ данные для отчёта
@dataclass
class Ctx:
    """Кто «смотрит» отчёт: вся организация (как администратор) или один участок (как его бригадир)."""
    tenant_id: int
    role: str = "owner"
    work_center_id: Optional[int] = None
    id: Optional[int] = None
    worker_id: Optional[int] = None


def _d(iso: Optional[str]) -> str:
    return f"{iso[8:10]}.{iso[5:7]}" if iso else "—"


def _wd(d: date) -> str:
    return f"{WD_RU[d.weekday()]} {d.strftime('%d.%m.%Y')}"


def _pct(x) -> str:
    return "—" if x is None else f"{fnum(round(x, 1))}%"


def _bullets(lines: list, title: str) -> list:
    return [f"▸ {title}"] + [f"   {l}" for l in lines]


def _day_totals(rep: dict) -> dict:
    rows = [t for t in flat_tasks(rep) if t["plan_qty"]]
    plan = sum(t["plan_qty"] for t in rows)
    fact = sum(t["fact_qty"] or 0 for t in rows)
    capped = sum(min(t["fact_qty"] or 0, t["plan_qty"]) for t in rows)
    missing = sorted({t["worker"] for t in rows if t["fact_qty"] is None})
    short = sum(1 for t in rows if t["fact_qty"] is not None and t["fact_qty"] < t["plan_qty"])
    return {"rows": rows, "plan": plan, "fact": fact, "pct": round(capped / plan * 100, 1) if plan else None,
            "workers": len({t["worker_id"] for t in rows}), "tasks": len(rows), "missing": missing, "short": short,
            "all_workers": len({t["worker_id"] for t in rows if t["fact_qty"] is not None})}


def _block(db: Session, ctx: Ctx, metrics: list, today: date) -> list:
    """Строки отчёта по одному контексту (организация или участок) для выбранных метрик."""
    dash = dashboard.build(db, ctx, today) if DASH_METRICS & set(metrics) else None     # type: ignore[arg-type]
    day_today = _day_totals(reports_service.day_report(db, ctx, today, today)) if {"today_plan", "today_fact"} & set(metrics) else None
    prev = None
    if "prev_day" in metrics:
        pd_ = db.scalar(select(func.max(DayPlanSnapshot.plan_date)).where(DayPlanSnapshot.tenant_id == ctx.tenant_id,
                                                                          DayPlanSnapshot.plan_date < today))
        prev = (pd_, _day_totals(reports_service.day_report(db, ctx, pd_, today))) if pd_ else (None, None)
    out: list = []
    for key in METRIC_KEYS:
        if key not in metrics:
            continue
        lines = _metric(key, dash, day_today, prev, ctx, today)
        if lines:
            out += lines + [""]
    return out[:-1] if out else out


def _metric(key: str, dash: Optional[dict], day: Optional[dict], prev, ctx: Ctx, today: date) -> list:
    title = METRIC_TITLE[key]
    if key in DASH_METRICS and dash is not None and dash.get("plan_id") is None and key not in ("workers", "missing_fact", "reasons"):
        return [f"▸ {title}: плана ещё нет — постройте его в разделе «Выполнение»"]
    if key == "progress":
        s = dash["summary"]
        return [f"▸ {title}: {_pct(s['progress_pct'])}  ·  заказов в работе {s['active']}, готово {s['done']}"]
    if key == "order_statuses":
        s = dash["summary"]
        return [f"▸ {title}: в работе {s['on_track']}  ·  риск опоздания {s['at_risk']}  ·  нет плана {s['no_plan']}  ·  готово {s['done']}"]
    if key == "risk_orders":
        risk = dash["risk"][:5]
        if not risk:
            return [f"▸ {title}: нет ✅"]
        return _bullets([f"• {r['id']} · {r['product']}: срок {_d(r.get('due'))}, прогноз {_d(r.get('forecast'))}" for r in risk],
                        f"{title} ({len(dash['risk'])})")
    if key == "due_soon":
        soon = dash["soonest"][:5]
        if not soon:
            return [f"▸ {title}: нет незавершённых заказов со сроком"]
        return _bullets([f"• {r['id']} · {r['product']}: срок {_d(r.get('due'))}, выполнено {_pct(r.get('progress'))}" for r in soon], title)
    if key == "today_plan":
        if not day["rows"]:
            return [f"▸ {title}: заданий нет"]
        return [f"▸ {title}: работников {day['workers']}  ·  заданий {day['tasks']}  ·  объём {fnum(day['plan'])}"]
    if key == "today_fact":
        if not day["rows"]:
            return [f"▸ {title}: заданий нет"]
        lines = [f"▸ {title}: {fnum(day['fact'])} из {fnum(day['plan'])}  ({_pct(day['pct'])})"]
        if day["missing"]:
            names = ", ".join(day["missing"][:8]) + ("…" if len(day["missing"]) > 8 else "")
            lines.append(f"   Факт ещё не внесён ({len(day['missing'])}): {names}")
        return lines
    if key == "prev_day":
        pd_, p = prev
        if pd_ is None or not p["rows"]:
            return [f"▸ {title}: данных нет"]
        lines = [f"▸ {title} ({_wd(pd_)}): план {fnum(p['plan'])}, факт {fnum(p['fact'])}  ({_pct(p['pct'])})"]
        if p["short"] or p["missing"]:
            lines.append(f"   Недовыполнено заданий: {p['short']}, без факта: {len(p['missing'])} чел.")
        return lines
    if key == "missing_fact":
        m = dash["missing_fact"]
        if not m["days"]:
            return [f"▸ {title}: за прошедшие дни всё внесено ✅"]
        return [f"▸ {title}: {m['tasks']} заданий за {m['days']} дн., начиная с {_d(m['first'])}"]
    if key == "adherence":
        a = dash["summary"]["adherence"]
        if a.get("percent") is None:
            return [f"▸ {title}: данных пока нет (история плана копится с начала работы)"]
        prev_s = "" if a.get("prev_percent") is None else f"  (4 недели ранее: {_pct(a['prev_percent'])})"
        return [f"▸ {title}: {_pct(a['percent'])}{prev_s}"]
    if key == "load":
        k = dash["kpi"]
        lines = [f"Среднее на 3 месяца: {_pct(k.get('load_3m'))}"]
        if ctx.work_center_id is None:
            for s in dash["load"]["series"]:
                vals = [v for v in s["values"] if v is not None]
                if vals:
                    lines.append(f"• {s['section']}: неделя {_pct(vals[0])}, пик {_pct(max(vals))}")
        else:
            for s in dash["load"]["series"]:
                vals = [v for v in s["values"] if v is not None]
                if vals:
                    lines.append(f"Ближайшая неделя {_pct(vals[0])}, пик {_pct(max(vals))}")
        return _bullets(lines, title)
    if key == "workers":
        a = dash["people_today"]["all"]
        lines = [f"▸ {title}: всего {a['total']}  ·  с заданиями сегодня {a['with_tasks']}  ·  не работают {a['off']}"]
        if ctx.work_center_id is None and len(dash["people_today"]["by_section"]) > 1:
            lines += [f"   • {s['section']}: {s['with_tasks']} из {s['total']}" for s in dash["people_today"]["by_section"]]
        return lines
    if key == "resources":
        secs = [s for s in dash["resources"]["sections"] if s["needs_more"]]
        if not secs:
            return [f"▸ {title}: дополнительных людей не требуется ✅"]
        lines = []
        for s in secs:
            parts = []
            if s["add_workers"]:
                parts.append(f"добавить {s['add_workers']} чел.")
            if s["qualification"]:
                parts.append("не хватает квалификации")
            if s["overload_weeks"]:
                parts.append(f"перегрузка недель: {s['overload_weeks']}")
            if s["late_ops"] and not parts:
                parts.append("есть операции, которые не успевают")
            lines.append(f"• {s['section']}: " + ", ".join(parts))
        return _bullets(lines, title)
    if key == "reasons":
        rs = dash["reasons"][:3]
        if not rs:
            return [f"▸ {title}: отклонений с причинами за 90 дней нет"]
        return _bullets([f"• {r['reason']} — {r['count']} раз, последний {_d(r['last_date'])}" for r in rs], title)
    return []


def compose(db: Session, tenant: Tenant, name: str, metrics: list, section_ids: list, now_utc: datetime) -> tuple:
    """(сообщения для отправки, сообщение об ошибке). Участки в section_ids, которых уже нет, пропускаются."""
    today = today_in(tenant.timezone)
    head = f"📊 {name}\n{tenant.name} · {_wd(today)}"
    body: list = []
    if section_ids:
        centers = list(db.scalars(select(WorkCenter).where(WorkCenter.tenant_id == tenant.id, WorkCenter.id.in_(section_ids)).order_by(WorkCenter.name)))
        if not centers:
            return [], "Выбранные участки удалены — выберите участки заново"
        for c in centers:
            body += [f"🏭 Участок «{c.name}»"] + (_block(db, Ctx(tenant.id, "foreman", c.id), metrics, today) or ["нет данных"]) + [""]
    else:
        body = _block(db, Ctx(tenant.id), metrics, today) + [""]
    text = "\n".join(body).rstrip()
    return _chunks(head, text), None


def _chunks(head: str, text: str) -> list:
    """Делит отчёт на сообщения не длиннее лимита Telegram, разрывая по пустым строкам (блокам метрик)."""
    blocks, cur, out = [b for b in text.split("\n\n")], head, []
    for b in blocks:
        if len(b) > CHUNK:
            b = b[:CHUNK - 1] + "…"
        if len(cur) + 2 + len(b) > CHUNK:
            out.append(cur)
            cur = b
        else:
            cur += "\n\n" + b
    out.append(cur)
    return out


# ------------------------------------------------------------------ отправка
class SendError(Exception):
    def __init__(self, text: str, permanent: bool):
        super().__init__(text)
        self.text, self.permanent = text, permanent


def explain_error(e: Exception) -> SendError:
    """Понятная причина сбоя. Постоянные ошибки (400/401/403/404) повторять бессмысленно."""
    s = str(e)
    m = re.search(r"sendMessage: (\d{3}) ?(.*)", s)
    if not m:
        return SendError("Нет связи с Telegram — отправка будет повторена", False)
    code, desc = int(m.group(1)), m.group(2).lower()
    if "upgraded to a supergroup" in desc:
        return SendError("Группа стала супергруппой и сменила номер — выполните /chatid в группе и укажите новый номер", True)
    if code == 400 and "chat not found" in desc:
        return SendError("Группа не найдена: проверьте номер и что бот добавлен в группу", True)
    if code in (403, 400) and ("kicked" in desc or "not a member" in desc or "forbidden" in desc or "can't" in desc):
        return SendError("Бот не состоит в группе или не может в ней писать — добавьте бота в группу", True)
    if code == 403:
        return SendError("Бот не состоит в группе или заблокирован — добавьте бота в группу", True)
    if code == 401:
        return SendError("Telegram не принял токен бота — обратитесь к администратору сервиса", True)
    if code in (400, 404):
        return SendError(f"Telegram отклонил отправку: {desc[:120]}", True)
    return SendError(f"Telegram временно недоступен ({code}) — отправка будет повторена", False)


def deliver(chat_ref: str, messages: list) -> None:
    """Отправляет сообщения в группу; при сбое — SendError."""
    c = tgclient.get_client()
    if c is None:
        raise SendError("Telegram-бот не настроен администратором сервиса", True)
    try:
        for m in messages:
            c.send_message(chat_target(chat_ref), m)
    except SendError:
        raise
    except Exception as e:                                     # noqa: BLE001
        raise explain_error(e) from None


def _record(rs: ReportSchedule, now: datetime, err: Optional[SendError]) -> None:
    rs.last_attempt_at = now
    if err is None:
        rs.last_status, rs.last_error, rs.last_sent_at = "ok", None, now
    else:
        rs.last_status, rs.last_error = "error", err.text[:300]


def send_now(db: Session, tenant: Tenant, rs: ReportSchedule) -> Optional[str]:
    """Немедленная отправка (кнопка «Отправить сейчас»). None — отправлено, иначе текст ошибки."""
    now = utcnow()
    msgs, err = compose(db, tenant, rs.name, rs.metrics, rs.section_ids, now)
    if err:
        _record(rs, now, SendError(err, True))
        return err
    try:
        deliver(rs.chat_ref, msgs)
    except SendError as e:
        _record(rs, now, e)
        return e.text
    _record(rs, now, None)
    return None


# ------------------------------------------------------------------ расписание
def due_date(rs: ReportSchedule, now_utc: datetime) -> Optional[date]:
    """Московская дата, за которую отчёт пора отправить сейчас (или None). Учитывает окно через полночь."""
    now = now_utc.astimezone(MSK) if now_utc.tzinfo else now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(MSK)
    h, m = map(int, rs.send_time.split(":"))
    for d in (now.date(), now.date() - timedelta(days=1)):
        at = datetime(d.year, d.month, d.day, h, m, tzinfo=MSK)
        if at <= now < at + WINDOW and d.weekday() in (rs.weekdays or []):
            return d
    return None


def _claim(db: Session, tid: int, rs_id: int, d: date) -> bool:
    from .telegram.events import first_time
    return first_time(db, tid, "report", f"rs{rs_id}", d)


def run_due(db: Session, now_utc: datetime) -> int:
    """Отправляет все отчёты, время которых наступило. Возвращает число отправленных. Коммитит сам (запись о попытке
    сохраняется до и после отправки, чтобы два планировщика не прислали отчёт дважды)."""
    dbm.auth_scope(db, True)
    tenant_ids = sorted(set(db.scalars(select(User.tenant_id))))          # организации читаем по таблице пользователей (только чтение)
    dbm.auth_scope(db, False)
    sent = 0
    for tid in tenant_ids:
        dbm.set_tenant(db, tid)
        tenant = db.get(Tenant, tid)
        if tenant is None or tenant.blocked_at is not None:
            continue
        for rs in list(db.scalars(select(ReportSchedule).where(ReportSchedule.tenant_id == tid, ReportSchedule.enabled.is_(True)).order_by(ReportSchedule.id))):
            d = due_date(rs, now_utc)
            if d is None:
                continue
            if rs.last_status == "error" and rs.last_attempt_at and utcnow() - rs.last_attempt_at < RETRY_EVERY:
                continue                                                  # временный сбой: повтор не чаще раза в 5 минут
            if not _claim(db, tid, rs.id, d):
                continue
            db.commit()                                                   # отметка «берусь» видна другим процессам до отправки
            try:
                msgs, err = compose(db, tenant, rs.name, rs.metrics, rs.section_ids, now_utc)
                if err:
                    raise SendError(err, True)
                deliver(rs.chat_ref, msgs)
                _record(rs, utcnow(), None)
                sent += 1
            except SendError as e:
                _record(rs, utcnow(), e)
                if not e.permanent:                                       # временный сбой — снимаем отметку, повторим
                    db.execute(delete(NotificationLog).where(NotificationLog.tenant_id == tid, NotificationLog.kind == "report",
                                                             NotificationLog.key == f"rs{rs.id}", NotificationLog.sent_on == d))
                log.error("report %s failed: %s", rs.id, e.text)
            except Exception as e:                                        # noqa: BLE001 — один отчёт не должен ронять остальные
                db.rollback()
                dbm.set_tenant(db, tid)
                rs = db.get(ReportSchedule, rs.id)
                if rs is not None:
                    _record(rs, utcnow(), SendError("Не удалось собрать отчёт — обратитесь в поддержку", True))
                log.error("report compose failed: %s", type(e).__name__)
            db.commit()
    dbm.set_tenant(db, None)
    return sent
