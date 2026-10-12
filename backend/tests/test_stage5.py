"""Этап 5 доработок: статус заказа по операциям — выполнено, в плане, смены, дата завершения, узкое место, проблемы, рекомендации."""
import math
from datetime import timedelta

from conftest import TODAY


def status(o, oid, role="owner"):
    r = o.get("/reports/order-status", role, params={"order_line_id": oid})
    assert r.status_code == 200, r.text
    return r.json()


def a_order(o):
    return o.get("/reports/status").json()["rows"][0]["order_line_id"]


def new_order(o, qty, due, ext, prio=1):
    prj, prod = o.ids("/projects")[0], o.ids("/products")[0]
    r = o.post("/order-lines", json={"project_id": prj, "product_id": prod, "qty": qty, "ext_id": ext, "priority": prio, "due_date": due})
    assert r.status_code == 201, r.text
    ops = o.get(f"/order-lines/{r.json()['id']}/operations").json()["items"]
    o.put(f"/order-lines/{r.json()['id']}/operations", json={"assignments": [
        {"operation_id": i["operation_id"], "work_center_ids": [c["id"] for c in o.wcs]} for i in ops]})
    return r.json()["id"]


def test_operations_table_is_consistent(full_org):
    o = full_org
    o.replan_all()
    oid = a_order(o)
    s = status(o, oid)
    ol = o.get(f"/order-lines/{oid}").json()
    assert s["plan_id"] and s["order"]["order_line_id"] == oid and s["order"]["qty"] == ol["qty"]
    ops = s["operations"]
    assert len(ops) >= 2 and [x["sequence"] for x in ops] == sorted(x["sequence"] for x in ops)
    wdays = o.get("/reports/day").json()["days"]
    for x in ops:
        assert x["required"] == math.ceil(ol["qty"] * x["per_item"] - 1e-9)
        assert x["done"] == 0 and x["remaining"] == x["required"]
        assert abs(x["planned"] + x["unplanned"] - x["remaining"]) < 1e-9
        if x["unplanned"] == 0:
            assert x["end_date"] and x["status"] == "В плане"
            assert x["shifts_left"] == len([d for d in wdays if TODAY.isoformat() <= d <= x["end_date"]])
            assert x["workers"] and x["shifts_left"] >= 1
    # ровно одно узкое место — операция с самым поздним завершением
    b = [x for x in ops if x["is_bottleneck"]]
    assert len(b) == 1
    ends = [x["end_date"] for x in ops if x["end_date"]]
    assert b[0]["end_date"] == max(ends)
    assert s["order"]["bottleneck"] == b[0]["name"] and s["order"]["forecast_end"] == max(ends)
    assert s["order"]["status"] in ("В работе", "Риск опоздания") and s["order"]["progress"] == 0


def test_done_operation_is_not_a_bottleneck_and_progress_grows(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    oid = a_order(o)
    s = status(o, oid)
    first = s["operations"][0]
    wid = o.w1["id"]
    r = o.post("/facts", json={"fact_date": TODAY.isoformat(), "order_line_id": oid, "operation_id": first["operation_id"],
                               "worker_id": wid, "qty": first["required"]})
    assert r.status_code == 200, r.text
    s2 = status(o, oid)
    d = s2["operations"][0]
    assert d["done"] == first["required"] and d["remaining"] == 0 and d["status"] == "Готово"
    assert d["shifts_left"] == 0 and d["end_date"] == TODAY.isoformat() and not d["is_bottleneck"]
    assert s2["order"]["progress"] > 0


def test_late_order_gets_problem_and_worker_recommendation(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    oid = new_order(o, 200, (TODAY + timedelta(days=3)).isoformat(), "LATE-1")
    o.replan_all()
    s = status(o, oid)
    late_ops = [x for x in s["operations"] if x["late"]]
    assert late_ops, "заказ на 200 шт. за 3 дня должен опаздывать"
    x = late_ops[0]
    assert any(p["code"] == "LATE" and "позже срока" in p["text"] for p in x["problems"])
    assert any("Добавить ориентировочно" in r for r in x["recommendations"]), x["recommendations"]
    assert s["order"]["late_days"] and s["order"]["late_days"] > 0 and s["order"]["status"] == "Риск опоздания"
    assert [y for y in s["operations"] if y["bottleneck_critical"]]


def test_problems_not_distributed_hold_reasons(full_org):
    o = full_org
    o.replan_all()
    oid = new_order(o, 6, (TODAY + timedelta(days=20)).isoformat(), "PR-1")
    ops = o.get(f"/order-lines/{oid}/operations").json()["items"]
    # одну операцию снимаем с участков
    o.put(f"/order-lines/{oid}/operations", json={"assignments": [{"operation_id": ops[0]["operation_id"], "work_center_ids": []}]})
    o.replan_all()
    s = status(o, oid)
    x = next(i for i in s["operations"] if i["operation_id"] == ops[0]["operation_id"])
    assert x["status"] == "Не распределена" and any(p["code"] == "NOT_DISTRIBUTED" for p in x["problems"])
    assert any("Распределить операцию" in r for r in x["recommendations"])
    # блокировка операции и повторяющаяся причина отклонений
    op2 = ops[1]
    assert o.patch(f"/operations/{op2['operation_id']}", json={"hold_qty": 3, "hold_reason": "ждём фурнитуру"}).status_code == 200
    reason = o.get("/fact-reasons").json()["items"][0]
    for d in (TODAY, TODAY - timedelta(days=1)):
        o.post("/facts", json={"fact_date": d.isoformat(), "order_line_id": oid, "operation_id": op2["operation_id"],
                               "worker_id": o.w1["id"], "qty": 1, "reason_id": reason["id"]})
    s = status(o, oid)
    y = next(i for i in s["operations"] if i["operation_id"] == op2["operation_id"])
    codes = {p["code"] for p in y["problems"]}
    assert "HOLD" in codes and "REASONS" in codes
    assert any("×2" in p["text"] and reason["name"] in p["text"] for p in y["problems"])
    assert any("ждём фурнитуру" in r for r in y["recommendations"]) and any("повторяющейся причиной" in r for r in y["recommendations"])


def test_no_qualified_workers(full_org):
    o = full_org
    o.replan_all()
    top = len(o.get("/settings").json()["qualifications"])
    oid = new_order(o, 4, (TODAY + timedelta(days=20)).isoformat(), "Q-1")
    ops = o.get(f"/order-lines/{oid}/operations").json()["items"]
    prod = o.get("/products").json()["items"][0]
    rts = o.get(f"/products/{prod['id']}/routings").json()
    o.patch("/settings", json={"qualifications": [f"Ур{i}" for i in range(1, 11)]})
    v2 = o.post(f"/products/{prod['id']}/routings").json()                       # новая версия — её операции можно менять
    first = v2["operations"][0]
    assert o.patch(f"/operations/{first['id']}", json={"min_skill": 10}).status_code == 200
    ol2 = o.post("/order-lines", json={"project_id": o.ids("/projects")[0], "product_id": prod["id"], "qty": 3, "ext_id": "Q-2",
                                       "routing_id": v2["id"], "due_date": (TODAY + timedelta(days=20)).isoformat()}).json()
    o.put(f"/order-lines/{ol2['id']}/operations", json={"assignments": [
        {"operation_id": i["id"], "work_center_ids": [c["id"] for c in o.wcs]} for i in v2["operations"]]})
    o.replan_all()
    s = status(o, ol2["id"])
    x = next(i for i in s["operations"] if i["operation_id"] == first["id"])
    assert any(p["code"] == "NO_QUALIFIED" and "Ур10" in p["text"] for p in x["problems"]), x["problems"]
    assert any("квалификацию" in r for r in x["recommendations"])


def test_permissions_foreman_sees_only_own_operations(full_org):
    o = full_org
    o.replan_all()
    oid = new_order(o, 6, (TODAY + timedelta(days=20)).isoformat(), "F-9")
    ops = o.get(f"/order-lines/{oid}/operations").json()["items"]
    a, b = o.wcs[0]["id"], o.wcs[1]["id"]                    # бригадир — участок A
    o.put(f"/order-lines/{oid}/operations", json={"assignments": [
        {"operation_id": ops[0]["operation_id"], "work_center_ids": [a]}] + [{"operation_id": i["operation_id"], "work_center_ids": [b]} for i in ops[1:]]})
    o.replan_all()
    mine = status(o, oid, "foreman")
    assert [x["operation_id"] for x in mine["operations"]] == [ops[0]["operation_id"]] and mine["order"]["bottleneck"] is None
    assert len(status(o, oid, "owner")["operations"]) == len(ops)
    assert len(status(o, oid, "technologist")["operations"]) == len(ops)
    only_b = new_order(o, 3, (TODAY + timedelta(days=20)).isoformat(), "F-10")
    ops2 = o.get(f"/order-lines/{only_b}/operations").json()["items"]
    o.put(f"/order-lines/{only_b}/operations", json={"assignments": [{"operation_id": i["operation_id"], "work_center_ids": [b]} for i in ops2]})
    assert o.get("/reports/order-status", "foreman", params={"order_line_id": only_b}).status_code == 404
    assert o.get("/reports/order-status", "worker", params={"order_line_id": oid}).status_code == 403
    assert o.get("/reports/order-status", params={"order_line_id": 999999}).status_code == 404


def test_without_plan(full_org):
    o = full_org
    oid = o.get("/order-lines").json()["items"][0]["id"]
    s = status(o, oid)
    assert s["plan_id"] is None and s["order"]["status"] == "Нет плана"
    assert all(x["planned"] == 0 and x["end_date"] is None and x["required"] > 0 for x in s["operations"])
