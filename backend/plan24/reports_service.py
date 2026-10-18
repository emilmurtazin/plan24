"""План на день с фактом — общий для веб-интерфейса и Telegram. Видимость по роли актора."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

import math

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .deps import scoped
from . import planning
from .models import DayPin, DayPlanSnapshot, ExtraTask, Fact, FactReason, Operation, OrderLine, Plan, PlanItem, Product, Project, Worker


def active_plan(db: Session, actor) -> Optional[Plan]:
    return db.scalar(select(Plan).where(Plan.tenant_id == actor.tenant_id, Plan.status == "active"))


def visible_workers(actor, workers: list) -> list:
    if actor.role == "worker":
        return [w for w in workers if w.id == actor.worker_id]
    if actor.role == "foreman":
        return [w for w in workers if w.work_center_id == actor.work_center_id]
    return workers


MISSING_DAYS = 31        # за сколько прошедших дней ищем невнесённый факт


def missing_fact_days(db: Session, user, today: date) -> list:
    """Прошедшие дни, на которые по плану были задания, а факт по ним не внесён: [{date, count, workers}].
    «Не внесён» — нет записи факта по связке день × позиция × операция × работник (внесённый ноль — это внесённый факт).
    Бригадир видит свой участок; сборщик — только себя."""
    lo = today - timedelta(days=MISSING_DAYS)
    snaps = list(db.scalars(scoped(DayPlanSnapshot, user).where(DayPlanSnapshot.plan_date >= lo, DayPlanSnapshot.plan_date < today)))
    if not snaps:
        return []
    workers = {w.id: w for w in db.scalars(scoped(Worker, user))}
    visible = {w.id for w in visible_workers(user, list(workers.values()))}
    have = {(f.fact_date, f.order_line_id, f.operation_id, f.worker_id) for f in db.scalars(
        scoped(Fact, user).where(Fact.fact_date >= lo, Fact.fact_date < today, Fact.deleted_at.is_(None)))}
    by_day: dict = defaultdict(lambda: [0, set()])
    for x in snaps:
        if x.worker_id in visible and x.qty > 0 and (x.plan_date, x.order_line_id, x.operation_id, x.worker_id) not in have:
            by_day[x.plan_date][0] += 1
            by_day[x.plan_date][1].add(x.worker_id)
    return [{"date": d.isoformat(), "count": c, "workers": len(ws)} for d, (c, ws) in sorted(by_day.items())]


def day_report(db: Session, user, day: Optional[date], today: date) -> dict:
    """План на день + факт по исполнителям. Сборщик видит только себя, бригадир — свой участок."""
    plan = active_plan(db, user)
    d = day or (date.fromisoformat(plan.params["today"]) if plan else today)
    workers = {w.id: w for w in db.scalars(scoped(Worker, user))}
    orders = {o.id: o for o in db.scalars(scoped(OrderLine, user))}
    products = {p.id: p.name for p in db.scalars(scoped(Product, user))}
    projects = {p.id: p.name for p in db.scalars(scoped(Project, user))}
    ops = {o.id: o for o in db.scalars(scoped(Operation, user))}
    reasons = {r.id: r.name for r in db.scalars(scoped(FactReason, user))}
    visible = {w.id for w in visible_workers(user, list(workers.values()))}

    tasks: dict = defaultdict(dict)       # worker_id -> (order_line_id, operation_id) -> task
    if plan:
        for it in db.scalars(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.plan_date == d)):
            if it.worker_id not in visible:
                continue
            t = tasks[it.worker_id].setdefault((it.order_line_id, it.operation_id),
                                               {"plan_qty": 0, "frozen": False, "locked": False, "manual": False, "item_id": it.id})
            t["plan_qty"] += it.qty
            t["frozen"] = t["frozen"] or it.is_frozen
            t["locked"] = t["locked"] or it.is_locked
            t["manual"] = t["manual"] or it.reason_code == "MANUAL"
    if d < today:
        # прошедший день: активный план начинается с сегодняшнего, а что планировалось на тот день — в снимках плана
        for x in db.scalars(scoped(DayPlanSnapshot, user).where(DayPlanSnapshot.plan_date == d)):
            if x.worker_id not in visible or x.qty <= 0:
                continue
            t = tasks[x.worker_id].setdefault((x.order_line_id, x.operation_id),
                                              {"plan_qty": 0, "frozen": False, "locked": False, "manual": False, "item_id": None})
            t["plan_qty"] += x.qty
    facts = {}
    for f in db.scalars(scoped(Fact, user).where(Fact.fact_date == d, Fact.deleted_at.is_(None))):
        if f.worker_id in visible:
            facts[(f.worker_id, f.order_line_id, f.operation_id)] = f
            tasks[f.worker_id].setdefault((f.order_line_id, f.operation_id),
                                          {"plan_qty": 0, "frozen": False, "locked": False, "manual": False, "item_id": None})
    # сколько по позиции и операции уже сделано всеми — чтобы понять, когда факт превысит потребность заказа
    pairs = {k for per in tasks.values() for k in per}
    done_total: dict = {}
    if pairs:
        for oid, opid, tot in db.execute(select(Fact.order_line_id, Fact.operation_id, func.coalesce(func.sum(Fact.qty), 0.0)).where(
                Fact.tenant_id == user.tenant_id, Fact.deleted_at.is_(None),
                Fact.order_line_id.in_({k[0] for k in pairs})).group_by(Fact.order_line_id, Fact.operation_id)):
            done_total[(oid, opid)] = tot
    extras: dict = defaultdict(list)
    for x in db.scalars(scoped(ExtraTask, user).where(ExtraTask.plan_date == d).order_by(ExtraTask.id)):
        if x.worker_id in visible:
            o = orders.get(x.order_line_id) if x.order_line_id else None
            extras[x.worker_id].append({"id": x.id, "title": x.title, "hours": x.hours, "fact_hours": x.fact_hours,
                                        "order_line_id": x.order_line_id, "order": (o.ext_id or f"#{o.id}") if o else None,
                                        "project": projects.get(o.project_id, "") if o else "",
                                        "product": products.get(o.product_id, "") if o else "",
                                        "reason_id": x.reason_id, "reason": reasons.get(x.reason_id), "note": x.note})
    pins = {p.worker_id for p in db.scalars(scoped(DayPin, user).where(DayPin.plan_date == d)) if p.worker_id in visible}

    out = []
    for wid in sorted(set(tasks) | set(extras) | pins, key=lambda i: workers[i].name):
        w, items = workers[wid], []
        for (oid, opid), t in sorted(tasks.get(wid, {}).items()):
            o = orders.get(oid)
            if o is None:
                continue
            f = facts.get((wid, oid, opid))
            fq = f.qty if f else None
            plan_q = t["plan_qty"]
            op = ops.get(opid)
            required = math.ceil(o.qty * op.per_item - 1e-9) if op else None
            need_left = None if required is None else required - (done_total.get((oid, opid), 0.0) - (fq or 0.0))
            state = "extra" if plan_q == 0 else ("none" if fq is None else ("done" if fq >= plan_q else "partial"))
            items.append({"order_line_id": oid, "order": o.ext_id or f"#{o.id}", "project": projects.get(o.project_id, ""),
                          "project_id": o.project_id, "product_id": o.product_id,
                          "product": products.get(o.product_id, ""), "operation_id": opid, "operation": op.name if op else "",
                          "plan_qty": plan_q, "fact_qty": fq, "fact_id": f.id if f else None,
                          "fact_status": f.status if f else None, "state": state, "frozen": t["frozen"],
                          "locked": t["locked"], "manual": t["manual"], "item_id": t["item_id"],
                          "need_left": need_left, "required": required,
                          "reason_id": f.reason_id if f else None, "reason": reasons.get(f.reason_id) if f and f.reason_id else None,
                          "note": f.note if f else ""})
        out.append({"worker_id": wid, "worker": w.name, "work_center_id": w.work_center_id, "is_temporary": w.is_temporary,
                    "pinned": wid in pins, "tasks": items, "extras": extras.get(wid, [])})
    can_enter = (d == today) if user.role == "worker" else (d <= today and user.role not in ("viewer", "director"))
    can_edit = bool(plan) and user.role in ("owner", "manager", "foreman") and d >= today
    days = (plan.summary.get("work_days", [])[:14] if plan else [])
    return {"date": d.isoformat(), "plan_id": plan.id if plan else None, "can_enter": can_enter, "can_edit": can_edit,
            "workers": out, "days": days, "today": today.isoformat(), "is_workday": planning.is_work_day(db, user.tenant_id, d),
            "missing": missing_fact_days(db, user, today) if user.role != "worker" else []}
