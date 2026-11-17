"""«План-график»: календарный план и факт по всем заказам (по дням, в нормо-днях), с фильтрами по участкам и заказам.

План: прошедшие дни — снимки плана «как было» (day_plan_snapshots), сегодня и будущее — активный план. Факт — внесённые факты
(по работникам и «на заказ»). Нормо-день = объём ÷ норма операции за день: так операции в разных единицах складываются в заказ.
Бригадир видит только свой участок."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import progress
from ..db import DB
from ..deps import READERS, get_or_404, get_today, require
from ..models import (DayPlanSnapshot, Fact, Operation, OrderLine, OrderLineOperation, Plan, PlanItem, Product, Project, WorkCenter, Worker, User)

router = APIRouter(prefix="/schedule", tags=["schedule"])
MAX_ORDERS = 400
MAX_SPAN_DAYS = 400
DEFAULT_BACK_DAYS = 45
DEFAULT_SPAN_DAYS = 150


def _nm(qty: float, op: Operation) -> float:
    return qty / op.norm_per_day if op.norm_per_day and op.norm_per_day > 0 else float(qty)


def _load(db: Session, user: User, today: date, wc: Optional[int], project_id: Optional[int], line_ids: list) -> dict:
    t = user.tenant_id
    if user.role == "foreman":
        wc = user.work_center_id
    q = select(OrderLine).where(OrderLine.tenant_id == t, OrderLine.deleted_at.is_(None), OrderLine.status.in_(("planned", "done")))
    if project_id is not None:
        q = q.where(OrderLine.project_id == project_id)
    if line_ids:
        q = q.where(OrderLine.id.in_(line_ids))
    if wc is not None:
        q = q.where(OrderLine.id.in_(select(OrderLineOperation.order_line_id).where(OrderLineOperation.tenant_id == t,
                                                                                    OrderLineOperation.work_center_id == wc)))
    projects = {p.id: p.name for p in db.scalars(select(Project).where(Project.tenant_id == t))}
    lines = sorted(db.scalars(q.limit(MAX_ORDERS * 3)), key=lambda l: (projects.get(l.project_id, ""), l.due_date or date.max, l.id))[:MAX_ORDERS]
    ids = [l.id for l in lines]
    ops_by_routing: dict = defaultdict(list)
    for o in db.scalars(select(Operation).where(Operation.tenant_id == t, Operation.routing_id.in_({l.routing_id for l in lines} or {0}))
                        .order_by(Operation.sequence, Operation.id)):
        ops_by_routing[o.routing_id].append(o)
    assign: dict = defaultdict(lambda: defaultdict(set))
    for a in db.scalars(select(OrderLineOperation).where(OrderLineOperation.tenant_id == t, OrderLineOperation.order_line_id.in_(ids or [0]))):
        assign[a.order_line_id][a.operation_id].add(a.work_center_id)
    worker_wc = {w.id: w.work_center_id for w in db.scalars(select(Worker).where(Worker.tenant_id == t))}
    centers = {c.id: c.name for c in db.scalars(select(WorkCenter).where(WorkCenter.tenant_id == t))}
    # операции в разрезе позиции: при фильтре по участку — только операции этого участка
    ops: dict = {}
    for l in lines:
        ops[l.id] = [o for o in ops_by_routing.get(l.routing_id, []) if wc is None or wc in assign[l.id].get(o.id, ())]
    allowed = {l.id: {o.id for o in ops[l.id]} for l in lines}
    plan: dict = defaultdict(lambda: defaultdict(float))        # (позиция, операция) → день → объём
    for s in db.scalars(select(DayPlanSnapshot).where(DayPlanSnapshot.tenant_id == t, DayPlanSnapshot.order_line_id.in_(ids or [0]),
                                                      DayPlanSnapshot.plan_date < today)):
        if s.operation_id in allowed.get(s.order_line_id, ()) and (wc is None or s.work_center_id == wc):
            plan[(s.order_line_id, s.operation_id)][s.plan_date] += s.qty
    active = db.scalar(select(Plan).where(Plan.tenant_id == t, Plan.status == "active"))
    if active is not None:
        for it in db.scalars(select(PlanItem).where(PlanItem.plan_id == active.id, PlanItem.order_line_id.in_(ids or [0]), PlanItem.plan_date >= today)):
            if it.operation_id in allowed.get(it.order_line_id, ()) and (wc is None or worker_wc.get(it.worker_id) == wc):
                plan[(it.order_line_id, it.operation_id)][it.plan_date] += it.qty
    fact: dict = defaultdict(lambda: defaultdict(float))
    done_all: dict = defaultdict(lambda: defaultdict(float))
    for f in db.scalars(select(Fact).where(Fact.tenant_id == t, Fact.deleted_at.is_(None), Fact.order_line_id.in_(ids or [0]))):
        done_all[f.order_line_id][f.operation_id] += f.qty
        if f.operation_id in allowed.get(f.order_line_id, ()):
            where = worker_wc.get(f.worker_id) if f.worker_id is not None else f.work_center_id
            if wc is None or where == wc:
                fact[(f.order_line_id, f.operation_id)][f.fact_date] += f.qty
    rows = {r.get("order_line_id"): r for r in (active.summary.get("status", []) if active else [])}
    return {"lines": lines, "projects": projects, "ops": ops, "assign": assign, "plan": plan, "fact": fact, "done": done_all, "rows": rows,
            "centers": centers, "products": {p.id: p.name for p in db.scalars(select(Product).where(Product.tenant_id == t))}, "wc": wc}


def _span(days: dict) -> tuple:
    ks = [d for d, v in days.items() if v > 1e-9]
    return (min(ks), max(ks)) if ks else (None, None)


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _window(bounds_lo: Optional[date], bounds_hi: Optional[date], date_from: Optional[date], date_to: Optional[date], today: date) -> tuple:
    lo = date_from or max(bounds_lo or today, today - timedelta(days=DEFAULT_BACK_DAYS))
    lo = min(lo, today) if date_from is None else lo
    hi = date_to or min(max(bounds_hi or today, today + timedelta(days=14)), lo + timedelta(days=DEFAULT_SPAN_DAYS))
    if hi < lo:
        raise HTTPException(422, "Конец периода раньше начала")
    if (hi - lo).days > MAX_SPAN_DAYS:
        raise HTTPException(422, f"Период не больше {MAX_SPAN_DAYS} дней")
    return lo, hi


@router.get("")
def schedule(date_from: Optional[date] = None, date_to: Optional[date] = None, work_center_id: Optional[int] = None,
             project_id: Optional[int] = None, order_line_id: list[int] = Query(default=[]), user: User = Depends(require(*READERS)),
             db: Session = DB, today: date = Depends(get_today)):
    """Все заказы с календарным планом и фактом по дням (нормо-дни). Окно по умолчанию: от начала работ (не раньше 45 дней назад)
    до конца плана; `bounds` — весь период, в котором есть план, факт или сроки."""
    d = _load(db, user, today, work_center_id, project_id, order_line_id)
    out, b_lo, b_hi = [], None, None
    tot: dict = defaultdict(lambda: [0.0, 0.0])
    per_order = []
    for l in d["lines"]:
        ops = d["ops"][l.id]
        plan_d, fact_d = defaultdict(float), defaultdict(float)
        by_op = {o.id: o for o in ops}
        for o in ops:
            for day, q in d["plan"].get((l.id, o.id), {}).items():
                plan_d[day] += _nm(q, o)
            for day, q in d["fact"].get((l.id, o.id), {}).items():
                fact_d[day] += _nm(q, o)
        pr = progress.order_progress(l.qty, ops, d["done"].get(l.id, {}))
        row = d["rows"].get(l.id) or {}
        pct = pr["percent"]
        status = row.get("status") or ("Готово" if pct >= 99.95 else "Нет плана")
        ps, pe = _span(plan_d)
        fs, fe = _span(fact_d)
        forecast = date.fromisoformat(row["forecast"]) if row.get("forecast") else None
        for x in (ps, fs, l.due_date):
            if x and (b_lo is None or x < b_lo):
                b_lo = x
        for x in (pe, fe, l.due_date, forecast):
            if x and (b_hi is None or x > b_hi):
                b_hi = x
        per_order.append((l, ops, plan_d, fact_d, pct, status, ps, pe, fs, fe, forecast, pr))
    lo, hi = _window(b_lo, b_hi, date_from, date_to, today)
    for l, ops, plan_d, fact_d, pct, status, ps, pe, fs, fe, forecast, pr in per_order:
        days = {}
        for day in set(plan_d) | set(fact_d):
            if lo <= day <= hi:
                p, f = round(plan_d.get(day, 0.0), 2), round(fact_d.get(day, 0.0), 2)
                if p or f:
                    days[day.isoformat()] = [p, f]
                    tot[day.isoformat()][0] += p
                    tot[day.isoformat()][1] += f
        out.append({"id": l.id, "project_id": l.project_id, "project": d["projects"].get(l.project_id, ""), "order": l.ext_id or f"№{l.id}",
                    "product": d["products"].get(l.product_id, ""), "qty": l.qty, "due": _iso(l.due_date), "status": status, "progress": pct,
                    "forecast": _iso(forecast), "plan_start": _iso(ps), "plan_end": _iso(pe), "fact_start": _iso(fs), "fact_end": _iso(fe),
                    "work": round(pr["total_work"], 2), "work_done": round(pr["done_work"], 2),
                    "sections": sorted({d["centers"].get(c, "") for o in ops for c in d["assign"][l.id].get(o.id, ())} - {""}),
                    "operations_count": len(ops), "days": days})
    return {"today": today.isoformat(), "date_from": lo.isoformat(), "date_to": hi.isoformat(),
            "bounds": {"min": _iso(b_lo), "max": _iso(b_hi)}, "orders": out,
            "totals": {k: [round(v[0], 2), round(v[1], 2)] for k, v in sorted(tot.items())},
            "truncated": len(d["lines"]) >= MAX_ORDERS, "work_center_id": d["wc"]}


@router.get("/{order_line_id}/operations")
def schedule_operations(order_line_id: int, date_from: date, date_to: date, work_center_id: Optional[int] = None,
                        user: User = Depends(require(*READERS)), db: Session = DB, today: date = Depends(get_today)):
    """Разбивка заказа по операциям: план и факт по дням в единицах операции."""
    ol = get_or_404(db, OrderLine, order_line_id, user)
    if date_to < date_from or (date_to - date_from).days > MAX_SPAN_DAYS:
        raise HTTPException(422, f"Период не больше {MAX_SPAN_DAYS} дней")
    d = _load(db, user, today, work_center_id, None, [ol.id])
    if not d["lines"]:
        if user.role == "foreman":
            raise HTTPException(404, "У этой позиции нет операций вашего участка")
        return {"order_line_id": ol.id, "operations": []}
    res = []
    for o in d["ops"][ol.id]:
        pl, fa = d["plan"].get((ol.id, o.id), {}), d["fact"].get((ol.id, o.id), {})
        req = progress.required(ol.qty, o)
        done = d["done"].get(ol.id, {}).get(o.id, 0.0)
        ps, pe = _span(pl)
        fs, fe = _span(fa)
        days = {}
        for day in set(pl) | set(fa):
            if date_from <= day <= date_to:
                days[day.isoformat()] = [round(pl.get(day, 0.0), 2), round(fa.get(day, 0.0), 2)]
        res.append({"id": o.id, "name": o.name, "unit": o.unit, "sequence": o.sequence, "required": req, "done": min(done, req) if req else 0,
                    "pct": round(min(done, req) / req * 100, 1) if req else 100.0,
                    "sections": sorted(d["centers"].get(c, "") for c in d["assign"][ol.id].get(o.id, ())),
                    "plan_start": _iso(ps), "plan_end": _iso(pe), "fact_start": _iso(fs), "fact_end": _iso(fe), "days": days})
    return {"order_line_id": ol.id, "operations": res}
