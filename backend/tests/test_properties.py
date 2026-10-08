"""Property-тесты (ТЗ 8.4) и тесты осознанных отличий от скрипта."""
import random
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta

from hypothesis import given, settings, strategies as st

from plan24.compat import build_input
from plan24.planner import FactRow, Operation, PlanInput, plan, reports
from plan24.synthetic import ROUTES, NAMES, _ops, _orders, _workers

TODAY = date(2026, 10, 2)


def random_dataset(seed: int, n_orders: int, n_workers: int, strict: int):
    rng = random.Random(seed)
    key = rng.choice(list(ROUTES))
    route = ROUTES[key]
    return {"orders": _orders(route, rng, TODAY, n_orders, prio_levels=rng.randint(1, 4)),
            "ops": _ops(route, rng), "workers": _workers(rng, n_workers, 3), "holidays": [],
            "facts": [], "settings": {"strictness": strict}}


ds_strategy = st.builds(random_dataset, st.integers(0, 10_000), st.integers(1, 10),
                        st.integers(1, 10), st.sampled_from([0, 30, 50, 100]))


def op_time(inp, it):
    op = next(o for o in inp.products[it.product] if o.name == it.operation)
    w = next(w for w in inp.workers if w.name == it.worker)
    return it.qty / (op.norm_per_day * w.perf * inp.config.efficiency)


@settings(max_examples=60, deadline=None)
@given(ds_strategy)
def test_capacity_never_exceeded(ds):
    inp = build_input(ds, TODAY)
    res = plan(inp)
    idx = {d: i for i, d in enumerate(res.work_days)}
    for w in inp.workers:
        per_day = defaultdict(float)
        longest = 0.0
        for it in res.items:
            if it.worker == w.name:
                t = op_time(inp, it)
                per_day[idx[it.date]] += t
                longest = max(longest, t)
        cum = 0.0
        for d in sorted(per_day):
            cum += per_day[d]
            # накопленное время не больше числа прошедших дней плюс «хвост» одной операции
            assert cum <= (d + 1) + min(longest, 1.0) + 1e-6 or cum <= (d + 1) + longest + 1e-6


@settings(max_examples=40, deadline=None)
@given(ds_strategy)
def test_deterministic(ds):
    inp = build_input(ds, TODAY)
    a, b = plan(inp), plan(inp)
    assert a.items == b.items and a.warnings == b.warnings


@settings(max_examples=60, deadline=None)
@given(ds_strategy)
def test_integer_positive_quantities(ds):
    res = plan(build_input(ds, TODAY))
    assert all(isinstance(i.qty, int) and i.qty > 0 for i in res.items)


def _group_violations(inp, res):
    bad = []
    for order in inp.order_lines:
        ops = inp.products.get(order.product, ())
        by_seq = defaultdict(list)
        for it in res.items:
            if it.order_id == order.id:
                o = next(o for o in ops if o.name == it.operation)
                by_seq[o.sequence].append(it.date)
        seqs = sorted(by_seq)
        for a, b in zip(seqs, seqs[1:]):
            if min(by_seq[b]) < max(by_seq[a]):
                bad.append(order.id)
    return bad


def test_script_defect_d04_is_reproduced_and_fixable():
    """D-04: в оригинале порядок групп нарушается для опаздывающих заказов; флаг это исправляет."""
    ds = random_dataset(0, 5, 1, 0)
    inp = build_input(ds, TODAY)
    assert "301" in _group_violations(inp, plan(inp))           # поведение скрипта (паритет)
    fixed = replace(inp, config=replace(inp.config, fix_group_start_after_deadline=True))
    assert _group_violations(fixed, plan(fixed)) == []


@settings(max_examples=80, deadline=None)
@given(ds_strategy)
def test_group_order_respected_with_fix(ds):
    inp = build_input(ds, TODAY)
    inp = replace(inp, config=replace(inp.config, fix_group_start_after_deadline=True))
    assert _group_violations(inp, plan(inp)) == []


@settings(max_examples=60, deadline=None)
@given(ds_strategy)
def test_group_order_respected_before_deadline_only(ds):
    """Без флага порядок групп соблюдается для всех, кроме строк «после срока» (D-04)."""
    inp = build_input(ds, TODAY)
    res = plan(inp)
    for order in inp.order_lines:
        ops = inp.products.get(order.product, ())
        by_seq = defaultdict(list)
        for it in res.items:
            if it.order_id == order.id and it.reason_code != "AFTER_DEADLINE":
                o = next(o for o in ops if o.name == it.operation)
                by_seq[o.sequence].append(it.date)
        seqs = sorted(by_seq)
        for a, b in zip(seqs, seqs[1:]):
            assert min(by_seq[b]) >= max(by_seq[a]), (order.id, a, b)


@settings(max_examples=40, deadline=None)
@given(ds_strategy)
def test_plan_covers_requirement(ds):
    """Если нет предупреждений о горизонте/исполнителях, план + факт закрывает потребность."""
    inp = build_input(ds, TODAY)
    res = plan(inp)
    if any(w.code in ("HORIZON_EXCEEDED", "NO_WORKERS", "PRIORITY_HORIZON") for w in res.warnings):
        return
    planned = defaultdict(int)
    for it in res.items:
        planned[(it.order_id, it.operation)] += it.qty
    for o in inp.order_lines:
        for op in inp.products.get(o.product, ()):
            import math
            need = math.ceil(o.qty * op.per_item - 1e-9)
            assert planned[(o.id, op.name)] >= need, (o.id, op.name)


def test_absence_blocks_worker():
    ds = random_dataset(7, 6, 4, 100)
    inp = build_input(ds, TODAY)
    res0 = plan(inp)
    victim = res0.items[0].worker
    day = res0.items[0].date
    workers = tuple(replace(w, absent_days=frozenset({day})) if w.name == victim else w for w in inp.workers)
    res = plan(replace(inp, workers=workers))
    assert not [i for i in res.items if i.worker == victim and i.date == day]


def test_hold_until_releases_automatically():
    """BR-8 (новое): удержание с датой снятия не действует после этой даты."""
    ds = random_dataset(11, 3, 5, 100)
    inp = build_input(ds, TODAY)
    prod = inp.order_lines[0].product
    ops = tuple(replace(o, hold_qty=o.hold_qty or 0) for o in inp.products[prod])
    held = tuple(replace(o, hold_qty=1000) for o in ops)
    p_held = dict(inp.products); p_held[prod] = held
    p_rel = dict(inp.products); p_rel[prod] = tuple(replace(o, hold_qty=1000, hold_until=TODAY) for o in ops)
    with_hold = plan(replace(inp, products=p_held))
    released = plan(replace(inp, products=p_rel))
    base = plan(inp)
    oid = inp.order_lines[0].id
    q = lambda r: sum(i.qty for i in r.items if i.order_id == oid)
    assert q(with_hold) == 0 and q(released) == q(base) > 0


def test_efficiency_scales_capacity():
    """BR-1: ёмкость = норма × perf × efficiency. При 0.85 людей нужно больше, чем при 1.0."""
    ds = random_dataset(5, 6, 4, 100)
    a = plan(build_input(ds, TODAY, efficiency=1.0))
    b = plan(build_input(ds, TODAY, efficiency=0.85))
    last = lambda r: max(i.date for i in r.items)
    assert last(b) >= last(a)
    assert sum(i.qty for i in a.items) == sum(i.qty for i in b.items)


def test_reason_codes_present():
    res = plan(build_input(random_dataset(3, 8, 3, 100), TODAY))
    assert {i.reason_code for i in res.items} <= {"FREE_CAPACITY", "AFTER_DEADLINE", "FROZEN"}


def test_empty_inputs_do_not_crash():
    ds = {"orders": [], "ops": [], "workers": [], "holidays": [], "facts": []}
    res = plan(build_input(ds, TODAY))
    assert res.items == [] and res.warnings[0].code == "NO_WORKERS"
    ds["workers"] = [{"name": "А", "perf": 1, "skill": 1}]
    assert plan(build_input(ds, TODAY)).items == []
