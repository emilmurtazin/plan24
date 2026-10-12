"""Планировщик Plan 24 — перенос distributeTasks() из Code_v3.5.gs.

Порядок операций, округления и сортировки сохранены намеренно: при жёсткости 100%
результат должен совпадать со скриптом строка в строку (тесты паритета, tests/parity).
Отличия от скрипта — только в docs/DEVIATIONS.md.

Правила ТЗ: BR-1…BR-12, BR-14 (статусы — в reports.py), BR-18 (reason_code), BR-19 (warnings).
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Optional

from .models import (
    FactRow, OrderLine, OrderStatus, Operation, PlanConfig, PlanInput,
    PlanItem, PlanResult, PlanWarning, Worker,
)

EPS = 1e-9


def _fmt(x) -> str:
    """Число как в JS-шаблонной строке: 5.0 -> '5'."""
    if isinstance(x, float) and x == int(x):
        return str(int(x))
    return str(x)


def _js_round(x: float) -> int:
    """Math.round из JS: .5 округляется вверх."""
    return math.floor(x + 0.5)


class _W:
    """Рабочее состояние исполнителя: свободная ёмкость по дням (в долях дня)."""
    __slots__ = ("name", "perf", "skill", "free", "allowed_key")

    def __init__(self, w: Worker, total_days: int, work_days: tuple):
        self.name = w.name
        self.perf = w.perf
        self.skill = w.skill
        self.free = [0.0 if work_days[i] in w.absent_days else 1.0 for i in range(total_days)]


def _allowed(w: _W, order: OrderLine) -> bool:
    return order.allowed_workers is None or (w.name in order.allowed_workers)


def _op_allowed(w: _W, order: OrderLine, op) -> bool:
    """Операция выполняется только работниками назначенных ей участков (если распределение задано)."""
    return order.op_workers is None or (w.name in order.op_workers.get(op.name, ()))


def allocate_fact(fact_by_pio: dict, batches_by_pi: dict, ops_by_product: dict) -> dict:
    """Факт по связке проект—изделие—операция раскладывается по партиям в порядке сроков (v3.4).

    Ранние партии закрываются первыми; излишек уходит на последнюю партию.
    """
    done: dict = {}
    for (project, product, op), total in fact_by_pio.items():
        batches = batches_by_pi.get((project, product))
        if not batches:
            continue
        op_info = next((o for o in ops_by_product.get(product, ()) if o.name == op), None)
        per_item = op_info.per_item if op_info else 1
        left = total
        for i, b in enumerate(batches):
            if left <= 0:
                continue
            cap = math.ceil(b.qty * per_item - EPS)
            give = left if i == len(batches) - 1 else min(left, cap)
            if give > 0:
                k = (b.id, op)
                done[k] = done.get(k, 0) + give
                left -= give
    return done


def group_batches(orders: list) -> dict:
    """Заказы одного проекта и изделия, по возрастанию срока (устойчиво)."""
    m: dict = defaultdict(list)
    for o in orders:
        m[(o.project, o.product)].append(o)
    inf = float("inf")
    for k in m:
        m[k].sort(key=lambda o: o.due.toordinal() if o.due else inf)
    return m


def plan(inp: PlanInput) -> PlanResult:
    cfg: PlanConfig = inp.config
    work_days = cfg.work_days
    total_days = len(work_days)
    result_items: list = []
    warnings: list = []
    status_map: dict = {}

    if not work_days or not inp.workers:
        warnings.append(PlanWarning("NO_WORKERS", "Нет доступных сборщиков."))
        return PlanResult([], warnings, {}, [], [], work_days)

    day_index = {d: i for i, d in enumerate(work_days)}
    today_key = work_days[0]
    eff = cfg.efficiency

    workers = [_W(w, total_days, work_days) for w in inp.workers]
    ops_by_product = {p: sorted(ops, key=lambda o: o.sequence) for p, ops in inp.products.items()}
    norm_by_op = {}
    for p, ops in inp.products.items():
        for o in ops:
            norm_by_op[(p, o.name)] = o.norm_per_day

    # --- порядок позиций: приоритет, затем срок (BR-4) ---
    inf = float("inf")
    orders = sorted(inp.order_lines, key=lambda o: (o.priority, o.due.toordinal() if o.due else inf))
    batches_by_pi = group_batches(orders)
    order_by_id = {o.id: o for o in orders}

    # --- факт ---
    fact_by_pio: dict = {}
    frozen_by_key: dict = {}      # (date, order_id, op, worker) -> plan qty
    existing_keys: set = set()
    today_fact_load: dict = defaultdict(float)
    worker_by_name = {w.name: w for w in workers}

    for f in inp.facts:
        if f.date is None or not f.product or not f.op:
            continue
        row_key = (f.date, f.order_id, f.op, f.worker)
        existing_keys.add(row_key)
        if f.qty > 0:
            proj = f.project
            if (proj, f.product) not in batches_by_pi and f.order_id in order_by_id:
                proj = order_by_id[f.order_id].project
            pio = (proj, f.product, f.op)
            fact_by_pio[pio] = fact_by_pio.get(pio, 0) + f.qty
            if f.date == today_key:
                norm = norm_by_op.get((f.product, f.op))
                if norm:
                    w = worker_by_name.get(f.worker)
                    perf = w.perf if w else 1
                    day_cap = norm * perf * eff
                    if day_cap > 0:
                        today_fact_load[f.worker] += f.qty / day_cap
        if f.qty == 0 and f.plan > 0:
            frozen_by_key[row_key] = f.plan

    # --- закреплённые вручную: день работника закрыт для автопланирования, строки остаются на месте ---
    for wn, d in inp.pinned_days:
        if wn in worker_by_name and d in day_index:
            worker_by_name[wn].free[day_index[d]] = 0.0
    pinned_by_pair: dict = defaultdict(list)
    for pi in inp.pinned:
        if pi.date in day_index and pi.qty > 0:
            pinned_by_pair[(pi.order_id, pi.op)].append((day_index[pi.date], pi))

    # индекс замороженных строк: (order_id, op) -> есть ли что искать (ускоряет поиск)
    frozen_pairs = {(k[1], k[2]) for k in frozen_by_key}

    done_map = allocate_fact(fact_by_pio, batches_by_pi, ops_by_product)

    loaded_today = [n for n, share in today_fact_load.items() if share >= cfg.today_load_threshold]
    loaded_today_set = set(loaded_today)

    # --- время и целые операции ---
    def op_days(op: Operation, w: _W) -> float:
        return 1 / (op.norm_per_day * w.perf * eff)

    def spend_time(w: _W, day: int, days: float) -> None:
        w.free[day] -= days
        if w.free[day] < EPS:
            over = max(0.0, -w.free[day])
            w.free[day] = 0.0
            d = day + 1
            while over > EPS and d < total_days:
                take = min(over, w.free[d])
                w.free[d] -= take
                over -= take
                d += 1

    # --- жёсткость приоритета: прогресс по уровням в нормо-днях (BR-4) ---
    prio_load: dict = {}

    def add_prio_load(p, day, normo_days):
        rec = prio_load.get(p)
        if rec is None:
            rec = prio_load[p] = {"total": 0.0, "by_day": [0.0] * total_days}
        rec["total"] += normo_days
        rec["by_day"][day] += normo_days

    def prio_share_day(p, share, fallback) -> int:
        if share <= 0:
            return -1
        rec = prio_load.get(p)
        if not rec or rec["total"] <= 0:
            return fallback
        cum = 0.0
        for d in range(total_days):
            cum += rec["by_day"][d]
            if cum >= share * rec["total"] - EPS:
                return d
        return fallback

    def strictness_for(p) -> float:
        return cfg.strictness_by_level.get(p, cfg.priority_strictness)

    new_fact_rows: list = []
    priority_last_day: dict = {}

    def emit(day, order, op, give, w, reason, frozen=False):
        result_items.append(PlanItem(work_days[day], order.id, order.project, order.product,
                                     op.name, give, w.name, reason, frozen))

    def schedule_op(order, op, remaining, candidates, from_day, to_day, reason, on_day_used):
        day = from_day
        while day <= to_day and remaining > 0:
            pool = [w for w in candidates if w.free[day] > 0.01]
            if day == 0:
                pool = [w for w in pool if w.name not in loaded_today_set]
            pool.sort(key=lambda w: (-(w.free[day] * w.perf), -w.perf))
            pool = pool[: cfg.max_workers_per_op]
            for w in pool:
                if remaining <= 0:
                    break
                one_op = op_days(op, w)
                give = math.floor(w.free[day] / one_op + EPS)
                if give < 1 and w.free[day] >= min(one_op * cfg.min_start_share, cfg.min_start_share) - EPS:
                    give = 1
                give = min(give, remaining)
                if give <= 0:
                    continue
                spend_time(w, day, give * one_op)
                add_prio_load(order.priority, day, give / op.norm_per_day)
                remaining -= give
                emit(day, order, op, give, w, reason)
                if day == 0 or day == 1:
                    rk = (work_days[day], order.id, op.name, w.name)
                    if rk not in existing_keys:
                        new_fact_rows.append(FactRow(work_days[day], order.id, order.project,
                                                     order.product, op.name, w.name, plan=give))
                        existing_keys.add(rk)
                st = status_map.get(order.id)
                if st and day > st.last_day_idx:
                    st.last_day_idx = day
                if on_day_used:
                    on_day_used(day)
            day += 1
        return remaining

    for order in orders:
        ops = ops_by_product.get(order.product)
        st = status_map.setdefault(order.id, OrderStatus(order=order))

        if not ops:
            warnings.append(PlanWarning("NO_OPERATIONS", f"{order.product} (ID {order.id}): нет операций",
                                        order.id))
            st.no_ops = True
            continue

        min_start_for_order = 0
        for p, p_last in priority_last_day.items():
            if p < order.priority:
                s = strictness_for(order.priority)
                last_day = p_last if s >= 100 else prio_share_day(p, s / 100, p_last)
                if last_day + 1 > min_start_for_order:
                    min_start_for_order = last_day + 1
        if min_start_for_order >= total_days:
            warnings.append(PlanWarning(
                "PRIORITY_HORIZON",
                f"{order.product} (ID {order.id}): не хватает горизонта для приоритета {_fmt(order.priority)}",
                order.id))
            st.no_ops = True
            continue

        deadline_idx = total_days - 1
        if order.due is not None:
            if order.due < work_days[0]:
                deadline_idx = 0
            else:
                idx = -1
                for i in range(total_days):
                    if work_days[i] <= order.due:
                        idx = i
                    else:
                        break
                deadline_idx = 0 if idx == -1 else idx
        if deadline_idx < min_start_for_order:
            deadline_idx = min_start_for_order

        by_seq: dict = {}
        for op in ops:
            by_seq.setdefault(op.sequence, []).append(op)
        groups = [by_seq[k] for k in sorted(by_seq)]

        group_progress: list = []
        order_max_day = min_start_for_order

        for gi, group_ops in enumerate(groups):
            ready_items = order.qty if gi == 0 else min(order.qty, group_progress[gi - 1][0])
            min_start_day = min_start_for_order
            if gi > 0:
                min_start_day = max(min_start_for_order, group_progress[gi - 1][1])
            group_last_day = min_start_day
            group_min_ready = order.qty

            for op in group_ops:
                total_required = math.ceil(order.qty * op.per_item - EPS)
                done_ops = done_map.get((order.id, op.name), 0)
                done_items = math.floor(done_ops / op.per_item)

                st.required_ops += total_required
                st.done_ops += done_ops

                hold = op.hold_qty or 0
                if op.hold_until is not None and work_days[0] >= op.hold_until:
                    hold = 0     # BR-8: удержание снимается автоматически
                blocked = min(hold, ready_items)
                available_items = max(0, ready_items - blocked)

                candidates = [w for w in workers if w.skill >= op.min_skill and _allowed(w, order) and _op_allowed(w, order, op)]
                sched_pool = [w for w in candidates if w.name not in inp.fixed_workers] if inp.fixed_workers else candidates

                already_ops = 0
                already_last_day = -1
                if (order.id, op.name) in frozen_pairs:
                    for day in range(total_days):
                        for w in candidates:
                            rk = (work_days[day], order.id, op.name, w.name)
                            planned = frozen_by_key.get(rk)
                            if planned:
                                pl = max(1, _js_round(planned))
                                already_ops += pl
                                emit(day, order, op, pl, w, "FROZEN", True)
                                spend_time(w, day, pl * op_days(op, w))
                                add_prio_load(order.priority, day, pl / op.norm_per_day)
                                if day > st.last_day_idx:
                                    st.last_day_idx = day
                                if day > already_last_day:
                                    already_last_day = day
                                if day > order_max_day:
                                    order_max_day = day
                                frozen_by_key[rk] = 0
                for di, pi in pinned_by_pair.get((order.id, op.name), ()):
                    already_ops += pi.qty
                    result_items.append(PlanItem(work_days[di], order.id, order.project, order.product, op.name,
                                                 pi.qty, pi.worker, pi.reason, False))
                    spend = pi.qty / op.norm_per_day
                    add_prio_load(order.priority, di, spend)
                    already_last_day = max(already_last_day, di)
                    order_max_day = max(order_max_day, di)
                    st.last_day_idx = max(st.last_day_idx, di)
                st.planned_ops += already_ops

                already_items = math.floor(already_ops / op.per_item)
                done_items_this = done_items + already_items
                remaining_items = max(0, available_items - done_items_this)
                remaining_ops = math.ceil(remaining_items * op.per_item - EPS)

                before = remaining_ops
                last_planned_day = already_last_day

                def on_day_used(d):
                    nonlocal last_planned_day, order_max_day
                    if d > last_planned_day:
                        last_planned_day = d
                    if d > order_max_day:
                        order_max_day = d

                if remaining_ops > 0:
                    if order.op_workers is not None and op.name not in order.op_workers:
                        warnings.append(PlanWarning(
                            "NOT_DISTRIBUTED",
                            f"{order.product} (ID {order.id}) / {op.name}: операция не распределена по участкам",
                            order.id, op.name))
                    elif not sched_pool and candidates and order.op_workers is not None:
                        pass        # участок операции сейчас не пересчитывается (частичное перепланирование)
                    elif not sched_pool:
                        warnings.append(PlanWarning(
                            "NO_WORKERS",
                            f"{order.product} (ID {order.id}) / {op.name}: нет подходящих сборщиков",
                            order.id, op.name))
                    else:
                        remaining_ops = schedule_op(order, op, remaining_ops, sched_pool,
                                                    min_start_day, deadline_idx, "FREE_CAPACITY", on_day_used)
                        if remaining_ops > 0 and deadline_idx < total_days - 1:
                            late_from = deadline_idx + 1
                            if cfg.fix_group_start_after_deadline:
                                late_from = max(late_from, min_start_day)   # D-04 (BR-6)
                            remaining_ops = schedule_op(order, op, remaining_ops, sched_pool,
                                                        late_from, total_days - 1, "AFTER_DEADLINE",
                                                        on_day_used)
                        if remaining_ops > 0:
                            warnings.append(PlanWarning(
                                "HORIZON_EXCEEDED",
                                f"{order.product} (ID {order.id}) / {op.name}: не влезает в горизонт, остаток {_fmt(remaining_ops)}",
                                order.id, op.name))

                planned_ops = before - remaining_ops
                st.planned_ops += planned_ops

                if last_planned_day > group_last_day:
                    group_last_day = last_planned_day

                total_planned_ops = done_ops + already_ops + planned_ops
                total_planned_items = math.floor(total_planned_ops / op.per_item)
                next_ready = min(total_planned_items, available_items)
                if next_ready < group_min_ready:
                    group_min_ready = next_ready

            group_progress.append((max(0, group_min_ready), group_last_day))

        if order.priority not in priority_last_day:
            priority_last_day[order.priority] = order_max_day
        elif order_max_day > priority_last_day[order.priority]:
            priority_last_day[order.priority] = order_max_day

    return PlanResult(result_items, warnings, status_map, loaded_today, new_fact_rows, work_days)
