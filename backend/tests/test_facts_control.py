"""Доступ бригадира к каталогу, удаление операции с переносом факта, факт «на заказ» и единое окно контроля факта."""
import pytest

from conftest import Org, TODAY

D = TODAY.isoformat()


def opts(o, role="owner"):
    r = o.get("/facts/order-options", role)
    assert r.status_code == 200, r.text
    return r.json()


def mine(o):
    """Позиция и операция, относящиеся к участку бригадира."""
    for it in opts(o, "foreman")["items"]:
        for op in it["operations"]:
            return it, op
    pytest.skip("в демо нет операций участка бригадира")


def post_fact(o, it, op, qty, role="foreman", **kw):
    return o.post("/facts/order", role, json={"fact_date": D, "order_line_id": it["id"], "operation_id": op["id"], "qty": qty, **kw})


# ---------------------------------------------------------------- 1. каталог для бригадира
def test_foreman_edits_catalog(full_org):
    o = full_org
    r = o.post("/products", "foreman", json={"name": "Изделие бригадира"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert o.patch(f"/products/{pid}", "foreman", json={"unit": "компл"}).status_code == 200
    rid = o.get(f"/products/{pid}/routings", "foreman").json()[0]["id"]
    op = o.post(f"/routings/{rid}/operations", "foreman", json={"name": "Сборка рамы", "norm_per_day": 5})
    assert op.status_code == 201, op.text
    assert o.patch(f"/operations/{op.json()['id']}", "foreman", json={"norm_per_day": 6}).status_code == 200
    assert o.delete(f"/operations/{op.json()['id']}", "foreman").status_code == 204
    assert o.delete(f"/products/{pid}", "foreman").status_code == 204
    assert o.get("/products", "worker").status_code == 403


# ---------------------------------------------------------------- 2. удаление операции с фактом
def one_routing_with_facts(o):
    """Позиция, у которой в маршруте ≥ 2 операций; факт вносится на первую."""
    for it in opts(o)["items"]:
        if len(it["operations"]) >= 2:
            return it
    pytest.skip("нет позиции с двумя операциями")


def test_delete_operation_with_fact_asks_where_to_move(full_org):
    o = full_org
    it = one_routing_with_facts(o)
    a, b = it["operations"][:2]
    assert post_fact(o, it, a, 3, "owner").status_code == 200
    assert post_fact(o, it, b, 2, "owner").status_code == 200
    info = o.get(f"/operations/{a['id']}/delete-info").json()
    assert info["facts"] == 1 and info["qty"] == 3 and b["id"] in [t["id"] for t in info["targets"]] and info["blocked"] is None
    assert o.delete(f"/operations/{a['id']}").status_code == 409                          # без цели — отказ, как и раньше
    assert o.delete(f"/operations/{a['id']}", params={"move_facts_to": a["id"]}).status_code == 422
    assert o.delete(f"/operations/{a['id']}", params={"move_facts_to": 999999}).status_code == 404
    assert o.delete(f"/operations/{a['id']}", params={"move_facts_to": b["id"]}).status_code == 204
    after = [x for x in opts(o)["items"] if x["id"] == it["id"]][0]["operations"]
    assert a["id"] not in [x["id"] for x in after]
    assert next(x for x in after if x["id"] == b["id"])["done"] == 5                       # 3 + 2: факт сложился, не потерян
    ev = [e for e in o.get("/audit?limit=50").json()["items"] if e["entity"] == "operation" and e["action"] == "delete"]
    assert ev and ev[0]["changes"]["facts_moved"]["to"] == b["id"] and ev[0]["changes"]["facts_moved"]["merged"] == 1


def test_delete_operation_moves_fact_without_conflict(full_org):
    o = full_org
    it = one_routing_with_facts(o)
    a, b = it["operations"][:2]
    assert post_fact(o, it, a, 4, "owner").status_code == 200
    assert o.delete(f"/operations/{a['id']}", params={"move_facts_to": b["id"]}).status_code == 204
    j = o.get("/facts/journal", params={"date_from": D, "date_to": D, "order_line_id": it["id"]}).json()
    assert [(x["operation"], x["qty"]) for x in j["items"]] == [(b["name"], 4)]


def test_delete_without_fact_needs_no_target(full_org):
    o = full_org
    it = one_routing_with_facts(o)
    a = it["operations"][0]
    assert o.get(f"/operations/{a['id']}/delete-info").json()["facts"] == 0
    assert o.delete(f"/operations/{a['id']}").status_code == 204


# ---------------------------------------------------------------- 3. факт «на заказ»
def test_order_fact_roles_and_replacement(full_org):
    o = full_org
    it, op = mine(o)
    r = post_fact(o, it, op, 2)
    assert r.status_code == 200, r.text
    assert r.json()["worker_id"] is None and r.json()["work_center_id"] == o.wcs[0]["id"] and r.json()["status"] == "confirmed"
    again = post_fact(o, it, op, 3).json()
    assert again["id"] == r.json()["id"] and again["replaced"] is True and again["previous_qty"] == 2     # итог дня заменяется
    assert post_fact(o, it, op, 1, "manager", work_center_id=o.wcs[0]["id"]).status_code == 200            # РП — на выбранный участок
    assert post_fact(o, it, op, 1, "owner", work_center_id=o.wcs[0]["id"]).json()["replaced"] is True
    for role in ("worker", "viewer", "technologist", "director"):
        assert post_fact(o, it, op, 1, role).status_code == 403
    fut = o.post("/facts/order", "foreman", json={"fact_date": "2026-12-31", "order_line_id": it["id"], "operation_id": op["id"], "qty": 1})
    assert fut.status_code == 422


def test_foreman_cannot_enter_for_other_section(full_org):
    o = full_org
    it, op = mine(o)
    other = o.wcs[1]["id"]
    # руководитель переводит операцию на другой участок
    assert o.put(f"/order-lines/{it['id']}/operations", "manager", json={"assignments": [{"operation_id": op["id"], "work_center_ids": [other]}]}).status_code == 200
    assert post_fact(o, it, op, 1, "foreman").status_code == 403                               # у бригадира этой операции больше нет
    assert op["id"] not in [x["id"] for p in opts(o, "foreman")["items"] if p["id"] == it["id"] for x in p["operations"]]
    r = post_fact(o, it, op, 1, "manager")                                                     # РП: участок подставился сам (он единственный)
    assert r.status_code == 200 and r.json()["work_center_id"] == other


def test_order_fact_over_required_needs_comment(full_org):
    o = full_org
    it, op = mine(o)
    big = op["required"] + 5
    assert post_fact(o, it, op, big).status_code == 422
    assert post_fact(o, it, op, big, note="переделка").status_code == 200


def test_order_fact_counts_in_progress_and_status(full_org):
    o = full_org
    o.post("/plans/replan")
    it, op = mine(o)
    before = next(x for x in o.get("/reports/order-status", params={"order_line_id": it["id"]}).json()["operations"] if x["name"] == op["name"])["done"]
    assert post_fact(o, it, op, 2).status_code == 200
    after = next(x for x in o.get("/reports/order-status", params={"order_line_id": it["id"]}).json()["operations"] if x["name"] == op["name"])["done"]
    assert after == before + 2


def test_order_fact_can_be_deleted_by_own_section_only(full_org):
    o = full_org
    it, op = mine(o)
    fid = post_fact(o, it, op, 1).json()["id"]
    assert o.delete(f"/facts/{fid}", "worker").status_code == 403
    assert o.delete(f"/facts/{fid}", "foreman").status_code == 204
    j = o.get("/facts/journal", params={"date_from": D, "date_to": D}).json()
    assert fid not in [x["id"] for x in j["items"]]


# ---------------------------------------------------------------- 4. окно контроля факта
def test_journal_shows_worker_and_order_facts_with_filters(full_org):
    o = full_org
    it, op = mine(o)
    assert post_fact(o, it, op, 2).status_code == 200                                      # на заказ
    w = next(w for w in o.workers if w["work_center_id"] == o.wcs[0]["id"])
    assert o.post("/facts", json={"fact_date": D, "order_line_id": it["id"], "operation_id": op["id"], "worker_id": w["id"], "qty": 1,
                                  "note": "тест"}).status_code == 200
    j = o.get("/facts/journal", params={"date_from": D, "date_to": D}).json()
    assert j["summary"]["count"] == len(j["items"]) == 2 and j["summary"]["qty"] == 3 and j["summary"]["unlinked"] == 1
    unl = next(x for x in j["items"] if x["worker_id"] is None)
    assert unl["worker"] is None and unl["section"] == o.wcs[0]["name"] and unl["operation"] == op["name"] and unl["entered_by"]
    assert unl["product"] and unl["project"] and unl["order"]
    by = lambda **p: o.get("/facts/journal", params={"date_from": D, "date_to": D, **p}).json()
    assert by(no_worker="true")["summary"]["count"] == 1 and by(worker_id=w["id"])["summary"]["count"] == 1
    assert by(deviations="true")["summary"]["count"] == 1                                    # только с комментарием/причиной
    assert by(work_center_id=o.wcs[1]["id"])["summary"]["count"] == 0 and by(work_center_id=o.wcs[0]["id"])["summary"]["count"] == 2
    assert by(operation=op["name"][:3].upper())["summary"]["count"] == 2
    assert by(order_line_id=it["id"], status="submitted")["summary"]["count"] == 0
    assert by(limit=1)["total"] == 2 and len(by(limit=1)["items"]) == 1 and len(by(limit=1, offset=1)["items"]) == 1
    assert o.get("/facts/journal", params={"date_from": "2026-01-01", "date_to": "2026-01-02"}).json()["items"] == []


def test_journal_visibility_by_role(full_org, env):
    o = full_org
    it, op = mine(o)
    post_fact(o, it, op, 2)
    other = next((c for c in o.wcs[1:]), None)
    foreign = None
    for x in opts(o)["items"]:
        for p in x["operations"]:
            if other and other["id"] in p["centers"] and o.wcs[0]["id"] not in p["centers"]:
                foreign = (x, p)
    if foreign:
        post_fact(o, *foreign, 1, "manager", work_center_id=other["id"])
    j = o.get("/facts/journal", "foreman", params={"date_from": D, "date_to": D}).json()
    assert j["items"] and all(x["section_id"] == o.wcs[0]["id"] for x in j["items"])          # бригадир — только свой участок
    for role in ("manager", "viewer", "director"):
        assert o.get("/facts/journal", role, params={"date_from": D, "date_to": D}).status_code == 200
    assert o.get("/facts/journal", "worker").status_code == 403
    stranger = Org(env, "x@b.ru", "Цех Б")
    assert stranger.get("/facts/journal", params={"date_from": D, "date_to": D}).json()["items"] == []


def test_journal_default_period_is_last_week(full_org):
    o = full_org
    it, op = mine(o)
    post_fact(o, it, op, 1)
    assert o.get("/facts/journal").json()["summary"]["count"] == 1


def test_replan_and_dashboard_work_with_order_facts(full_org):
    o = full_org
    o.post("/plans/replan")
    it, op = mine(o)
    assert post_fact(o, it, op, 2, note="без исполнителя").status_code == 200
    assert o.replan_all().status_code == 201                                                   # планировщик учитывает факт без работника
    assert o.get("/reports/overview").status_code == 200 and o.get("/reports/overview", "foreman").status_code == 200
    assert o.get("/reports/status").status_code == 200 and o.get("/reports/day", "foreman").status_code == 200
    ov = o.get("/reports/overview", "foreman").json()
    assert any(r["reason"] == "Без причины (комментарий)" for r in ov["reasons"])               # комментарий факта «на заказ» виден бригадиру своего участка
