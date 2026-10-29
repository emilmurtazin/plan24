"""Отчёты поверх результата планирования (BR-14, FR-26…FR-28).

Считаются отдельными функциями, а не внутри планировщика (ТЗ 8.2, п. 7).
Перенос writeStatusSheet / writeLoadSheet из Code_v3.5.gs.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from .models import PlanInput, PlanResult

STATUS_DONE = "Готово"
STATUS_RISK = "Риск опоздания"
STATUS_NO_PLAN = "Нет плана"
STATUS_NO_OPS = "Нет операций"
STATUS_IN_PROGRESS = "В работе"


def status_rows(inp: PlanInput, res: PlanResult) -> list:
    """Статус позиций заказа (лист «Статус заказов»)."""
    rows = []
    wd = res.work_days
    for order in sorted(inp.order_lines, key=lambda o: (o.priority, o.due.toordinal() if o.due else 10**9)):
        st = res.order_status.get(order.id)
        if st is None:
            continue
        required, done, planned = st.required_ops, st.done_ops, st.planned_ops
        left = required - done
        pct = round(done / required * 100) if required else 0
        forecast = None
        if st.no_ops or required == 0:
            status, forecast_text = STATUS_NO_OPS, "—"
        elif left <= 0:
            status, forecast_text = STATUS_DONE, "Выполнено"
        elif planned == 0:
            status, forecast_text = STATUS_NO_PLAN, "Не запланировано"
        else:
            forecast = wd[st.last_day_idx]
            forecast_text = forecast.strftime("%d.%m.%Y")
            status = STATUS_IN_PROGRESS
            if order.due is not None and forecast > order.due:
                status = STATUS_RISK
        rows.append({
            "id": order.id, "project": order.project, "product": order.product, "qty": order.qty,
            "required_ops": required, "done_ops": done, "left_ops": left,
            "pct": pct if required else None,
            "forecast": forecast.isoformat() if forecast else None,
            "forecast_text": forecast_text,
            "due": order.due.isoformat() if order.due else None,
            "priority": order.priority,
            "status": status,
        })
    return rows


def _day_cap(norm_by_op: dict, perf_by_name: dict, product: str, op: str, worker: str, eff: float):
    norm = norm_by_op.get((product, op))
    if not norm:
        return None
    return norm * (perf_by_name.get(worker) or 1) * eff


def load_matrix(inp: PlanInput, res: PlanResult) -> dict:
    """Загрузка исполнитель × день: план, факт, отклонение (в долях рабочего дня)."""
    eff = inp.config.efficiency
    eff_of = dict(inp.all_workers_eff) or {w.name: w.efficiency for w in inp.workers if w.efficiency is not None}    # эффективность участков
    today = res.work_days[0] if res.work_days else inp.config.today
    norm_by_op = {(p, o.name): o.norm_per_day for p, ops in inp.products.items() for o in ops}
    perf = dict(inp.all_workers_perf) or {w.name: w.perf for w in inp.workers}

    plan_data: dict = defaultdict(float)
    fact_data: dict = defaultdict(float)
    dates: set = set()
    names: set = set()

    # в скрипте план-строки на сегодня/завтра попадают в «Факт» до построения загрузки
    for f in list(inp.facts) + list(res.new_fact_rows):
        if not (f.date and f.order_id and f.product and f.op and f.worker):
            continue
        cap = _day_cap(norm_by_op, perf, f.product, f.op, f.worker, eff_of.get(f.worker, eff))
        if not cap:
            continue
        dates.add(f.date)
        names.add(f.worker)
        if f.plan > 0 and f.date <= today:
            plan_data[(f.date, f.worker)] += f.plan / cap
        if f.qty > 0:
            fact_data[(f.date, f.worker)] += f.qty / cap

    for it in res.items:
        if it.qty <= 0 or it.date <= today:
            continue
        cap = _day_cap(norm_by_op, perf, it.product, it.operation, it.worker, eff_of.get(it.worker, eff))
        if not cap:
            continue
        plan_data[(it.date, it.worker)] += it.qty / cap
        dates.add(it.date)
        names.add(it.worker)

    dates.update(res.work_days)
    sorted_dates = sorted(dates)
    worker_list = list(dict.fromkeys([w.name for w in inp.workers] + sorted(names - {w.name for w in inp.workers})))

    def r2(x):
        return round(x + 0.0, 2)

    plan_m, fact_m, dev_m = {}, {}, {}
    for n in worker_list:
        plan_m[n], fact_m[n], dev_m[n] = {}, {}, {}
        for d in sorted_dates:
            p = plan_data.get((d, n), 0)
            f = fact_data.get((d, n), 0)
            if p > 0:
                plan_m[n][d.isoformat()] = r2(p)
            if f > 0:
                fact_m[n][d.isoformat()] = r2(f)
            if (p > 0 or f > 0) and d <= today:
                dev_m[n][d.isoformat()] = r2(f - p)
    return {
        "dates": [d.isoformat() for d in sorted_dates],
        "workers": worker_list,
        "plan": plan_m, "fact": fact_m, "deviation": dev_m,
        "today": today.isoformat(),
    }


def day_plan(res: PlanResult, day: date) -> dict:
    """План на день по исполнителям (FR-26)."""
    by_worker: dict = defaultdict(list)
    for it in res.items:
        if it.date == day and it.qty > 0:
            by_worker[it.worker].append({
                "order_id": it.order_id, "project": it.project, "product": it.product,
                "operation": it.operation, "qty": it.qty,
                "reason": it.reason_code, "frozen": it.is_frozen,
            })
    return {"date": day.isoformat(), "workers": [{"worker": w, "tasks": t} for w, t in sorted(by_worker.items())]}


def summary(inp: PlanInput, res: PlanResult) -> dict:
    rows = status_rows(inp, res)
    by_status: dict = defaultdict(int)
    for r in rows:
        by_status[r["status"]] += 1
    return {
        "items": len(res.items),
        "orders": len(rows),
        "by_status": dict(by_status),
        "warnings": len(res.warnings),
        "loaded_today": res.loaded_today,
        "first_day": res.work_days[0].isoformat() if res.work_days else None,
        "last_day": res.work_days[-1].isoformat() if res.work_days else None,
    }
