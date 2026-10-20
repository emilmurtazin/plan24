"""События, о которых бот сообщает сам (не в ответ на сообщение): отклонение от плана — бригадиру, изменение плана — работнику.

Сообщения собираются в `out` и отправляются после ответа пользователю (client.deliver). Каждому — с учётом его настроек
(«Оповещения») и без повторов (NotificationLog).
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config
from ..models import (DayPlanSnapshot, FactReason, NotificationLog, Operation, OrderLine, Plan, PlanItem, Product, TelegramLink, User,
                      Worker)
from . import client as tgclient
from .ui import fday, fnum, kb, say


def enabled() -> bool:
    """Есть ли смысл собирать события: бот настроен (или подставлен поддельный в тестах)."""
    return tgclient.get_client() is not None


# ------------------------------------------------------------------ настройки и адресаты
def link_settings(link: TelegramLink) -> dict:
    cfg = config.telegram_settings()
    s = dict(link.settings or {})
    return {"m_on": s.get("m_on", True), "m_at": s.get("m_at") or cfg["morning_at"],
            "e_on": s.get("e_on", True), "e_at": s.get("e_at") or cfg["evening_at"], "alerts": s.get("alerts", True)}


def first_time(db: Session, tenant_id: int, kind: str, key: str, day: date) -> bool:
    try:
        with db.begin_nested():
            db.add(NotificationLog(tenant_id=tenant_id, kind=kind, key=key[:64], sent_on=day))
            db.flush()
        return True
    except IntegrityError:
        return False


def foreman_links(db: Session, tenant_id: int, center_id: Optional[int], skip_user_id: Optional[int] = None,
                  only_alerts: bool = True) -> list:
    """Привязки бригадиров участка. only_alerts — только тем, у кого включены оповещения (проблемы и отсутствие шлём всем)."""
    if center_id is None:
        return []
    uids = [u.id for u in db.scalars(select(User).where(User.tenant_id == tenant_id, User.role == "foreman", User.is_active.is_(True),
                                                        User.work_center_id == center_id)) if u.id != skip_user_id]
    if not uids:
        return []
    return [l for l in db.scalars(select(TelegramLink).where(TelegramLink.tenant_id == tenant_id, TelegramLink.user_id.in_(uids)))
            if link_settings(l)["alerts"] or not only_alerts]


def worker_link(db: Session, tenant_id: int, worker_id: int) -> Optional[TelegramLink]:
    return db.scalar(select(TelegramLink).where(TelegramLink.tenant_id == tenant_id, TelegramLink.worker_id == worker_id))


# ------------------------------------------------------------------ названия
class Names:
    """Короткие подписи «операция · изделие · заказ» с кэшем на время одной сборки сообщений."""
    def __init__(self, db: Session):
        self.db, self._c = db, {}

    def task(self, order_line_id: int, operation_id: int) -> str:
        k = (order_line_id, operation_id)
        if k not in self._c:
            op, ol = self.db.get(Operation, operation_id), self.db.get(OrderLine, order_line_id)
            prod = self.db.get(Product, ol.product_id) if ol else None
            self._c[k] = " · ".join([op.name if op else "операция", prod.name if prod else "изделие", (ol.ext_id or f"№{ol.id}") if ol else ""]).strip(" ·")
        return self._c[k]


def planned_qty(db: Session, tenant_id: int, d: date, ol: int, op: int, worker_id: int) -> float:
    """Сколько планировалось на день по связке позиция × операция × работник: активный план, а для прошедших дней — снимок."""
    plan = db.scalar(select(Plan.id).where(Plan.tenant_id == tenant_id, Plan.status == "active"))
    q = 0.0
    if plan:
        q = sum(x.qty for x in db.scalars(select(PlanItem).where(PlanItem.plan_id == plan, PlanItem.plan_date == d, PlanItem.order_line_id == ol,
                                                                  PlanItem.operation_id == op, PlanItem.worker_id == worker_id)))
    if not q:
        q = sum(x.qty for x in db.scalars(select(DayPlanSnapshot).where(
            DayPlanSnapshot.tenant_id == tenant_id, DayPlanSnapshot.plan_date == d, DayPlanSnapshot.order_line_id == ol,
            DayPlanSnapshot.operation_id == op, DayPlanSnapshot.worker_id == worker_id)))
    return q


# ------------------------------------------------------------------ отклонение от плана → бригадиру
def alert_shortfall(db: Session, fact, out: list, skip_user_id: Optional[int] = None) -> int:
    """Сделано меньше плана — бригадир участка получает сообщение сразу, пока смена не закончилась. → число отправленных."""
    if fact.qty is None:
        return 0
    w = db.get(Worker, fact.worker_id)
    plan = planned_qty(db, fact.tenant_id, fact.fact_date, fact.order_line_id, fact.operation_id, fact.worker_id)
    if not w or not plan or fact.qty >= plan:
        return 0
    reason = db.get(FactReason, fact.reason_id).name if fact.reason_id else ((fact.note or "").strip() or "не указана")
    text = (f"⚠ Отклонение: {w.name}\n{Names(db).task(fact.order_line_id, fact.operation_id)}\n"
            f"Сделано {fnum(fact.qty)} из {fnum(plan)} — {fday(fact.fact_date)}\nПричина: {reason}")
    sent = 0
    for link in foreman_links(db, fact.tenant_id, w.work_center_id, skip_user_id):
        key = f"{link.id}:{fact.id}:{fact.qty:g}:{fact.reason_id or 0}"
        if first_time(db, fact.tenant_id, "dev", key, fact.fact_date):
            say(out, link.chat_id, text, kb([[(f"👤 {w.name[:30]}", f"w:{w.id}")]]))
            sent += 1
    return sent


# ------------------------------------------------------------------ изменение плана → работнику
def plan_state(db: Session, tenant_id: int, days: list, worker_ids: Optional[list] = None) -> dict:
    """{(работник, день): {(позиция, операция): количество}} по активному плану на указанные дни."""
    plan = db.scalar(select(Plan.id).where(Plan.tenant_id == tenant_id, Plan.status == "active"))
    st: dict = {}
    if not plan:
        return st
    q = select(PlanItem).where(PlanItem.plan_id == plan, PlanItem.plan_date.in_(days))
    if worker_ids:
        q = q.where(PlanItem.worker_id.in_(worker_ids))
    for it in db.scalars(q):
        d = st.setdefault((it.worker_id, it.plan_date), {})
        d[(it.order_line_id, it.operation_id)] = d.get((it.order_line_id, it.operation_id), 0) + it.qty
    return st


def notify_plan_changes(db: Session, tenant_id: int, before: dict, after: dict, out: list, today: date, who: str = "Бригадир") -> int:
    """Работнику — что изменилось в его плане на сегодня/завтра. Повторы одинакового состояния за день не шлём."""
    names, sent = Names(db), 0
    for (wid, day) in sorted(set(before) | set(after), key=lambda k: (k[1], k[0])):
        if day not in (today, today + timedelta(days=1)):
            continue
        b, a = before.get((wid, day), {}), after.get((wid, day), {})
        if b == a:
            continue
        link = worker_link(db, tenant_id, wid)
        if link is None or not link_settings(link)["alerts"]:
            continue
        sig = hashlib.sha1(repr(sorted(a.items())).encode()).hexdigest()[:10]
        if not first_time(db, tenant_id, "planchg", f"{wid}:{day.isoformat()}:{sig}", today):
            continue
        lines = [f"📝 {who} изменил ваш план на {fday(day)}:"]
        for k in sorted(a, key=str):
            if k not in b:
                lines.append(f"＋ {names.task(*k)} — {fnum(a[k])}")
            elif a[k] != b[k]:
                lines.append(f"✎ {names.task(*k)} — {fnum(b[k])} → {fnum(a[k])}")
        for k in sorted(b, key=str):
            if k not in a:
                lines.append(f"− {names.task(*k)} (убрано)")
        if len(lines) > 9:
            lines = lines[:9] + ["…"]
        say(out, link.chat_id, "\n".join(lines), kb([[("📋 Мои задания", "l")]]))
        sent += 1
    return sent
