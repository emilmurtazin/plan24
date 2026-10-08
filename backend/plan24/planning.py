"""Сборка входа планировщика из БД, запуск и сохранение снапшота плана (ТЗ 8.2, BR-11, BR-13)."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (Fact, Holiday, Operation, OrderLine, Plan, PlanItem, PlannerSettings, Product, Project,
                     Routing, User, Worker, WorkerAbsence, WorkerOrderLine, utcnow)
from .planner import (FactRow, Operation as POp, OrderLine as POrder, PlanConfig, PlanInput, Worker as PWorker,
                      build_work_calendar, plan as run_planner, reports)

PAST_DAYS = 14        # глубина истории плана для отклонений «план/факт»


class NoActivePlan(Exception):
    """Частичное перепланирование участка возможно только поверх существующего общего плана."""


@dataclass
class Ctx:
    """Связь между ключами планировщика (строки) и идентификаторами БД."""
    order_by_key: dict = field(default_factory=dict)       # order_key -> OrderLine
    key_by_order_id: dict = field(default_factory=dict)
    worker_by_name: dict = field(default_factory=dict)     # name -> Worker
    op_id: dict = field(default_factory=dict)              # (order_key, op name) -> Operation.id
    product_name: dict = field(default_factory=dict)       # order_key -> имя изделия для людей
    project_id: dict = field(default_factory=dict)


def _weekdays(s: str) -> tuple:
    return tuple(int(x) for x in s.split(",") if x != "") or (0, 1, 2, 3, 4)


def build_input(db: Session, tenant_id: int, today: date, overrides: Optional[dict] = None):
    st: PlannerSettings = db.get(PlannerSettings, tenant_id)
    ov = overrides or {}
    q = lambda m: select(m).where(m.tenant_id == tenant_id)

    products = {p.id: p for p in db.scalars(q(Product))}
    projects = {p.id: p for p in db.scalars(q(Project))}
    routings = {r.id: r for r in db.scalars(q(Routing))}
    workers_all = list(db.scalars(q(Worker).order_by(Worker.id)))
    orders = list(db.scalars(q(OrderLine).where(OrderLine.deleted_at.is_(None), OrderLine.status == "planned")
                             .order_by(OrderLine.id)))
    holidays = [h.holiday_date for h in db.scalars(q(Holiday))]
    # защита от несогласованного чтения: позиция со ссылкой на отсутствующее изделие/проект/маршрут пропускается
    # (попадёт в следующий расчёт); падать на чужих или частично записанных данных нельзя
    orders = [o for o in orders if o.product_id in products and o.project_id in projects and o.routing_id in routings
              and routings[o.routing_id].tenant_id == tenant_id and products[o.product_id].tenant_id == tenant_id]
    cal = build_work_calendar(today, [o.due_date for o in orders], holidays, _weekdays(st.work_weekdays))

    # --- исполнители, отсутствия, допуски ---
    absent: dict = defaultdict(set)
    cal_set = set(cal)
    for a in db.scalars(q(WorkerAbsence)):
        d = a.date_from
        while d <= a.date_to:
            if d in cal_set:
                absent[a.worker_id].add(d)
            d += timedelta(days=1)
    avail = [w for w in workers_all if w.status == "available"]
    links: dict = defaultdict(set)
    for l in db.scalars(q(WorkerOrderLine)):
        links[l.worker_id].add(l.order_line_id)

    # --- ключи изделий: имя, а при нескольких версиях маршрута в плане — «имя (vN)» ---
    versions_used: dict = defaultdict(set)
    for o in orders:
        versions_used[o.product_id].add(o.routing_id)

    def pkey(o: OrderLine) -> str:
        p = products[o.product_id]
        return p.name if len(versions_used[o.product_id]) == 1 else f"{p.name} (v{routings[o.routing_id].version_no})"

    ctx = Ctx(worker_by_name={w.name: w for w in workers_all})
    pproducts, porders = {}, []
    for o in orders:
        key = o.ext_id or f"#{o.id}"
        pk = pkey(o)
        ctx.order_by_key[key] = o
        ctx.key_by_order_id[o.id] = key
        ctx.product_name[key] = products[o.product_id].name
        ctx.project_id[key] = o.project_id
        if pk not in pproducts:
            pproducts[pk] = tuple(POp(name=op.name, norm_per_day=op.norm_per_day, min_skill=op.min_skill,
                                      sequence=op.sequence, per_item=op.per_item, hold_qty=op.hold_qty,
                                      hold_until=op.hold_until, hold_reason=op.hold_reason)
                                  for op in routings[o.routing_id].operations)
        for op in routings[o.routing_id].operations:
            ctx.op_id[(key, op.name)] = op.id
        names = frozenset(w.name for w in avail if (not links[w.id]) or (o.id in links[w.id]))
        porders.append(POrder(id=key, project=projects[o.project_id].name, product=pk, qty=o.qty, due=o.due_date,
                              priority=float(o.priority),
                              allowed_workers=None if len(names) == len(avail) else names))
    prod_key_of = {ctx.key_by_order_id[o.id]: pkey(o) for o in orders}

    pworkers = tuple(PWorker(id=w.id, name=w.name, perf=w.perf, skill=w.skill, absent_days=frozenset(absent[w.id]))
                     for w in avail)

    # --- факт и план прошлых/ближайших дней в одних строках, как лист «Факт» скрипта ---
    scope = ov.get("scope_center_id")
    fixed_names: frozenset = frozenset()
    active_plan = db.scalar(select(Plan).where(Plan.tenant_id == tenant_id, Plan.status == "active"))
    if scope is not None:
        if active_plan is None:
            raise NoActivePlan()
        fixed_names = frozenset(w.name for w in avail if w.work_center_id != scope)
    freeze_days = ov.get("freeze_days", st.freeze_days)
    plan_today = cal[0] if cal else today
    freeze_window = set(cal[:freeze_days]) if freeze_days else set()
    rows: dict = {}     # (date, order_key, op, worker) -> [plan, qty]
    op_names = {o.id: o.name for o in db.scalars(q(Operation))}
    wname = {w.id: w.name for w in workers_all}
    for f in db.scalars(q(Fact).where(Fact.deleted_at.is_(None))):
        key = ctx.key_by_order_id.get(f.order_line_id)
        if key is None or f.operation_id not in op_names:
            continue
        r = rows.setdefault((f.fact_date, key, op_names[f.operation_id], wname.get(f.worker_id, "")), [0, 0])
        r[1] += f.qty
    for it in _prior_plan_items(db, tenant_id, plan_today, freeze_window):
        key = ctx.key_by_order_id.get(it.order_line_id)
        if key is None or it.operation_id not in op_names:
            continue
        if wname.get(it.worker_id) in fixed_names and it.plan_date >= plan_today:
            continue                                   # будущее фиксированных исполнителей добавляется ниже, без окна
        r = rows.setdefault((it.plan_date, key, op_names[it.operation_id], wname.get(it.worker_id, "")), [0, 0])
        r[0] += it.qty
    fact_rows = [FactRow(date=d, order_id=k, project=projects[ctx.project_id[k]].name, product=prod_key_of[k],
                         op=op, worker=w, plan=p, qty=qty) for (d, k, op, w), (p, qty) in sorted(rows.items(), key=lambda x: str(x[0]))]
    if fixed_names:
        # Всё, что уже назначено исполнителям вне участка, остаётся как есть: остаток плана (план − уже сделанное).
        fixed_ids = {w.id for w in avail if w.name in fixed_names}
        left: dict = defaultdict(float)
        for it in db.scalars(select(PlanItem).where(PlanItem.plan_id == active_plan.id, PlanItem.plan_date >= plan_today,
                                                    PlanItem.worker_id.in_(fixed_ids))):
            key = ctx.key_by_order_id.get(it.order_line_id)
            if key is None or it.operation_id not in op_names:
                continue
            left[(it.plan_date, key, op_names[it.operation_id], wname[it.worker_id])] += it.qty
        for (d, k, op, w), qty in sorted(left.items(), key=lambda x: str(x[0])):
            remaining = qty - rows.get((d, k, op, w), [0, 0])[1]          # уже внесённый факт вычитается
            if remaining > 0:
                fact_rows.append(FactRow(date=d, order_id=k, project=projects[ctx.project_id[k]].name,
                                         product=prod_key_of[k], op=op, worker=w, plan=remaining, qty=0))
    facts = tuple(fact_rows)

    by_level = {int(k): float(v) for k, v in (ov.get("strictness_by_level", st.strictness_by_level) or {}).items()}
    cfg = PlanConfig(
        today=plan_today, work_days=cal, efficiency=ov.get("efficiency", st.efficiency),
        max_workers_per_op=ov.get("max_workers_per_op", st.max_workers_per_op),
        today_load_threshold=ov.get("today_load_threshold", st.today_load_threshold),
        min_start_share=ov.get("min_start_share", st.min_start_share),
        priority_strictness=float(ov.get("priority_strictness", st.priority_strictness)),
        strictness_by_level=by_level, freeze_days=freeze_days,
        fix_group_start_after_deadline=ov.get("fix_group_start", st.fix_group_start))
    inp = PlanInput(order_lines=tuple(porders), products=pproducts, workers=pworkers, facts=facts, config=cfg,
                    all_workers_perf={w.name: w.perf for w in workers_all}, fixed_workers=fixed_names)
    return inp, ctx


def _prior_plan_items(db: Session, tenant_id: int, plan_today: date, freeze_window: set) -> list:
    """Строки плана, которые нужно учесть как «ранее запланированное»:
    — активный план на ближайшие дни (заморозка, BR-11), только на даты окна заморозки;
    — действовавший план за прошлые дни (для отклонений план/факт)."""
    out = []
    active = db.scalar(select(Plan).where(Plan.tenant_id == tenant_id, Plan.status == "active"))
    if active and freeze_window:
        out += list(db.scalars(select(PlanItem).where(PlanItem.plan_id == active.id, PlanItem.plan_date.in_(freeze_window))))
    lo = plan_today - timedelta(days=PAST_DAYS)
    plans = list(db.scalars(select(Plan).where(Plan.tenant_id == tenant_id).order_by(Plan.id.desc()).limit(60)))
    if plans:
        ids = [p.id for p in plans]
        items = db.scalars(select(PlanItem).where(PlanItem.plan_id.in_(ids), PlanItem.plan_date >= lo,
                                                  PlanItem.plan_date < plan_today))
        by_plan_date: dict = defaultdict(list)
        for it in items:
            by_plan_date[(it.plan_id, it.plan_date)].append(it)
        dates = {d for (_, d) in by_plan_date}
        for d in dates:
            for p in plans:                                  # от новых к старым: план, действовавший в этот день
                if date.fromisoformat(p.params.get("today", "1970-01-01")) <= d and (p.id, d) in by_plan_date:
                    out += by_plan_date[(p.id, d)]
                    break
    return out


def input_hash(inp: PlanInput) -> str:
    """Хеш реальных входных данных. Строки плана (заморозка, история) порождены самим планом
    и в хеш не входят — иначе новый план сразу считался бы устаревшим."""
    real_facts = tuple(replace(f, plan=0) for f in inp.facts if f.qty > 0)
    blob = json.dumps(asdict(replace(inp, facts=real_facts)), default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o),
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _status_json(inp, res, ctx) -> list:
    rows = reports.status_rows(inp, res)
    for r in rows:
        o = ctx.order_by_key[r["id"]]
        r["order_line_id"], r["project_id"], r["ext_id"] = o.id, o.project_id, o.ext_id
        r["id"] = r["ext_id"] or f"#{o.id}"
        r["product"] = ctx.product_name[r["id"]] if r["id"] in ctx.product_name else r["product"]
    return rows


def run_and_save(db: Session, user: User, today: date, overrides: Optional[dict] = None) -> Plan:
    inp, ctx = build_input(db, user.tenant_id, today, overrides)
    res = run_planner(inp)
    summary = {
        "status": _status_json(inp, res, ctx),
        "load": reports.load_matrix(inp, res),
        "work_days": [d.isoformat() for d in res.work_days],
        "loaded_today": res.loaded_today,
        "today": inp.config.today.isoformat(),
    }
    lm = summary["load"]
    lm["worker_info"] = {n: {"id": ctx.worker_by_name[n].id, "work_center_id": ctx.worker_by_name[n].work_center_id}
                         for n in lm["workers"] if n in ctx.worker_by_name}
    warnings = [{"code": w.code, "text": w.text, "order_line_id": ctx.order_by_key[w.order_id].id if w.order_id in ctx.order_by_key else None,
                 "operation": w.operation} for w in res.warnings]
    prev = db.scalar(select(Plan).where(Plan.tenant_id == user.tenant_id, Plan.status == "active"))
    old_flags: dict = {}
    if prev and inp.fixed_workers:
        wid = {n: ctx.worker_by_name[n].id for n in inp.fixed_workers if n in ctx.worker_by_name}
        for it in db.scalars(select(PlanItem).where(PlanItem.plan_id == prev.id, PlanItem.worker_id.in_(set(wid.values())))):
            old_flags[(it.plan_date, it.order_line_id, it.operation_id, it.worker_id)] = (it.is_frozen, it.reason_code)
    if prev:
        prev.status = "archived"
        db.flush()
    p = Plan(tenant_id=user.tenant_id, status="active", created_by=user.id, warnings=warnings, summary=summary,
             input_hash=input_hash(inp),
             params={"today": inp.config.today.isoformat(), "strictness": inp.config.priority_strictness,
                     "by_level": {str(k): v for k, v in inp.config.strictness_by_level.items()},
                     "fix_group_start": inp.config.fix_group_start_after_deadline, "efficiency": inp.config.efficiency,
                     "scope_center_id": (overrides or {}).get("scope_center_id")})
    db.add(p)
    db.flush()
    for it in res.items:
        o = ctx.order_by_key[it.order_id]
        wid_ = ctx.worker_by_name[it.worker].id
        opid_ = ctx.op_id[(it.order_id, it.operation)]
        frozen, reason = old_flags.get((it.date, o.id, opid_, wid_), (it.is_frozen, it.reason_code))
        db.add(PlanItem(tenant_id=user.tenant_id, plan_id=p.id, plan_date=it.date, order_line_id=o.id, operation_id=opid_,
                        worker_id=wid_, qty=it.qty, is_frozen=frozen, reason_code=reason))
    return p
