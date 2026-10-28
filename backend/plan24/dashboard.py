"""Дашборд (этап 6 доработок): выполнение, статусы заказов, потребность в ресурсах, повторяющиеся причины отклонений,
работники по участкам и квалификациям, загрузка участков на 3 месяца вперёд, недовыполнение плана за 3 месяца назад.

Бригадир видит только свой участок."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import distribution, order_status
from .models import (DayPlanSnapshot, ExtraTask, Fact, FactReason, Operation, OrderLine, Plan, PlanItem, PlannerSettings, Routing,
                     User, WorkCenter, Worker)

WEEKS = 13                      # ≈ 3 месяца
MONTH_DAYS = 30                 # окно «Статуса заказов» на дашборде: плановый срок заверш. — ближайший месяц
LOAD_DAYS = 92                  # «ближайшие 3 месяца» для среднего процента загрузки
NO_SECTION = 0


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _weeks_forward(today: date) -> list:
    m = monday(today)
    return [m + timedelta(weeks=i) for i in range(WEEKS)]


def _weeks_back(today: date) -> list:
    m = monday(today)
    return [m - timedelta(weeks=WEEKS - 1 - i) for i in range(WEEKS)]


def build(db: Session, user: User, today: date) -> dict:
    t = user.tenant_id
    plan: Optional[Plan] = db.scalar(select(Plan).where(Plan.tenant_id == t, Plan.status == "active"))
    st = db.get(PlannerSettings, t)
    quals = (st.qualifications if st else None) or []
    own = user.work_center_id if user.role == "foreman" else None
    centers = {c.id: c.name for c in db.scalars(select(WorkCenter).where(WorkCenter.tenant_id == t).order_by(WorkCenter.name))}
    if own is not None:
        centers = {k: v for k, v in centers.items() if k == own}
    sections = [{"id": k, "name": v} for k, v in centers.items()]
    all_workers = list(db.scalars(select(Worker).where(Worker.tenant_id == t)))
    workers = [w for w in all_workers if own is None or w.work_center_id == own]
    wcenter = {w.id: (w.work_center_id or NO_SECTION) for w in all_workers}
    cname = {**centers, NO_SECTION: "Без участка"}

    # ---------- статусы заказов и выполнение ----------
    rows = list(plan.summary.get("status", [])) if plan else []
    if own is not None:
        mine = distribution.order_ids_of_center(db, user, own)
        rows = [r for r in rows if r.get("order_line_id") in mine]
    by_status = Counter(r["status"] for r in rows)
    ol_ids = [r["order_line_id"] for r in rows]
    prog = _progress(db, t, ol_ids)
    for r in rows:
        r["progress"] = prog["per_order"].get(r["order_line_id"], 0.0)
    risk = sorted((r for r in rows if r["status"] == "Риск опоздания"), key=lambda r: (r.get("due") or "9999"))
    soon = sorted((r for r in rows if r["status"] != "Готово" and r.get("due")), key=lambda r: r["due"])[:5]

    # ---------- ключевые показатели и заказы ближайшего месяца ----------
    kpi = _kpi(db, t, rows, prog["per_order"], plan, today, workers, own)
    orders_month = _orders_month(rows, today)

    # ---------- загрузка участков на 3 месяца вперёд ----------
    load = _load_forecast(plan, today, sections, own)
    # ---------- недовыполнение за 3 месяца назад ----------
    under = _underperformance(db, t, today, centers, own)
    # ---------- повторяющиеся причины отклонений ----------
    reasons = _reasons(db, t, today, wcenter, cname, own)
    # ---------- потребность в ресурсах ----------
    resources = _resources(db, user, rows, risk, load, sections, workers, today)

    return {
        "plan_id": plan.id if plan else None, "today": today.isoformat(),
        "progress": {"percent": prog["percent"], "done": prog["done"], "required": prog["required"], "orders": len(rows)},
        "kpi": kpi, "orders_month": orders_month,
        "missing_fact": missing_fact_summary(db, user, today),
        "by_status": dict(by_status), "risk": risk[:8], "soonest": soon,
        "workers": _workers(workers, sections, quals, cname),
        "resources": resources, "reasons": reasons, "load": load, "underperformance": under,
        "week_peak_load": max([v for s in load["series"] for v in s["values"] if v is not None], default=0),
        "loaded_today": plan.summary.get("loaded_today", []) if plan else [],
    }


# ------------------------------------------------------------------ части
def missing_fact_summary(db: Session, user: User, today: date) -> dict:
    """Невнесённый факт за прошедшие дни (последние 31 день): сколько дней, заданий и самый ранний день — чтобы перейти и внести."""
    from .reports_service import missing_fact_days
    days = missing_fact_days(db, user, today)
    return {"days": len(days), "tasks": sum(d["count"] for d in days), "first": days[0]["date"] if days else None,
            "last": days[-1]["date"] if days else None}


def _kpi(db: Session, t: int, rows: list, per_order: dict, plan: Optional[Plan], today: date, workers: list, own) -> dict:
    """Пять показателей дашборда.

    Проект «в работе» — есть незавершённая позиция, по которой уже внесён факт; «в очереди» — есть незавершённые позиции,
    но по ним факта ещё нет; «с риском срыва сроков» — есть позиция со статусом «Риск опоздания» (проект может быть
    одновременно в работе или в очереди). Работники с заданиями — получившие на сегодня строку плана или работу вне перечня.
    Загрузка — средний процент рабочего дня по работникам и рабочим дням плана на ближайшие 3 месяца."""
    ids = [r["order_line_id"] for r in rows]
    proj_of = {o.id: o.project_id for o in db.scalars(select(OrderLine).where(OrderLine.id.in_(ids)))} if ids else {}
    started, queued, risky, projects = set(), set(), set(), set()
    for r in rows:
        pid = proj_of.get(r["order_line_id"])
        if pid is None or r["status"] in ("Готово", "Нет операций"):
            continue
        projects.add(pid)
        if r["status"] == "Риск опоздания":
            risky.add(pid)
        if per_order.get(r["order_line_id"], 0.0) > 0:
            started.add(pid)
    queued = projects - started

    wids = {w.id for w in workers}
    busy = set()
    if plan:
        busy |= {w for (w,) in db.execute(select(PlanItem.worker_id).where(
            PlanItem.plan_id == plan.id, PlanItem.plan_date == today, PlanItem.qty > 0).distinct())}
    busy |= {w for (w,) in db.execute(select(ExtraTask.worker_id).where(
        ExtraTask.tenant_id == t, ExtraTask.plan_date == today).distinct())}
    busy &= wids

    load_pct, horizon = None, None
    lm = (plan.summary.get("load") if plan else None) or {}
    info, plan_load = lm.get("worker_info", {}), lm.get("plan", {})
    names = [n for n in lm.get("workers", []) if own is None or info.get(n, {}).get("work_center_id") == own]
    lo, hi = today.isoformat(), (today + timedelta(days=LOAD_DAYS)).isoformat()
    days = [d for d in lm.get("dates", []) if lo <= d <= hi]
    if names and days:
        load_pct = round(sum(plan_load.get(n, {}).get(d, 0.0) for n in names for d in days) / (len(names) * len(days)) * 100, 1)
        horizon = days[-1]
    return {"projects_in_work": len(started), "projects_queued": len(queued), "projects_at_risk": len(risky),
            "workers_with_tasks": len(busy), "workers_total": len(wids), "load_3m": load_pct, "load_horizon_end": horizon}


def _orders_month(rows: list, today: date) -> list:
    """Заказы с плановым сроком завершения в ближайший месяц (и просроченные, но не завершённые) — одной таблицей."""
    end = (today + timedelta(days=MONTH_DAYS)).isoformat()
    out = []
    for r in rows:
        due = r.get("due")
        if not due or due > end:
            continue
        overdue = due < today.isoformat()
        if overdue and r["status"] == "Готово":
            continue
        out.append({"order_line_id": r["order_line_id"], "id": r["id"], "project": r["project"], "product": r["product"],
                    "qty": r.get("qty"), "progress": r.get("progress", 0.0), "due": due, "forecast": r.get("forecast"),
                    "forecast_text": r.get("forecast_text"), "status": r["status"], "overdue": overdue})
    out.sort(key=lambda r: (r["due"], str(r["id"])))
    return out


def _progress(db: Session, t: int, ol_ids: list) -> dict:
    if not ol_ids:
        return {"percent": 0.0, "done": 0.0, "required": 0.0, "per_order": {}}
    ols = list(db.scalars(select(OrderLine).where(OrderLine.id.in_(ol_ids))))
    routing_ops: dict = defaultdict(list)
    for op in db.scalars(select(Operation).where(Operation.tenant_id == t)):
        routing_ops[op.routing_id].append(op)
    done = {(a, b): c for a, b, c in db.execute(
        select(Fact.order_line_id, Fact.operation_id, func.coalesce(func.sum(Fact.qty), 0.0))
        .where(Fact.tenant_id == t, Fact.deleted_at.is_(None), Fact.order_line_id.in_(ol_ids))
        .group_by(Fact.order_line_id, Fact.operation_id))}
    per, tot_done, tot_req = {}, 0.0, 0.0
    for o in ols:
        req = dn = 0.0
        for op in routing_ops.get(o.routing_id, []):
            r = math.ceil(o.qty * op.per_item - 1e-9)
            req += r
            dn += min(done.get((o.id, op.id), 0.0), r)
        per[o.id] = round(dn / req * 100, 1) if req else 0.0
        tot_done, tot_req = tot_done + dn, tot_req + req
    return {"percent": round(tot_done / tot_req * 100, 1) if tot_req else 0.0, "done": tot_done, "required": tot_req, "per_order": per}


def _workers(workers: list, sections: list, quals: list, cname: dict) -> dict:
    def cnt(ws):
        return {"total": len(ws), "available": sum(1 for w in ws if w.status == "available"),
                "temporary": sum(1 for w in ws if w.is_temporary), "manual_only": sum(1 for w in ws if not w.auto_plan)}
    ids = [s["id"] for s in sections] + ([NO_SECTION] if any(w.work_center_id is None for w in workers) else [])
    by_section = [{"section_id": i, "section": cname[i], **cnt([w for w in workers if (w.work_center_id or NO_SECTION) == i])} for i in ids]
    levels = max(len(quals), int(max([w.skill for w in workers], default=0)))
    lv = [{"level": i, "name": quals[i - 1] if i <= len(quals) else str(i),
           "total": sum(1 for w in workers if round(w.skill) == i),
           "by_section": {str(s): sum(1 for w in workers if round(w.skill) == i and (w.work_center_id or NO_SECTION) == s) for s in ids}}
          for i in range(1, levels + 1)]
    return {"total": len(workers), "available": sum(1 for w in workers if w.status == "available"),
            "by_section": by_section, "by_qualification": lv, "sections": [{"id": i, "name": cname[i]} for i in ids]}


def _load_forecast(plan: Optional[Plan], today: date, sections: list, own) -> dict:
    weeks = _weeks_forward(today)
    out = {"weeks": [w.isoformat() for w in weeks], "series": [], "horizon_end": None}
    lm = (plan.summary.get("load") if plan else None) or {}
    dates = lm.get("dates", [])
    if not dates:
        return out
    out["horizon_end"] = dates[-1]
    info, plan_load = lm.get("worker_info", {}), lm.get("plan", {})
    for s in sections:
        names = [n for n in lm.get("workers", []) if info.get(n, {}).get("work_center_id") == s["id"]]
        vals = []
        for w in weeks:
            lo, hi = w.isoformat(), (w + timedelta(days=7)).isoformat()
            days = [d for d in dates if lo <= d < hi]
            if not days or not names:
                vals.append(None)
                continue
            tot = sum(plan_load.get(n, {}).get(d, 0.0) for n in names for d in days)
            vals.append(round(tot / (len(names) * len(days)) * 100, 1))
        out["series"].append({"section_id": s["id"], "section": s["name"], "values": vals, "workers": len(names)})
    return out


def _underperformance(db: Session, t: int, today: date, centers: dict, own) -> dict:
    weeks = _weeks_back(today)
    start, end = weeks[0], today                       # сегодняшний день ещё не закончен — в расчёт не входит
    snaps = list(db.scalars(select(DayPlanSnapshot).where(DayPlanSnapshot.tenant_id == t, DayPlanSnapshot.plan_date >= start,
                                                          DayPlanSnapshot.plan_date < end)))
    first = db.scalar(select(func.min(DayPlanSnapshot.plan_date)).where(DayPlanSnapshot.tenant_id == t))
    facts: dict = defaultdict(float)
    for d, ol, op, w, q in db.execute(select(Fact.fact_date, Fact.order_line_id, Fact.operation_id, Fact.worker_id,
                                             func.coalesce(func.sum(Fact.qty), 0.0))
                                      .where(Fact.tenant_id == t, Fact.deleted_at.is_(None), Fact.fact_date >= start,
                                             Fact.fact_date < end).group_by(Fact.fact_date, Fact.order_line_id, Fact.operation_id, Fact.worker_id)):
        facts[(d, ol, op, w)] = q
    idx = {w: i for i, w in enumerate(weeks)}
    planned: dict = defaultdict(lambda: [0.0] * WEEKS)
    under: dict = defaultdict(lambda: [0.0] * WEEKS)
    for s in snaps:
        sid = s.work_center_id or NO_SECTION
        if own is not None and sid != own:
            continue
        wi = idx[monday(s.plan_date)]
        done = facts.get((s.plan_date, s.order_line_id, s.operation_id, s.worker_id), 0.0)
        planned[sid][wi] += s.qty
        under[sid][wi] += max(0.0, s.qty - done)
    ids = [i for i in list(centers) + [NO_SECTION] if i in planned]
    series = [{"section_id": i, "section": centers.get(i, "Без участка"), "planned": [round(x, 1) for x in planned[i]],
               "under": [round(x, 1) for x in under[i]],
               "percent": [round(u / p * 100, 1) if p else None for u, p in zip(under[i], planned[i])]} for i in ids]
    return {"weeks": [w.isoformat() for w in weeks], "series": series, "since": first.isoformat() if first else None,
            "has_data": bool(snaps)}


def _reasons(db: Session, t: int, today: date, wcenter: dict, cname: dict, own) -> list:
    start = today - timedelta(days=90)
    names = {r.id: r.name for r in db.scalars(select(FactReason).where(FactReason.tenant_id == t))}
    ops = {o.id: o.name for o in db.scalars(select(Operation).where(Operation.tenant_id == t))}
    groups: dict = defaultdict(list)
    for f in db.scalars(select(Fact).where(Fact.tenant_id == t, Fact.deleted_at.is_(None), Fact.fact_date >= start)):
        if not (f.reason_id or (f.note or "").strip()):
            continue
        if own is not None and wcenter.get(f.worker_id) != own:
            continue
        groups[names.get(f.reason_id, "Без причины (комментарий)") if f.reason_id else "Без причины (комментарий)"].append(f)
    out = []
    for name, fs in groups.items():
        out.append({
            "reason": name, "count": len(fs), "days": len({f.fact_date for f in fs}), "workers": len({f.worker_id for f in fs}),
            "last_date": max(f.fact_date for f in fs).isoformat(), "repeating": len(fs) >= 2,
            "operations": [{"name": ops.get(o, "?"), "count": c} for o, c in Counter(f.operation_id for f in fs).most_common(3)],
            "sections": [{"name": cname.get(s, "Без участка"), "count": c} for s, c in Counter(wcenter.get(f.worker_id, NO_SECTION) for f in fs).most_common(3)],
            "notes": list(dict.fromkeys(f.note.strip() for f in sorted(fs, key=lambda f: f.fact_date, reverse=True) if (f.note or "").strip()))[:2],
        })
    out.sort(key=lambda r: (-r["count"], r["reason"]))
    return out


def _resources(db: Session, user: User, rows: list, risk: list, load: dict, sections: list, workers: list, today: date) -> dict:
    """Где не хватает людей. Берём заказы в риске (до 25), по их операциям — структурированные рекомендации order_status."""
    per: dict = {s["id"]: {"section_id": s["id"], "section": s["name"], "add_workers": 0, "items": [], "qualification": [], "late_ops": []} for s in sections}
    by_name = {s["name"]: s["id"] for s in sections}
    # проверяем не только «Риск опоздания»: заказ, который не выполняется в срок, может иметь и другой статус —
    # «Нет плана» (объём не уложился в горизонт), «В работе» с неназначенным остатком или с уже прошедшим сроком
    seen = {r["order_line_id"] for r in risk}
    extra = [r for r in rows if r["order_line_id"] not in seen and r["status"] not in ("Готово", "Нет операций")
             and (r["status"] == "Нет плана" or (r.get("due") and r["due"] < today.isoformat()))]
    extra.sort(key=lambda r: (r.get("due") or "9999"))
    checked = (list(risk) + extra)[:25]
    for r in checked:
        ol = db.get(OrderLine, r["order_line_id"])
        if ol is None:
            continue
        st = order_status.build(db, user, ol, today)
        due = st["order"]["due_date"]
        for op in st["operations"]:
            for sname in op["sections"]:
                sid = by_name.get(sname)
                if sid is None:
                    continue
                if op["staffing"] and op["staffing"]["add"] > 0:
                    per[sid]["add_workers"] = max(per[sid]["add_workers"], op["staffing"]["add"])
                    per[sid]["items"].append({"order_line_id": r["order_line_id"], "order": r["id"], "product": r["product"],
                                              "operation": op["name"], "add": op["staffing"]["add"], "current": op["staffing"]["current"],
                                              "need": op["staffing"]["need"], "spare": op["staffing"].get("spare", 0),
                                              "end_date": op["end_date"], "due": due, "unplanned": op["unplanned"]})
                if op["status"] != "Готово" and (op["late"] or op["unplanned"] > 0 or op["bottleneck_critical"]) \
                        and not (op["staffing"] and op["staffing"]["add"] > 0):
                    per[sid]["late_ops"].append({"order_line_id": r["order_line_id"], "order": r["id"], "operation": op["name"],
                                                 "end_date": op["end_date"], "due": due, "unplanned": op["unplanned"],
                                                 "reason": "не весь объём в плане" if op["unplanned"] > 0 else "завершится позже срока"})
                for p in op["problems"]:
                    if p["code"] == "NO_QUALIFIED":
                        per[sid]["qualification"].append({"order": r["id"], "operation": op["name"], "text": p["text"]})
    wcount = Counter(w.work_center_id for w in workers)
    for s in load["series"]:
        p = per.get(s["section_id"])
        if p is None:
            continue
        vals = [v for v in s["values"] if v is not None]
        p["peak_load"] = max(vals, default=None)
        p["overload_weeks"] = sum(1 for v in vals if v > 100)
        p["workers"] = wcount.get(s["section_id"], 0)
    for p in per.values():
        p["items"].sort(key=lambda i: -i["add"])
        p["items"] = p["items"][:5]
        p["qualification"] = p["qualification"][:3]
        p["late_ops"] = p["late_ops"][:5]
        p.setdefault("peak_load", None); p.setdefault("overload_weeks", 0); p.setdefault("workers", wcount.get(p["section_id"], 0))
        p["needs_more"] = bool(p["add_workers"] or p["qualification"] or p["late_ops"] or p["overload_weeks"])
    return {"sections": list(per.values()), "orders_checked": len(checked), "any_need": any(p["needs_more"] for p in per.values())}
