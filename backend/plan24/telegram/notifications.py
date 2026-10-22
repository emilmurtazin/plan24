"""Утренние задания и вечерние напоминания (ТЗ: «Утро: бот присылает задания. Вечер: напоминание внести факт»).

Запускается по расписанию (cron каждые 5–10 минут или `python -m plan24.telegram scheduler`).
Каждому — не чаще раза в сутки на вид уведомления (NotificationLog).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, reports_service
from .. import db as dbm
from ..models import TelegramLink, Tenant
from . import bot, events
from .ui import app_button

WINDOW_HOURS = 3        # не «догонять» уведомление позже чем через 3 часа после его времени


def _at(s: str):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _in_window(local: datetime, at: str) -> bool:
    mins = local.hour * 60 + local.minute
    start = _at(at)
    return start <= mins < start + WINDOW_HOURS * 60


_first_time = events.first_time


def run_due(db: Session, now_utc: datetime) -> list:
    """Возвращает список исходящих сообщений (для client.deliver). Время — по часовому поясу каждой организации,
    у каждого человека — свои настройки (время, вкл/выкл) из «⚙ Настройки» в боте."""
    out: list = []
    dbm.auth_scope(db, True)                                     # список организаций с привязками — только чтение
    tenant_ids = sorted(set(db.scalars(select(TelegramLink.tenant_id))))
    dbm.auth_scope(db, False)
    for tid in tenant_ids:
        dbm.set_tenant(db, tid)                                  # дальше — данные одной организации
        tenant = db.get(Tenant, tid)
        local = now_utc.astimezone(ZoneInfo(tenant.timezone))
        day = local.date()
        for link in db.scalars(select(TelegramLink).where(TelegramLink.tenant_id == tid).order_by(TelegramLink.id)):
            s = events.link_settings(link)
            due = [k for k, on, at in (("morning", s["m_on"], s["m_at"]), ("evening", s["e_on"], s["e_at"])) if on and _in_window(local, at)]
            if not due:
                continue
            actor, name = bot.resolve_actor(db, link)
            if actor is None:
                continue
            for kind in due:
                msg = _compose(db, actor, name, kind, day)
                if msg and _first_time(db, tid, kind, str(link.id), day):
                    markup = msg[1]
                    url = config.tgapp_url()
                    if url and markup:                                   # в каждое напоминание — кнопка запуска приложения
                        markup["inline_keyboard"].append([app_button(url)])
                    bot.say(out, link.chat_id, msg[0], markup)
    return out


def _compose(db: Session, actor, name: str, kind: str, day):
    """(текст, кнопки) или None, если сообщать нечего."""
    rows = bot._flat_tasks(reports_service.day_report(db, actor, day, day))
    planned = [t for t in rows if t["plan_qty"]]
    if not planned:
        return None                                          # нет плана на сегодня — не беспокоим
    foreman = getattr(actor, "role", "") == "foreman"
    if kind == "morning":
        if foreman:
            return (f"Доброе утро, {name}! На участке сегодня {len({t['worker_id'] for t in planned})} исполнителей, "
                    f"{len(planned)} заданий.", bot.kb([[("👥 По людям", "l")]]))
        lines = [f"Доброе утро, {name}! Задания на сегодня:"]
        lines += [f"• {t['operation']} · {t['product']} · {t['order']} — {bot.fnum(t['plan_qty'])}" for t in planned[:12]]
        return "\n".join(lines), bot.kb([[("📋 Задания", "l")]])
    missing = [t for t in planned if t["fact_qty"] is None]
    if not missing:
        return None                                          # факт внесён — напоминать не нужно
    if foreman:
        names = sorted({t["worker"] for t in missing})
        return (f"Факт ещё не внесён у {len(names)}: " + ", ".join(names[:15]) + ("…" if len(names) > 15 else ""),
                bot.kb([[("🔔 Напомнить им", "rem")], [("✅ Внести по плану за всех", "cd:0")], [("👥 По людям", "l")]]))
    return (f"{name}, не забудьте внести факт: заданий без факта — {len(missing)}.",
            bot.kb([[("✅ Закрыть день по плану", "cd:0")], [("📋 Задания", "l")]]))
