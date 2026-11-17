"""«План-график»: календарный план и факт по заказам по дням, фильтры по участку и заказам."""
from datetime import timedelta

import pytest

from conftest import TODAY
from plan24 import db as dbm
from plan24.models import DayPlanSnapshot

D = TODAY.isoformat()


@pytest.fixture()
def planned(full_org):
    full_org.post("/plans/replan")
    return full_org


def sched(o, role="owner", **p):
    r = o.get("/schedule", role, params=p)
    assert r.status_code == 200, r.text
    return r.json()


def total(order, i):
    return sum(v[i] for v in order["days"].values())


def test_orders_with_plan_by_day(planned):
    o = planned
    s = sched(o)
    assert s["today"] == D and s["orders"] and s["date_from"] <= D <= s["date_to"] and s["bounds"]["min"] and s["bounds"]["max"]
    x = next(x for x in s["orders"] if x["plan_start"])
    assert {"id", "project", "order", "product", "qty", "due", "status", "progress", "forecast", "plan_end", "fact_start", "work", "sections", "days"} <= set(x)
    assert total(x, 0) > 0 and all(len(v) == 2 for v in x["days"].values())
    assert x["plan_start"] <= x["plan_end"] and x["status"] in ("В работе", "Риск опоздания", "Нет плана", "Готово", "Нет операций")
    assert s["totals"] and abs(sum(v[0] for v in s["totals"].values()) - sum(total(y, 0) for y in s["orders"])) < 0.05


def test_fact_appears_next_to_plan(planned):
    o = planned
    it = next(i for i in o.get("/facts/order-options").json()["items"])
    op = it["operations"][0]
    assert o.post("/facts/order", json={"fact_date": D, "order_line_id": it["id"], "operation_id": op["id"], "qty": 2}).status_code == 200
    x = next(x for x in sched(o)["orders"] if x["id"] == it["id"])
    assert D in x["days"] and x["days"][D][1] > 0 and x["fact_start"] == D and x["progress"] > 0 and x["work_done"] > 0


def test_past_plan_comes_from_snapshots(planned):
    o = planned
    it = next(i for i in o.get("/facts/order-options").json()["items"])
    op = it["operations"][0]
    past = TODAY - timedelta(days=3)
    s = dbm.admin_session(); s.info["write"] = True
    from sqlalchemy import select
    from plan24.models import OrderLine
    tid = s.scalar(select(OrderLine.tenant_id).where(OrderLine.id == it["id"]))
    s.add(DayPlanSnapshot(tenant_id=tid, plan_date=past, order_line_id=it["id"], operation_id=op["id"], worker_id=1, work_center_id=None, qty=5))
    s.commit(); s.close()
    x = next(x for x in sched(o, date_from=(past - timedelta(days=1)).isoformat(), date_to=D)["orders"] if x["id"] == it["id"])
    assert x["days"][past.isoformat()][0] > 0 and x["plan_start"] == past.isoformat()


def test_filter_by_orders_and_project(planned):
    o = planned
    allo = sched(o)["orders"]
    pick = [x["id"] for x in allo[:2]]
    got = sched(o, order_line_id=pick)["orders"]
    assert [x["id"] for x in got] == [x["id"] for x in allo if x["id"] in pick]
    proj = allo[0]["project_id"]
    assert all(x["project_id"] == proj for x in sched(o, project_id=proj)["orders"])
    assert sched(o, order_line_id=[999999])["orders"] == []


def test_filter_by_section(planned):
    o = planned
    allo = {x["id"]: x for x in sched(o)["orders"]}
    a, b = o.wcs[0], o.wcs[1]
    sa, sb = sched(o, work_center_id=a["id"]), sched(o, work_center_id=b["id"])
    assert sa["work_center_id"] == a["id"] and sa["orders"] and set(x["id"] for x in sa["orders"]) <= set(allo)
    for x in sa["orders"]:
        assert a["name"] in x["sections"] and total(x, 0) <= total(allo[x["id"]], 0) + 0.01     # только план этого участка
    assert sum(total(x, 0) for x in sa["orders"]) + sum(total(x, 0) for x in sb["orders"]) <= sum(total(x, 0) for x in allo.values()) + 0.5


def test_foreman_sees_only_own_section(planned):
    o = planned
    mine, other = o.wcs[0], o.wcs[1]
    s = sched(o, "foreman", work_center_id=other["id"])                                             # чужой участок в запросе игнорируется
    assert s["work_center_id"] == mine["id"] and all(mine["name"] in x["sections"] for x in s["orders"])
    assert o.get("/schedule", "worker").status_code == 403


def test_window_and_validation(planned):
    o = planned
    s = sched(o, date_from=D, date_to=(TODAY + timedelta(days=6)).isoformat())
    assert s["date_from"] == D and all(D <= k <= s["date_to"] for x in s["orders"] for k in x["days"])
    assert o.get("/schedule", params={"date_from": D, "date_to": (TODAY - timedelta(days=1)).isoformat()}).status_code == 422
    assert o.get("/schedule", params={"date_from": D, "date_to": (TODAY + timedelta(days=500)).isoformat()}).status_code == 422


def test_operations_breakdown(planned):
    o = planned
    x = next(x for x in sched(o)["orders"] if x["plan_start"])
    r = o.get(f"/schedule/{x['id']}/operations", params={"date_from": x["plan_start"], "date_to": x["plan_end"]})
    assert r.status_code == 200
    ops = r.json()["operations"]
    assert ops and {"id", "name", "unit", "required", "done", "pct", "sections", "days"} <= set(ops[0])
    assert any(v[0] > 0 for p in ops for v in p["days"].values())
    assert o.get("/schedule/999999/operations", params={"date_from": D, "date_to": D}).status_code == 404
    assert o.get(f"/schedule/{x['id']}/operations", params={"date_from": D, "date_to": (TODAY + timedelta(days=900)).isoformat()}).status_code == 422
