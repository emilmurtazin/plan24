"""Ввод факта (FR-21…FR-25)."""
from datetime import timedelta

from conftest import TODAY
from test_planning import first_task

D = TODAY.isoformat()


def body(o, **kw):
    ol = o.ids("/order-lines")[0]
    d = o.get(f"/order-lines/{ol}").json()
    op = next(x for r in o.get(f"/products/{d['product_id']}/routings").json() if r["id"] == d["routing_id"] for x in r["operations"])
    return {"fact_date": D, "order_line_id": ol, "operation_id": op["id"], "worker_id": o.w1["id"], "qty": 5, **kw}


def test_repeat_entry_replaces_value_not_adds(full_org):
    o = full_org
    r1 = o.post("/facts", json=body(o, qty=5)).json()
    r2 = o.post("/facts", json=body(o, qty=8)).json()
    assert r1["id"] == r2["id"] and r2["replaced"] is True and r2["previous_qty"] == 5 and r2["qty"] == 8
    rows = o.get("/facts").json()["items"]
    assert len(rows) == 1 and rows[0]["qty"] == 8
    ev = [a for a in o.get("/audit").json()["items"] if a["entity"] == "fact"]
    assert ev[0]["changes"]["qty"] == [5.0, 8.0]                           # прежнее значение сохранено в журнале


def test_validation_rules(full_org):
    o = full_org
    assert o.post("/facts", json=body(o, qty=-1)).status_code == 422
    assert o.post("/facts", json=body(o, fact_date=(TODAY + timedelta(days=1)).isoformat())).status_code == 422
    b = body(o)
    other_prod = [p for p in o.get("/products").json()["items"] if p["id"] != o.get(f"/order-lines/{b['order_line_id']}").json()["product_id"]][0]
    foreign_op = o.get(f"/products/{other_prod['id']}/routings").json()[0]["operations"][0]["id"]
    assert o.post("/facts", json={**b, "operation_id": foreign_op}).status_code == 422     # операция не из маршрута позиции
    assert o.post("/facts", json={**b, "reason_id": 999999}).status_code == 404
    o.patch(f"/order-lines/{b['order_line_id']}", json={"status": "cancelled"})
    assert o.post("/facts", json=b).status_code == 422


def test_zero_with_reason_is_recorded_but_not_counted(full_org):
    o = full_org
    reason = o.ids("/fact-reasons")[0]
    r = o.post("/facts", json=body(o, qty=0, reason_id=reason, note="не было материала"))
    assert r.status_code == 200 and r.json()["reason_id"] == reason
    o.post("/plans/replan")
    st = {x["order_line_id"]: x for x in o.get("/reports/status").json()["rows"]}[body(o)["order_line_id"]]
    assert st["done_ops"] == 0


def test_worker_rights(full_org):
    o = full_org
    other = next(w for w in o.workers if w["id"] != o.w1["id"])
    assert o.post("/facts", "worker", json=body(o)).status_code == 200                           # свой, сегодня
    assert o.post("/facts", "worker", json=body(o, worker_id=other["id"])).status_code == 403    # чужой
    y = (TODAY - timedelta(days=1)).isoformat()
    assert o.post("/facts", "worker", json=body(o, fact_date=y)).status_code == 403              # прошлый день
    assert o.post("/facts", "foreman", json=body(o, fact_date=y)).status_code == 200            # бригадир может за прошлые дни
    mine = o.get("/facts", "worker").json()["items"]
    assert {f["worker_id"] for f in mine} == {o.w1["id"]}                                        # видит только свои
    assert o.delete(f"/facts/{mine[0]['id']}", "worker").status_code == 403


def test_foreman_limited_to_own_work_center(full_org):
    o = full_org
    own = next(w for w in o.workers if w["work_center_id"] == o.wcs[0]["id"])
    foreign = next(w for w in o.workers if w["work_center_id"] == o.wcs[1]["id"])
    assert o.post("/facts", "foreman", json=body(o, worker_id=own["id"])).status_code == 200
    assert o.post("/facts", "foreman", json=body(o, worker_id=foreign["id"])).status_code == 403
    fid = o.post("/facts", "manager", json=body(o, worker_id=foreign["id"])).json()["id"]
    assert o.delete(f"/facts/{fid}", "foreman").status_code == 403
    assert o.post(f"/facts/{fid}/confirm", "foreman").status_code == 403
    assert {f["worker_id"] for f in o.get("/facts", "foreman").json()["items"]} == {own["id"]}


def test_viewer_cannot_enter_or_read_facts(full_org):
    assert full_org.post("/facts", "viewer", json=body(full_org)).status_code == 403
    assert full_org.get("/facts", "viewer").status_code == 403


def test_idempotency_key_prevents_duplicates(full_org):
    o = full_org
    h = {"Idempotency-Key": "k-123"}
    r1 = o.c.post("/api/v1/facts", json=body(o, qty=4), headers={**o.users["owner"], **h})
    r2 = o.c.post("/api/v1/facts", json=body(o, qty=4), headers={**o.users["owner"], **h})
    assert r1.json() == r2.json()
    assert len([a for a in o.get("/audit").json()["items"] if a["entity"] == "fact"]) == 1
    # тот же ключ другой организации — независимый
    from conftest import Org
    o2 = Org(o.c, "z@z.ru", "Цех Z")
    assert o2.get("/facts").json()["items"] == []


def test_confirmation_flow(full_org):
    o = full_org
    o.patch("/settings", json={"fact_confirmation": True})
    f = o.post("/facts", "worker", json=body(o)).json()
    assert f["status"] == "submitted"
    assert o.post("/facts", "foreman", json=body(o, qty=7)).json()["status"] == "confirmed"      # бригадир — сразу подтверждено
    f2 = o.post("/facts", "worker", json=body(o, qty=6)).json()
    assert f2["status"] == "submitted"
    assert o.post(f"/facts/{f2['id']}/confirm", "foreman").json()["status"] == "confirmed"
    assert o.post(f"/facts/{f2['id']}/confirm", "worker").status_code == 403


def test_soft_delete_and_revive(full_org):
    o = full_org
    f = o.post("/facts", json=body(o, qty=9)).json()
    assert o.delete(f"/facts/{f['id']}").status_code == 204
    assert o.get("/facts").json()["items"] == []
    o.post("/plans/replan")
    st = {x["order_line_id"]: x for x in o.get("/reports/status").json()["rows"]}[body(o)["order_line_id"]]
    assert st["done_ops"] == 0                                              # удалённый факт не учитывается
    again = o.post("/facts", json=body(o, qty=3)).json()
    assert again["qty"] == 3 and again["replaced"] is False


def test_day_report_shows_plan_vs_fact_and_role_scoping(full_org):
    o = full_org
    o.post("/plans/replan")
    r, w, t = first_task(o)
    o.post("/facts", json={"fact_date": D, "order_line_id": t["order_line_id"], "operation_id": t["operation_id"],
                           "worker_id": w["worker_id"], "qty": t["plan_qty"]})
    day = o.get("/reports/day").json()
    mine = next(x for x in day["workers"] if x["worker_id"] == w["worker_id"])
    task = next(x for x in mine["tasks"] if x["operation_id"] == t["operation_id"] and x["order_line_id"] == t["order_line_id"])
    assert task["fact_qty"] == t["plan_qty"] and task["state"] == "done"
    assert day["can_enter"] is True
    # бригадир видит только свой участок, сборщик — только себя
    fm = o.get("/reports/day", "foreman").json()
    assert {x["work_center_id"] for x in fm["workers"]} <= {o.wcs[0]["id"]}
    wk = o.get("/reports/day", "worker").json()
    assert {x["worker_id"] for x in wk["workers"]} <= {o.w1["id"]}
    assert o.get("/reports/day", "viewer").json()["can_enter"] is False
    past = (TODAY - timedelta(days=1)).isoformat()
    assert o.get("/reports/day", "worker", params={"day": past}).json()["can_enter"] is False
    # нагрузка бригадира — только его участок
    ld = o.get("/reports/load", "foreman").json()
    assert all(ld["worker_info"][n]["work_center_id"] == o.wcs[0]["id"] for n in ld["workers"])
