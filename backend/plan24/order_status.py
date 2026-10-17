"""Статус заказа по операциям (этап 5 доработок): выполнено, распланировано, смены до завершения, дата завершения,
узкое место, проблемы и рекомендации. Считается на лету по активному плану и факту.

Узкое место — операция, определяющая срок завершения заказа: нераспланированная целиком, а иначе с самой поздней датой
завершения. «Критичное» — если из-за неё заказ не успевает к сроку. Рекомендация по людям: сколько работников нужно, чтобы
закончить к сроку при текущих нормах и производительности, минус уже занятые на операции."""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import distribution
from .models import (DayPlanSnapshot, Fact, FactReason, Operation, OrderLine, OrderLineOperation, Plan, PlanItem, PlannerSettings, Product,
                     Project, Routing, User, WorkCenter, Worker)

FALLBACK_DAYS = 5          # если срок уже прошёл или не задан: «за сколько смен хотим закончить»


def _g(x: float) -> str:
    return f"{x:g}"


def _d(s: str) -> str:
    y, m, d = s.split("-")
    return f"{d}.{m}.{y}"


def build(db: Session, user: User, ol: OrderLine, today: date) -> dict:
    t = user.tenant_id
    plan: Optional[Plan] = db.scalar(select(Plan).where(Plan.tenant_id == t, Plan.status == "active"))
    st = db.get(PlannerSettings, t)
    quals = (st.qualifications if st else None) or []
    wdays: list = list(plan.summary.get("work_days", [])) if plan else []
    plan_today = plan.params.get("today") if plan else today.isoformat()
    due = ol.due_date.isoformat() if ol.due_date else None

    routing = db.get(Routing, ol.routing_id)
    ops = sorted(routing.operations, key=lambda o: (o.sequence, o.id))
    centers = {c.id: c.name for c in db.scalars(select(WorkCenter).where(WorkCenter.tenant_id == t))}
    assign: dict = defaultdict(set)
    for a in db.scalars(select(OrderLineOperation).where(OrderLineOperation.order_line_id == ol.id)):
        assign[a.operation_id].add(a.work_center_id)
    workers = {w.id: w for w in db.scalars(select(Worker).where(Worker.tenant_id == t))}
    reasons = {r.id: r.name for r in db.scalars(select(FactReason).where(FactReason.tenant_id == t))}
    facts = list(db.scalars(select(Fact).where(Fact.tenant_id == t, Fact.order_line_id == ol.id, Fact.deleted_at.is_(None))))
    items = list(db.scalars(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.order_line_id == ol.id))) if plan else []
    warns: dict = defaultdict(list)
    for w in (plan.warnings if plan else []):
        if w.get("order_line_id") == ol.id:
            warns[w.get("operation")].append(w)
    row = next((r for r in (plan.summary.get("status", []) if plan else []) if r.get("order_line_id") == ol.id), None)

    def shifts(a: str, b: str) -> int:             # рабочих смен в [a, b] по календарю плана
        return max(0, bisect_right(wdays, b) - bisect_left(wdays, a))

    own = user.work_center_id if user.role == "foreman" else None
    calc = []
    for op in ops:
        required = math.ceil(ol.qty * op.per_item - 1e-9)
        fo = [f for f in facts if f.operation_id == op.id]
        done = sum(f.qty for f in fo)
        fkeys = {(f.fact_date.isoformat(), f.worker_id) for f in fo if f.qty > 0}
        open_items = [i for i in items if i.operation_id == op.id and i.plan_date.isoformat() >= plan_today
                      and (i.plan_date.isoformat(), i.worker_id) not in fkeys]
        planned = sum(i.qty for i in open_items)
        remaining = max(0.0, required - done)
        unplanned = max(0.0, remaining - planned)
        if remaining <= 1e-9:
            end = max((f.fact_date.isoformat() for f in fo), default=None)
        elif unplanned <= 1e-9 and open_items:
            end = max(i.plan_date.isoformat() for i in open_items)
        else:
            end = None
        by_worker = Counter()
        for i in open_items:
            by_worker[i.worker_id] += i.qty
        calc.append({"op": op, "required": required, "done": done, "planned": planned, "remaining": remaining,
                     "unplanned": unplanned, "end": end, "facts": fo, "workers": [w for w, _ in by_worker.most_common()]})

    # узкое место: операция, определяющая срок завершения (по всем операциям заказа)
    open_ops = [c for c in calc if c["remaining"] > 1e-9]
    bott = max(open_ops, key=lambda c: (c["unplanned"] > 1e-9, c["end"] or "", c["remaining"])) if open_ops else None
    forecast = None if (not open_ops or any(c["unplanned"] > 1e-9 for c in open_ops)) else max(c["end"] for c in open_ops)
    late_days = None
    if forecast and due and forecast > due:
        late_days = (date.fromisoformat(forecast) - date.fromisoformat(due)).days
    order_late = bool(open_ops) and (bool(bott and bott["unplanned"] > 1e-9) or late_days is not None)

    out_ops = []
    for c in calc:
        op = c["op"]
        cs = assign.get(op.id, set())
        if own is not None and own not in cs:
            continue                                  # участок видит только свой набор операций
        has_centers = bool(centers)
        problems, recs = [], []
        staffing = None
        cand = [w for w in workers.values() if w.status == "available" and w.auto_plan and w.skill >= op.min_skill
                and (not has_centers or w.work_center_id in cs)]
        on_op = [workers[i] for i in c["workers"] if i in workers]
        is_b = bott is not None and c is bott
        late = bool(c["end"] and due and c["end"] > due)
        if c["remaining"] > 1e-9:
            if has_centers and not cs:
                problems.append({"code": "NOT_DISTRIBUTED", "text": "Операция не распределена по участкам — в план не попадает"})
                recs.append("Распределить операцию по участкам: «Проекты и Заказы → Заказы → По участкам»")
            for w in warns.get(op.name, []):
                if w["code"] in ("NO_WORKERS", "HORIZON_EXCEEDED", "PRIORITY_HORIZON"):
                    problems.append({"code": w["code"], "text": w["text"].split(": ", 1)[-1]})
            if not cand and (cs or not has_centers):
                lvl = quals[int(op.min_skill) - 1] if 0 < op.min_skill <= len(quals) and float(op.min_skill).is_integer() else _g(op.min_skill)
                problems.append({"code": "NO_QUALIFIED", "text": f"Нет доступных работников с квалификацией не ниже «{lvl}»"})
                recs.append("Повысить квалификацию работников участка, назначить участок с подходящими работниками или снизить требуемый уровень операции")
            if op.hold_qty and (op.hold_until is None or op.hold_until >= today):
                until = f" до {_d(op.hold_until.isoformat())}" if op.hold_until else ""
                problems.append({"code": "HOLD", "text": f"Заблокировано изделий: {_g(op.hold_qty)}{until}" + (f" — {op.hold_reason}" if op.hold_reason else "")})
                recs.append("Снять блокировку или устранить её причину" + (f": {op.hold_reason}" if op.hold_reason else ""))
            if late:
                problems.append({"code": "LATE", "text": f"Завершится {_d(c['end'])} — позже срока заказа {_d(due)}"})
        rc = Counter(reasons.get(f.reason_id, "причина удалена") for f in c["facts"] if f.reason_id)
        if rc:
            problems.append({"code": "REASONS", "text": "Причины отклонений: " + ", ".join(f"{n} ×{k}" for n, k in rc.most_common())})
            for n, k in rc.most_common():
                if k >= 2:
                    recs.append(f"Разобраться с повторяющейся причиной «{n}» ({k} раза)")
        # рекомендация по людям
        if c["remaining"] > 1e-9 and (late or c["unplanned"] > 1e-9 or (is_b and order_late)) and cand:
            days = shifts(plan_today, due) if due else 0
            target = days if days > 0 else FALLBACK_DAYS
            pool = on_op or cand
            perf = sum(w.perf for w in pool) / len(pool)
            need = math.ceil(c["remaining"] / (op.norm_per_day * perf * target) - 1e-9)
            cur = len(on_op)
            if need > cur:
                staffing = {"need": need, "current": cur, "add": need - cur, "target_days": target}
                spare = [w for w in sorted(cand, key=lambda w: -w.perf) if w.id not in c["workers"]]
                goal = f"закончить к {_d(due)}" if days > 0 else f"закончить за {target} смен"
                txt = f"Добавить ориентировочно {need - cur} работн. (сейчас {cur}, нужно около {need}), чтобы {goal}"
                if not spare:
                    txt += ". Свободных подходящих работников на участке нет — нужен перевод с другого участка или новые люди"
                else:
                    staffing["spare"] = len(spare)
                    txt += ". Подходят по квалификации: " + ", ".join(w.name for w in spare[:3])
                    if need > cur + len(spare):          # людей, которых можно подключить, меньше, чем нужно для срока
                        full = math.ceil(c["remaining"] / (op.norm_per_day * perf * (cur + len(spare))) - 1e-9)
                        txt += f". Даже со всеми подходящими работниками ({cur + len(spare)}) понадобится около {full} смен — рассмотрите перенос срока"
                recs.append(txt)
        if is_b and order_late and not recs:
            recs.append("Операция определяет срок заказа: проверить норму и условия работы, при необходимости усилить участок")
        # человеко-смен до завершения: сколько полных рабочих дней одного работника нужно на оставшийся объём
        # (норма × средняя производительность работников операции × коэффициент эффективности)
        pool_perf = (sum(w.perf for w in (on_op or cand)) / len(on_op or cand)) if (on_op or cand) else 1.0
        person_shifts = 0.0 if c["remaining"] <= 1e-9 else round(c["remaining"] / (op.norm_per_day * pool_perf * (st.efficiency if st else 1.0)), 1)
        status = ("Готово" if c["remaining"] <= 1e-9 else "Не распределена" if (has_centers and not cs)
                  else "Не весь объём в плане" if c["unplanned"] > 1e-9 else "В плане")
        out_ops.append({
            "operation_id": op.id, "name": op.name, "sequence": op.sequence, "norm_per_day": op.norm_per_day, "per_item": op.per_item,
            "min_skill": op.min_skill, "sections": sorted(centers[x] for x in cs if x in centers),
            "required": c["required"], "done": c["done"], "planned": c["planned"], "unplanned": c["unplanned"], "remaining": c["remaining"],
            "end_date": c["end"], "shifts_left": (0 if c["remaining"] <= 1e-9 else shifts(plan_today, c["end"]) if c["end"] else None),
            "person_shifts_left": person_shifts,
            "workers": [w.name for w in on_op], "status": status, "is_bottleneck": is_b,
            "bottleneck_critical": bool(is_b and order_late), "late": late, "problems": problems, "recommendations": recs,
            "staffing": staffing,
        })
    req = sum(c["required"] for c in calc) or 1
    prog = sum(min(c["done"], c["required"]) for c in calc) / req
    product, project = db.get(Product, ol.product_id), db.get(Project, ol.project_id)
    return {
        "plan_id": plan.id if plan else None,
        "order": {"order_line_id": ol.id, "order": ol.ext_id or f"#{ol.id}", "product": product.name if product else "",
                  "project": project.name if project else "", "customer": ol.customer or (project.customer if project else ""),
                  "qty": ol.qty, "due_date": due, "version": routing.version_no, "version_name": routing.name,
                  "status": (row or {}).get("status") or ("Готово" if not open_ops and calc else "Нет плана"),
                  "progress": round(prog * 100, 1), "forecast_end": forecast, "late_days": late_days,
                  "bottleneck": (bott["op"].name if bott and own is None else None)},
        "operations": out_ops,
    }


def idle_by_order(db: Session, user: User, today: date, order_ids: list, center: Optional[int] = None) -> dict:
    """«Простой» заказа = недовыполнение плана за прошедшие дни: Σ max(0, план − факт) / Σ план по связкам день × операция ×
    работник, в нормо-днях (штуки ÷ норма операции за день). Сегодняшний день не закончен — не входит. Нет плана за прошлое
    (снимки ведутся с момента первого расчёта) — значения нет. center — для бригадира: только план его участка."""
    if not order_ids:
        return {}
    q = select(DayPlanSnapshot).where(DayPlanSnapshot.tenant_id == user.tenant_id, DayPlanSnapshot.plan_date < today,
                                      DayPlanSnapshot.order_line_id.in_(order_ids))
    if center is not None:
        q = q.where(DayPlanSnapshot.work_center_id == center)
    snaps = list(db.scalars(q))
    if not snaps:
        return {}
    norms = {o.id: o.norm_per_day for o in db.scalars(select(Operation).where(Operation.id.in_({x.operation_id for x in snaps})))}
    facts = {(d, ol, op, w): v for d, ol, op, w, v in db.execute(
        select(Fact.fact_date, Fact.order_line_id, Fact.operation_id, Fact.worker_id, func.coalesce(func.sum(Fact.qty), 0.0))
        .where(Fact.tenant_id == user.tenant_id, Fact.deleted_at.is_(None), Fact.fact_date < today, Fact.order_line_id.in_(order_ids))
        .group_by(Fact.fact_date, Fact.order_line_id, Fact.operation_id, Fact.worker_id))}
    plan, short = defaultdict(float), defaultdict(float)
    for x in snaps:
        norm = norms.get(x.operation_id)
        if not norm:
            continue
        done = facts.get((x.plan_date, x.order_line_id, x.operation_id, x.worker_id), 0.0)
        plan[x.order_line_id] += x.qty / norm
        short[x.order_line_id] += max(0.0, x.qty - done) / norm
    return {i: {"plan_days": round(plan[i], 2), "short_days": round(short[i], 2), "pct": round(short[i] / plan[i] * 100, 1)}
            for i in plan if plan[i] > 0}
