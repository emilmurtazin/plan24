"""Единая доля выполнения заказа (список, карточка, дашборд) и сводка руководителя."""
import math
from datetime import timedelta

from sqlalchemy import select

from conftest import TODAY
from plan24 import db as dbm
from plan24.models import DayPlanSnapshot, PlanItem


def card(o, oid):
    return o.get("/reports/order-status", params={"order_line_id": oid}).json()


def fact(o, oid, op_id, qty, worker=None, day=None):
    r = o.post("/facts", json={"fact_date": (day or TODAY).isoformat(), "order_line_id": oid, "operation_id": op_id,
                               "worker_id": (worker or o.w1)["id"], "qty": qty, "note": "тест"})
    assert r.status_code == 200, r.text
    return r


def rows_by_id(o):
    return {r["order_line_id"]: r for r in o.get("/reports/status").json()["rows"]}


def test_progress_is_the_same_in_list_card_and_dashboard_and_weighted_by_labour(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    oid = next(iter(rows_by_id(o)))
    ops = card(o, oid)["operations"]
    first = ops[0]
    fact(o, oid, first["operation_id"], first["required"] + 7)             # перевыполнение не должно давать >100% по операции
    c = card(o, oid)
    lst = rows_by_id(o)[oid]["progress"]
    dash = next(r["progress"] for r in o.get("/reports/overview").json()["orders_month"] + [{"order_line_id": oid, "progress": lst}] if r.get("order_line_id") == oid)
    assert c["order"]["progress"] == lst == dash > 0                          # везде одно и то же число (раньше в списке было 0)
    # по трудоёмкости: вес операции — нормо-дни; быстрый «Раскрой» — малая часть заказа
    total = sum(x["required"] / x["norm_per_day"] for x in ops)
    expect = round(first["required"] / first["norm_per_day"] / total * 100, 1)
    naive = round(first["required"] / sum(x["required"] for x in ops) * 100, 1)
    assert abs(lst - expect) < 0.11 and lst < naive
    assert all(0 <= x["done"] and min(x["done"], x["required"]) / x["required"] <= 1 for x in ops)


def test_fully_done_order_is_ready_in_list_and_dashboard(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    oid = next(iter(rows_by_id(o)))
    assert rows_by_id(o)[oid]["status"] != "Готово"
    for x in card(o, oid)["operations"]:
        fact(o, oid, x["operation_id"], x["required"])
    r = rows_by_id(o)[oid]
    assert card(o, oid)["order"]["progress"] == r["progress"] == 100.0
    assert r["status"] == "Готово"                                          # без перепланирования
    d = o.get("/reports/overview").json()
    assert d["summary"]["done"] >= 1 and d["by_status"].get("Готово", 0) >= 1
    assert all(a.get("order_line_id") != oid for a in d["summary"]["attention"])


def test_progress_never_shows_100_until_every_operation_is_complete(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    oid = next(iter(rows_by_id(o)))
    ops = card(o, oid)["operations"]
    for x in ops[:-1]:
        fact(o, oid, x["operation_id"], x["required"])
    last = ops[-1]
    fact(o, oid, last["operation_id"], max(1, last["required"] - 1))
    assert 0 < rows_by_id(o)[oid]["progress"] < 100


def test_deleted_order_disappears_from_status_list_and_dashboard_without_replan(full_org):
    o = full_org
    o.replan_all()
    ids = list(rows_by_id(o))
    gone = ids[0]
    assert o.delete(f"/order-lines/{gone}").status_code == 204
    assert gone not in rows_by_id(o)
    d = o.get("/reports/overview").json()
    assert sum(d["by_status"].values()) == len(ids) - 1 and d["progress"]["orders"] == len(ids) - 1
    assert all(r["order_line_id"] != gone for r in d["orders_month"])


def test_dashboard_summary_shape_and_consistency(full_org):
    o = full_org
    o.replan_all()
    d = o.get("/reports/overview").json()
    s = d["summary"]
    assert s["on_track"] + s["at_risk"] + s["no_plan"] <= s["active"] and s["active"] + s["done"] <= len(rows_by_id(o))
    assert s["on_time_pct"] is None or 0 <= s["on_time_pct"] <= 100
    assert 0 <= s["progress_pct"] <= 100 and s["work_left_days"] >= 0 and s["load_state"] in (None, "ok", "high", "over")
    assert len(s["attention"]) <= 8 and all(a["kind"] in ("late", "noplan", "resource", "facts", "reason") and a["level"] in ("high", "medium", "low") for a in s["attention"])
    late_orders = [a for a in s["attention"] if a["kind"] == "late"]
    assert len(late_orders) <= s["at_risk"]
    assert d["progress"]["percent"] == s["progress_pct"] and d["progress"]["total_work"] >= d["progress"]["done_work"]


def test_dashboard_summary_for_empty_org(org):
    s = org.get("/reports/overview").json()["summary"]
    assert s["active"] == 0 and s["on_time_pct"] is None and s["attention"] == [] and s["load_pct"] is None


def test_plan_adherence_over_last_weeks(full_org):
    o = full_org
    o.replan_all()
    oid = next(iter(rows_by_id(o)))
    s = dbm.admin_session()
    try:
        pi = s.scalars(select(PlanItem).where(PlanItem.order_line_id == oid)).first()
        day = TODAY - timedelta(days=3)
        s.add(DayPlanSnapshot(tenant_id=pi.tenant_id, plan_date=day, order_line_id=oid, operation_id=pi.operation_id,
                              worker_id=pi.worker_id, work_center_id=None, qty=10))
        s.commit()
        op, w = pi.operation_id, pi.worker_id
    finally:
        s.close()
    assert o.get("/reports/overview").json()["summary"]["adherence"]["percent"] == 0.0       # факта нет — план не выполнен
    o.post("/facts", json={"fact_date": day.isoformat(), "order_line_id": oid, "operation_id": op, "worker_id": w, "qty": 5})
    a = o.get("/reports/overview").json()["summary"]["adherence"]
    assert a["percent"] == 50.0 and a["prev_percent"] is None
