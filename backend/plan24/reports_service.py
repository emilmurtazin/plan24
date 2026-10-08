"""План на день с фактом — общий для веб-интерфейса и Telegram. Видимость по роли актора."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .deps import scoped
from .models import Fact, Operation, OrderLine, Plan, PlanItem, Product, Project, Worker


def active_plan(db: Session, actor) -> Optional[Plan]:
    return db.scalar(select(Plan).where(Plan.tenant_id == actor.tenant_id, Plan.status == "active"))


def visible_workers(actor, workers: list) -> list:
    if actor.role == "worker":
        return [w for w in workers if w.id == actor.worker_id]
    if actor.role == "foreman":
        return [w for w in workers if w.work_center_id == actor.work_center_id]
    return workers


def day_report(db: Session, user, day: Optional[date], today: date) -> dict:
    """План на день + факт по исполнителям. Сборщик видит только себя, бригадир — свой участок."""
    plan = active_plan(db, user)
    d = day or (date.fromisoformat(plan.params["today"]) if plan else today)
    workers = {w.id: w for w in db.scalars(scoped(Worker, user))}
    orders = {o.id: o for o in db.scalars(scoped(OrderLine, user))}
    products = {p.id: p.name for p in db.scalars(scoped(Product, user))}
    projects = {p.id: p.name for p in db.scalars(scoped(Project, user))}
    ops = {o.id: o.name for o in db.scalars(scoped(Operation, user))}
    visible = {w.id for w in visible_workers(user, list(workers.values()))}

    tasks: dict = defaultdict(dict)       # worker_id -> (order_line_id, operation_id) -> task
    if plan:
        for it in db.scalars(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.plan_date == d)):
            if it.worker_id not in visible:
                continue
            t = tasks[it.worker_id].setdefault((it.order_line_id, it.operation_id), {"plan_qty": 0, "frozen": False})
            t["plan_qty"] += it.qty
            t["frozen"] = t["frozen"] or it.is_frozen
    facts = {}
    for f in db.scalars(scoped(Fact, user).where(Fact.fact_date == d)):
        if f.worker_id in visible:
            facts[(f.worker_id, f.order_line_id, f.operation_id)] = f
            tasks[f.worker_id].setdefault((f.order_line_id, f.operation_id), {"plan_qty": 0, "frozen": False})

    out = []
    for wid in sorted(tasks, key=lambda i: workers[i].name):
        w, items = workers[wid], []
        for (oid, opid), t in sorted(tasks[wid].items()):
            o = orders.get(oid)
            if o is None:
                continue
            f = facts.get((wid, oid, opid))
            fq = f.qty if f else None
            plan_q = t["plan_qty"]
            state = "extra" if plan_q == 0 else ("none" if fq is None else ("done" if fq >= plan_q else "partial"))
            items.append({"order_line_id": oid, "order": o.ext_id or f"#{o.id}", "project": projects.get(o.project_id, ""),
                          "product": products.get(o.product_id, ""), "operation_id": opid, "operation": ops.get(opid, ""),
                          "plan_qty": plan_q, "fact_qty": fq, "fact_id": f.id if f else None,
                          "fact_status": f.status if f else None, "state": state, "frozen": t["frozen"]})
        out.append({"worker_id": wid, "worker": w.name, "work_center_id": w.work_center_id, "tasks": items})
    can_enter = (d == today) if user.role == "worker" else (d <= today and user.role != "viewer")
    days = (plan.summary.get("work_days", [])[:14] if plan else [])
    return {"date": d.isoformat(), "plan_id": plan.id if plan else None, "can_enter": can_enter, "workers": out,
            "days": days, "today": today.isoformat()}


