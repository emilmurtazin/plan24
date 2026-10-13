"""Снимки плана по дням. Строки на сегодня и будущее перезаписываются актуальным планом; прошедшие дни остаются как были —
по ним строится график недовыполнения плана (за каждый прошедший день известно, что на него планировалось)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import db as dbm
from .models import DayPlanSnapshot, Plan, PlanItem, PlannerSettings, Tenant, User, Worker


def sync(db: Session, tenant_id: int, plan: Plan, today: date) -> int:
    """Сохранить план на сегодня и будущее. Возвращает число строк."""
    db.execute(delete(DayPlanSnapshot).where(DayPlanSnapshot.tenant_id == tenant_id, DayPlanSnapshot.plan_date >= today))
    centers = {w.id: w.work_center_id for w in db.scalars(select(Worker).where(Worker.tenant_id == tenant_id))}
    agg: dict = defaultdict(int)
    for it in db.scalars(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.plan_date >= today)):
        agg[(it.plan_date, it.order_line_id, it.operation_id, it.worker_id)] += it.qty
    for (d, ol, op, w), q in agg.items():
        if q > 0:
            db.add(DayPlanSnapshot(tenant_id=tenant_id, plan_date=d, order_line_id=ol, operation_id=op, worker_id=w,
                                   work_center_id=centers.get(w), qty=q))
    st = db.get(PlannerSettings, tenant_id)
    if st is not None:
        st.snapshot_on = today
    return len(agg)


def sync_active(db: Session, tenant_id: int, today: date) -> bool:
    plan = db.scalar(select(Plan).where(Plan.tenant_id == tenant_id, Plan.status == "active"))
    if plan is None:
        return False
    sync(db, tenant_id, plan, today)
    return True


def ensure_today(db: Session, tenant_id: int, today: date) -> None:
    """Раз в сутки (при первом обращении) — на случай, если за день не было ни перепланирования, ни правок."""
    st = db.get(PlannerSettings, tenant_id)
    if st is not None and st.snapshot_on != today:
        sync_active(db, tenant_id, today)


def sync_all(db: Session, now_utc: datetime) -> int:
    """Для планировщика рассылок: обновить снимки всех организаций (по их часовым поясам), если за сегодня ещё не делали."""
    dbm.auth_scope(db, True)                               # список организаций — по пользователям (единственное, что читается до выбора организации)
    tenant_ids = sorted(set(db.scalars(select(User.tenant_id))))
    dbm.auth_scope(db, False)
    n = 0
    for tid in tenant_ids:
        dbm.set_tenant(db, tid)
        today = now_utc.astimezone(ZoneInfo(db.get(Tenant, tid).timezone)).date()
        st = db.get(PlannerSettings, tid)
        if st is not None and st.snapshot_on != today and sync_active(db, tid, today):
            n += 1
    return n
