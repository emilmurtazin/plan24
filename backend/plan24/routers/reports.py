"""Отчёты по активному плану с учётом роли (FR-26…FR-28, BR-14)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import dashboard as dashboard_service
from .. import distribution, order_status, reports_service, snapshots
from ..db import DB
from ..deps import READERS, STAFF, ALL, get_or_404, get_today, require, scoped
from ..models import (Fact, Operation, OrderLine, Plan, PlanItem, Product, Project, User, WorkCenter, Worker)

router = APIRouter(prefix="/reports", tags=["reports"])


def _active(db, user) -> Optional[Plan]:
    return db.scalar(select(Plan).where(Plan.tenant_id == user.tenant_id, Plan.status == "active"))


def _own_rows(db, user, rows: list) -> list:
    """Бригадир видит позиции, у которых есть операции его участка (остальные участки — не его дело)."""
    if user.role != "foreman":
        return rows
    mine = distribution.order_ids_of_center(db, user, user.work_center_id)
    return [r for r in rows if r.get("order_line_id") in mine]


def _visible_workers(db, user, workers: list) -> list:
    if user.role == "worker":
        return [w for w in workers if w.id == user.worker_id]
    if user.role == "foreman":
        return [w for w in workers if w.work_center_id == user.work_center_id]
    return workers


@router.get("/day")
def day(day: Optional[date] = None, user: User = Depends(require(*ALL)), db: Session = DB,
        today: date = Depends(get_today)):
    """План на день + факт по исполнителям. Сборщик видит только себя, бригадир — свой участок."""
    return reports_service.day_report(db, user, day, today)


@router.get("/status")
def status(user: User = Depends(require(*READERS)), db: Session = DB, today: date = Depends(get_today)):
    """Сводка по заказам: прогноз, срок, статус, % выполнения (progress) и % простоя (idle_pct — недовыполнение плана за прошлые дни)."""
    p = _active(db, user)
    if not p:
        return {"plan_id": None, "rows": []}
    rows = _own_rows(db, user, p.summary.get("status", []))
    idle = order_status.idle_by_order(db, user, today, [r["order_line_id"] for r in rows if r.get("order_line_id")],
                                      user.work_center_id if user.role == "foreman" else None)
    rows = [{**r, "progress": r.get("progress", 0.0), "idle_pct": (idle.get(r.get("order_line_id")) or {}).get("pct")} for r in rows]
    return {"plan_id": p.id, "created_at": p.created_at.isoformat(), "rows": rows}


@router.get("/overview")
def overview(user: User = Depends(require(*READERS)), db: Session = DB, today: date = Depends(get_today)):
    """Данные дашборда: выполнение, статусы, потребность в ресурсах, причины отклонений, работники, графики на 3 месяца."""
    snapshots.ensure_today(db, user.tenant_id, today)         # раз в сутки при первом открытии, если снимок за день ещё не делали
    return dashboard_service.build(db, user, today)


@router.get("/order-status")
def order_status_by_operations(order_line_id: int, user: User = Depends(require(*READERS)), db: Session = DB,
                               today: date = Depends(get_today)):
    """Статус одного заказа по операциям: выполнено, в плане, смены до завершения, узкое место, проблемы, рекомендации.
    Участок (бригадир) видит только свои операции."""
    ol = get_or_404(db, OrderLine, order_line_id, user)
    if user.role == "foreman" and ol.id not in distribution.order_ids_of_center(db, user, user.work_center_id):
        raise HTTPException(404, "У этой позиции нет операций вашего участка")
    return order_status.build(db, user, ol, today)


@router.get("/load")
def load(user: User = Depends(require(*READERS)), db: Session = DB):
    p = _active(db, user)
    if not p:
        return {"plan_id": None}
    lm = dict(p.summary["load"])
    if user.role == "foreman":
        keep = [n for n in lm["workers"] if lm["worker_info"].get(n, {}).get("work_center_id") == user.work_center_id]
        lm = {**lm, "workers": keep, **{k: {n: lm[k][n] for n in keep} for k in ("plan", "fact", "deviation")}}
    centers = {c.id: c.name for c in db.scalars(scoped(WorkCenter, user))}
    return {"plan_id": p.id, **lm, "centers": {str(k): v for k, v in centers.items()}}


@router.get("/dashboard")
def dashboard(user: User = Depends(require(*READERS)), db: Session = DB, today: date = Depends(get_today)):
    p = _active(db, user)
    if not p:
        return {"plan_id": None}
    rows = _own_rows(db, user, p.summary.get("status", []))
    by: dict = defaultdict(int)
    for r in rows:
        by[r["status"]] += 1
    risk = sorted([r for r in rows if r["status"] == "Риск опоздания"], key=lambda r: r["due"] or "9999")[:5]
    nearest = sorted([r for r in rows if r["due"] and r["status"] != "Готово"], key=lambda r: r["due"])[:5]
    lm = p.summary["load"]
    week = [d for d in lm["dates"] if today.isoformat() <= d][:7]
    peak = max((lm["plan"][n].get(d, 0) for n in lm["workers"] for d in week), default=0)
    return {"plan_id": p.id, "by_status": dict(by), "risk": risk, "nearest_due": nearest, "week_peak_load": peak,
            "loaded_today": p.summary.get("loaded_today", [])}
