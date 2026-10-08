"""Утренние задания и вечерние напоминания (ТЗ: «Утро: бот присылает задания. Вечер: напоминание внести факт»).

Запускается по расписанию (cron каждые 5–10 минут или `python -m plan24.telegram scheduler`).
Каждому — не чаще раза в сутки на вид уведомления (NotificationLog).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config, reports_service
from .. import db as dbm
from ..models import NotificationLog, TelegramLink, Tenant
from . import bot

WINDOW_HOURS = 3        # не «догонять» уведомление позже чем через 3 часа после его времени


def _at(s: str):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _in_window(local: datetime, at: str) -> bool:
    mins = local.hour * 60 + local.minute
    start = _at(at)
    return start <= mins < start + WINDOW_HOURS * 60


def _first_time(db: Session, tenant_id: int, kind: str, key: str, day) -> bool:
    try:
        with db.begin_nested():
            db.add(NotificationLog(tenant_id=tenant_id, kind=kind, key=key, sent_on=day))
            db.flush()
        return True
    except IntegrityError:
        return False


def run_due(db: Session, now_utc: datetime) -> list:
    """Возвращает список исходящих сообщений (для client.deliver). Время — по часовому поясу каждой организации."""
    cfg = config.telegram_settings()
    out: list = []
    dbm.auth_scope(db, True)                                     # список организаций с привязками — только чтение
    tenant_ids = sorted(set(db.scalars(select(TelegramLink.tenant_id))))
    dbm.auth_scope(db, False)
    for tid in tenant_ids:
        dbm.set_tenant(db, tid)                                  # дальше — данные одной организации
        tenant = db.get(Tenant, tid)
        local = now_utc.astimezone(ZoneInfo(tenant.timezone))
        day = local.date()
        for kind, at in (("morning", cfg["morning_at"]), ("evening", cfg["evening_at"])):
            if not _in_window(local, at):
                continue
            for link in db.scalars(select(TelegramLink).where(TelegramLink.tenant_id == tid).order_by(TelegramLink.id)):
                actor, name = bot.resolve_actor(db, link)
                if actor is None:
                    continue
                msg = _compose(db, actor, name, kind, day)
                if msg and _first_time(db, tid, kind, str(link.id), day):
                    bot.say(out, link.chat_id, msg, bot.kb([[("Мои задания", "l")]]))
    return out


def _compose(db: Session, actor, name: str, kind: str, day) -> str | None:
    rows = bot._flat_tasks(reports_service.day_report(db, actor, day, day))
    planned = [t for t in rows if t["plan_qty"]]
    if not planned:
        return None                                          # нет плана на сегодня — не беспокоим
    foreman = getattr(actor, "role", "") == "foreman"
    if kind == "morning":
        if foreman:
            return (f"Доброе утро, {name}! На участке сегодня {len({t['worker_id'] for t in planned})} исполнителей, "
                    f"{len(planned)} заданий.")
        lines = [f"Доброе утро, {name}! Задания на сегодня:"]
        lines += [f"• {t['operation']} · {t['product']} · {t['order']} — {bot.fnum(t['plan_qty'])}" for t in planned[:12]]
        return "\n".join(lines)
    missing = [t for t in planned if t["fact_qty"] is None]
    if not missing:
        return None                                          # факт внесён — напоминать не нужно
    if foreman:
        names = sorted({t["worker"] for t in missing})
        return f"Факт ещё не внесён у {len(names)}: " + ", ".join(names[:15]) + ("…" if len(names) > 15 else "")
    return f"{name}, не забудьте внести факт: заданий без факта — {len(missing)}."
