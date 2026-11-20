"""Партия (% выполнения предшественника) по каждому предшественнику и «не планировать больше, чем нужно по заказу»."""
import math
from collections import defaultdict
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from conftest import TODAY
from plan24 import db as dbm
from plan24.models import Fact, Operation, OrderLine, Plan, PlanItem
from plan24.planner import Operation as POp, OrderLine as POrder, PlanConfig, PlanInput, Worker
from plan24.planner.engine import plan as run_plan
from test_stage4 import find_task, pin


# ---------------------------------------------------------------- движок: партия по предшественникам
def days(n, start=date(2026, 10, 5)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


def run(ops, qty=40, workers=3):
    wd = days(30)
    inp = PlanInput(order_lines=(POrder("1", "P", "prod", qty, wd[-1]),), products={"prod": tuple(ops)},
                    workers=tuple(Worker(id=i, name=f"W{i}") for i in range(workers)), facts=(),
                    config=PlanConfig(today=wd[0], work_days=wd, cap_load=True))
    res = run_plan(inp)
    tab = defaultdict(lambda: defaultdict(int))
    for it in res.items:
        tab[it.operation][it.date] += it.qty
    return tab


def cum(tab, name, d):
    return sum(q for x, q in tab[name].items() if x <= d)


@pytest.mark.parametrize("a1,a2", [(30, 100), (100, 30), (50, 50), (20, 70)])
def test_each_predecessor_releases_work_at_its_own_percent(a1, a2):
    ops = [POp("A1", 10, sequence=1, predecessors=()), POp("A2", 5, sequence=1, predecessors=()),
           POp("B", 40, sequence=2, predecessors=("A1", "A2"), pred_pcts=(("A1", float(a1)), ("A2", float(a2))))]
    t = run(ops)
    assert sum(t["B"].values()) == 40
    for d in t["B"]:
        assert cum(t, "A1", d) >= math.ceil(40 * a1 / 100) and cum(t, "A2", d) >= math.ceil(40 * a2 / 100)       # порог каждого предшественника
        assert cum(t, "B", d) <= min(cum(t, "A1", d), cum(t, "A2", d))                                              # и не больше, чем они выпустили


def test_default_percent_applies_where_predecessor_has_no_own():
    ops = [POp("A1", 10, sequence=1, predecessors=()), POp("A2", 10, sequence=1, predecessors=()),
           POp("B", 40, sequence=2, predecessors=("A1", "A2"), start_after_pct=30, pred_pcts=(("A2", 100.0),))]
    t = run(ops)
    first = min(t["B"])
    assert cum(t, "A1", first) >= 12 and cum(t, "A2", first) >= 40                                                 # A1 — общие 30%, A2 — своё 100%


def test_plan_without_per_link_percent_is_unchanged():
    base = [POp("A", 10, sequence=1), POp("B", 10, sequence=2, predecessors=("A",), start_after_pct=50)]
    same = [POp("A", 10, sequence=1), POp("B", 10, sequence=2, predecessors=("A",), start_after_pct=50, pred_pcts=(("A", 50.0),))]
    assert {k: dict(v) for k, v in run(base).items()} == {k: dict(v) for k, v in run(same).items()}


# ---------------------------------------------------------------- API каталога
def routing_with_ops(o):
    pid = o.post("/products", json={"name": "Партия-тест"}).json()["id"]
    rid = o.get(f"/products/{pid}/routings").json()[0]["id"]
    ids = {}
    for i, n in enumerate(("Резка", "Кромка", "Сборка"), start=1):
        ids[n] = o.post(f"/routings/{rid}/operations", json={"name": n, "norm_per_day": 10, "sequence": i}).json()["id"]
    o.pid = pid
    return rid, ids


def ops_of(o, rid):
    for pr in o.get("/products").json()["items"]:
        for r in o.get(f"/products/{pr['id']}/routings").json():
            if r["id"] == rid:
                return {x["id"]: x for x in r["operations"]}
    raise AssertionError("маршрут не найден")


def test_operation_api_stores_and_prunes_percents(full_org):
    o = full_org
    rid, ids = routing_with_ops(o)
    ver = lambda n: ops_of(o, rid)[ids[n]]["version"]
    r = o.patch(f"/operations/{ids['Сборка']}", json={"predecessors": ["Резка", "Кромка"], "predecessor_pcts": {"Резка": 30, "Кромка": 100}, "version": ver("Сборка")})
    assert r.status_code == 200, r.text
    assert r.json()["predecessor_pcts"] == {"Резка": 30.0, "Кромка": 100.0}
    assert o.patch(f"/operations/{ids['Сборка']}", json={"predecessor_pcts": {"Сварка": 50}, "version": ver("Сборка")}).status_code == 422       # не предшественник
    assert o.patch(f"/operations/{ids['Сборка']}", json={"predecessor_pcts": {"Резка": 0}, "version": ver("Сборка")}).status_code == 422
    assert o.patch(f"/operations/{ids['Сборка']}", json={"predecessor_pcts": {"Резка": 101}, "version": ver("Сборка")}).status_code == 422
    r2 = o.patch(f"/operations/{ids['Сборка']}", json={"predecessors": ["Кромка"], "version": ver("Сборка")})                                    # убрали предшественника — запись исчезла
    assert r2.status_code == 200 and r2.json()["predecessor_pcts"] == {"Кромка": 100.0}
    assert o.patch(f"/operations/{ids['Кромка']}", json={"name": "Кромление", "version": ver("Кромка")}).status_code == 200                       # переименование тянет ключ
    assert ops_of(o, rid)[ids["Сборка"]]["predecessor_pcts"] == {"Кромление": 100.0}
    assert o.delete(f"/operations/{ids['Кромка']}").status_code == 204                                                                           # удалили — запись исчезла
    assert ops_of(o, rid)[ids["Сборка"]]["predecessor_pcts"] is None


def test_new_version_keeps_percents(full_org):
    o = full_org
    rid, ids = routing_with_ops(o)
    ver = ops_of(o, rid)[ids["Сборка"]]["version"]
    assert o.patch(f"/operations/{ids['Сборка']}", json={"predecessors": ["Резка"], "predecessor_pcts": {"Резка": 40}, "version": ver}).status_code == 200
    nv = o.post(f"/products/{o.pid}/routings")
    assert nv.status_code == 201
    assert next(x for x in nv.json()["operations"] if x["name"] == "Сборка")["predecessor_pcts"] == {"Резка": 40.0}


def test_flow_map_percent_on_link(full_org):
    o = full_org
    rid, ids = routing_with_ops(o)
    a, b = ids["Резка"], ids["Сборка"]
    assert o.post(f"/flow-map/{rid}/links", json={"from_id": a, "to_id": b}).status_code == 200
    assert o.put(f"/flow-map/{rid}/links/pct", json={"from_id": a, "to_id": b, "pct": 30}).status_code == 200
    m = o.get("/flow-map", params={"routing_id": rid}).json()
    e = next(e for e in m["edges"] if e["from"] == a and e["to"] == b)
    assert e["pct"] == 30 and e["pct_own"] is True
    assert o.put(f"/flow-map/{rid}/links/pct", json={"from_id": ids["Кромка"], "to_id": a, "pct": 30}).status_code == 404       # такой связи нет
    assert o.put(f"/flow-map/{rid}/links/pct", json={"from_id": a, "to_id": b, "pct": 0}).status_code == 422
    assert o.put(f"/flow-map/{rid}/links/pct", json={"from_id": a, "to_id": b, "pct": None}).status_code == 200            # вернуть общую партию
    e = next(e for e in o.get("/flow-map", params={"routing_id": rid}).json()["edges"] if e["from"] == a and e["to"] == b)
    assert e["pct"] is None and e["pct_own"] is False
    assert o.put(f"/flow-map/{rid}/links/pct", "viewer", json={"from_id": a, "to_id": b, "pct": 10}).status_code == 403
    o.put(f"/flow-map/{rid}/links/pct", json={"from_id": a, "to_id": b, "pct": 30})
    assert o.delete(f"/flow-map/{rid}/links", params={"from_id": a, "to_id": b}).status_code == 200                           # связь удалена — процент тоже
    assert ops_of(o, rid)[b]["predecessor_pcts"] is None


def test_percent_reaches_the_planner(full_org):
    """Партия по связи из каталога учитывается в расчёте плана заказа."""
    o = full_org
    ol = o.get("/order-lines").json()["items"][0]
    ops = sorted(ops_of(o, ol["routing_id"]).values(), key=lambda x: x["sequence"])
    if len(ops) < 3:
        pytest.skip("мало операций")
    last, first = ops[-1], ops[0]
    assert o.patch(f"/operations/{last['id']}", json={"predecessors": [first["name"]], "predecessor_pcts": {first["name"]: 25}, "version": last["version"]}).status_code == 200
    assert o.replan_all().status_code == 201


# ---------------------------------------------------------------- «не планировать больше, чем нужно»
def net_surplus(o):
    s = dbm.admin_session()
    try:
        pid = s.scalar(select(Plan.id).where(Plan.status == "active"))
        fact, ftot = defaultdict(float), defaultdict(float)
        for f in s.scalars(select(Fact).where(Fact.deleted_at.is_(None))):
            fact[(f.fact_date, f.worker_id, f.order_line_id, f.operation_id)] += f.qty
            ftot[(f.order_line_id, f.operation_id)] += f.qty
        need = defaultdict(float)
        for i in s.scalars(select(PlanItem).where(PlanItem.plan_id == pid)):
            need[(i.order_line_id, i.operation_id)] += max(0.0, i.qty - fact.get((i.plan_date, i.worker_id, i.order_line_id, i.operation_id), 0))
        out = {}
        for (ol, op), q in need.items():
            line, opr = s.get(OrderLine, ol), s.get(Operation, op)
            req = math.ceil(line.qty * opr.per_item - 1e-9)
            if q + ftot.get((ol, op), 0) > req + 1e-6:
                out[(ol, opr.name)] = (req, q, ftot.get((ol, op), 0))
        return out
    finally:
        s.close()


def replan_warnings(o, **extra):
    """Перепланирование участок за участком; → все предупреждения расчётов."""
    out = []
    for wc in o.get("/work-centers").json()["items"]:
        r = o.post("/plans/replan", json={"work_center_id": wc["id"], **extra})
        assert r.status_code == 201, r.text
        out += r.json()["warnings"]
    return out


def test_pinned_day_is_trimmed_when_work_already_done_by_someone_else(full_org):
    from test_stage4 import mate
    o = full_org
    o.replan_all()
    day, w, t = find_task(o)
    pin(o, w["worker_id"], day)
    w2 = mate(o, w)
    req = next(op for it in o.get("/facts/order-options").json()["items"] if it["id"] == t["order_line_id"]
               for op in it["operations"] if op["id"] == t["operation_id"])["required"]
    assert o.post("/facts", json={"fact_date": TODAY.isoformat(), "order_line_id": t["order_line_id"], "operation_id": t["operation_id"],
                                  "worker_id": w2["id"], "qty": req}).status_code == 200
    assert net_surplus(o)                                                                  # до перепланирования закреплённое — лишнее
    warns = replan_warnings(o)
    assert net_surplus(o) == {}
    assert any(x["code"] == "PINNED_TRIMMED" for x in warns), [x["code"] for x in warns]          # администратор видит, что лишнее убрано


def test_pinned_rows_are_trimmed_when_order_shrinks(full_org):
    o = full_org
    o.replan_all()
    day, w, t = find_task(o)
    pin(o, w["worker_id"], day)
    line = next(x for x in o.get("/order-lines").json()["items"] if x["id"] == t["order_line_id"])
    assert o.patch(f"/order-lines/{line['id']}", json={"qty": max(1, line["qty"] // 3), "version": line["version"]}).status_code == 200
    replan_warnings(o, freeze_days=0)              # замороженные ближайшие дни (как в эталоне) не трогаем: проверяем закреплённое
    assert net_surplus(o) == {}


def test_pinned_row_with_partial_fact_does_not_plan_the_rest_twice(full_org):
    o = full_org
    o.replan_all()
    d = o.get("/reports/day").json()
    w, t = next((w, t) for w in d["workers"] for t in w["tasks"] if t["plan_qty"] and t["item_id"] and t["plan_qty"] >= 3)
    pin(o, w["worker_id"], TODAY.isoformat())
    part = int(t["plan_qty"]) - 2
    assert o.post("/facts", json={"fact_date": TODAY.isoformat(), "order_line_id": t["order_line_id"], "operation_id": t["operation_id"],
                                  "worker_id": w["worker_id"], "qty": part, "note": "меньше"}).status_code == 200
    for _ in range(2):
        assert o.replan_all().status_code == 201
        assert net_surplus(o) == {}


def test_plain_replan_never_overplans(full_org):
    o = full_org
    for _ in range(3):
        o.replan_all()
        assert net_surplus(o) == {}
