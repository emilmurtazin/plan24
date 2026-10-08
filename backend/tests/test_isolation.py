"""Изоляция организаций (ТЗ раздел 12): пользователь A не видит и не меняет данные B — ни по одному маршруту."""
import pytest
from conftest import Org

CRUD = ["/work-centers", "/workers", "/projects", "/products", "/order-lines", "/fact-reasons", "/holidays", "/absences"]


@pytest.fixture()
def two(env):
    a, b = Org(env, "a@a.ru", "Цех А"), Org(env, "b@b.ru", "Цех Б")
    a.seed("furniture"); b.seed("windows")
    w = b.get("/workers").json()["items"][0]["id"]
    b.post("/absences", json={"worker_id": w, "date_from": "2026-10-05", "date_to": "2026-10-06"})
    b.post("/holidays", json={"holiday_date": "2026-11-04", "name": "Б-праздник"})
    return a, b


def first_op(org, order_line_id):
    """Первая операция маршрута, на котором стоит позиция."""
    ol = org.get(f"/order-lines/{order_line_id}").json()
    for r in org.get(f"/products/{ol['product_id']}/routings").json():
        if r["id"] == ol["routing_id"]:
            return r["operations"][0]["id"]


def snapshot(org, path):
    return org.get(path).json()["items"]


@pytest.mark.parametrize("path", CRUD)
def test_no_overlap_and_foreign_ids_are_404(two, path):
    a, b = two
    ids_a, ids_b = set(a.ids(path)), set(b.ids(path))
    assert ids_b, f"в B нет данных для {path} — тест был бы пустым"
    assert not (ids_a & ids_b)
    before = snapshot(b, path)
    for i in ids_b:
        assert a.get(f"{path}/{i}").status_code == 404
        assert a.patch(f"{path}/{i}", json={}).status_code == 404
        assert a.delete(f"{path}/{i}").status_code == 404
    assert snapshot(b, path) == before                    # данные B не изменились


def test_sub_resources_are_isolated(two):
    a, b = two
    prod_b = b.ids("/products")[0]
    routing_b = b.get(f"/products/{prod_b}/routings").json()[0]
    op_b = routing_b["operations"][0]["id"]
    worker_b = b.ids("/workers")[0]
    ol_b = b.ids("/order-lines")[0]
    assert a.get(f"/products/{prod_b}/routings").status_code == 404
    assert a.post(f"/products/{prod_b}/routings").status_code == 404
    assert a.post(f"/routings/{routing_b['id']}/operations", json={"name": "Х", "norm_per_day": 1}).status_code == 404
    assert a.patch(f"/operations/{op_b}", json={"hold_qty": 5}).status_code == 404
    assert a.delete(f"/operations/{op_b}").status_code == 404
    assert a.get(f"/workers/{worker_b}/access").status_code == 404
    assert a.put(f"/workers/{worker_b}/access", json={"order_line_ids": [ol_b]}).status_code == 404
    # операция B не изменилась
    assert b.get(f"/products/{prod_b}/routings").json()[0]["operations"][0]["hold_qty"] == routing_b["operations"][0]["hold_qty"]


def test_cross_tenant_references_rejected(two):
    a, b = two
    pa, proda = a.ids("/projects")[0], a.ids("/products")[0]
    pb, prodb = b.ids("/projects")[0], b.ids("/products")[0]
    routing_b = b.get(f"/products/{prodb}/routings").json()[0]["id"]
    ol = lambda **kw: a.post("/order-lines", json={"project_id": pa, "product_id": proda, "qty": 5, **kw})
    assert ol().status_code == 201                                         # контроль: своё работает
    assert ol(project_id=pb).status_code == 404
    assert ol(product_id=prodb).status_code == 404
    assert ol(routing_id=routing_b).status_code == 404
    wa, wb = a.ids("/workers")[0], b.ids("/workers")[0]
    assert a.post("/absences", json={"worker_id": wb, "date_from": "2026-10-05", "date_to": "2026-10-05"}).status_code == 404
    assert a.post("/workers", json={"name": "Новый", "work_center_id": b.ids("/work-centers")[0]}).status_code == 404
    assert a.put(f"/workers/{wa}/access", json={"order_line_ids": [b.ids("/order-lines")[0]]}).status_code == 404
    assert a.post("/users", json={"email": "u@a.ru", "name": "Тест Тестов", "role": "foreman", "password": "password-1",
                                  "work_center_id": b.ids("/work-centers")[0]}).status_code == 404


def test_facts_cannot_reference_foreign_entities(two):
    a, b = two
    wa, wb = a.ids("/workers")[0], b.ids("/workers")[0]
    ola, olb = a.ids("/order-lines")[0], b.ids("/order-lines")[0]
    opa = first_op(a, ola)
    opb = first_op(b, olb)
    good = {"fact_date": "2026-10-02", "order_line_id": ola, "operation_id": opa, "worker_id": wa, "qty": 1}
    r = a.post("/facts", json=good)
    assert r.status_code == 200, r.text
    assert a.post("/facts", json={**good, "worker_id": wb}).status_code == 404
    assert a.post("/facts", json={**good, "order_line_id": olb}).status_code == 404
    assert a.post("/facts", json={**good, "operation_id": opb}).status_code == 404
    assert b.get("/facts").json()["items"] == []                         # факт A не виден B
    fid = r.json()["id"]
    assert b.delete(f"/facts/{fid}").status_code == 404
    assert b.post(f"/facts/{fid}/confirm").status_code == 404


def test_plans_and_reports_contain_only_own_data(two):
    a, b = two
    pa, pb = a.post("/plans/replan").json()["id"], b.post("/plans/replan").json()["id"]
    assert a.post(f"/plans/{pb}/activate").status_code == 404
    assert [p["id"] for p in a.get("/plans").json()["items"]] == [pa]
    assert {r["product"] for r in a.get("/reports/status").json()["rows"]} <= {"Шкаф", "Тумба"}
    assert {r["product"] for r in b.get("/reports/status").json()["rows"]} <= {"Окно ПВХ", "Витраж"}
    names_b = {w["name"] for w in b.get("/workers").json()["items"]}
    day_a = a.get("/reports/day").json()
    assert day_a["workers"] and not ({w["worker"] for w in day_a["workers"]} & names_b - {w["name"] for w in a.get("/workers").json()["items"]})
    ids_a = set(a.ids("/workers"))
    assert {w["worker_id"] for w in day_a["workers"]} <= ids_a
    assert set(a.get("/reports/load").json()["workers"]) <= {w["name"] for w in a.get("/workers").json()["items"]}


def test_users_and_audit_are_isolated(two):
    a, b = two
    ub = b.get("/users").json()[0]["id"]
    assert a.patch(f"/users/{ub}", json={"is_active": False}).status_code == 404
    assert b.get("/users").json()[0]["is_active"] is True
    assert {u["email"] for u in a.get("/users").json()} == {"a@a.ru"}
    b.post("/projects", json={"name": "Только у Б"})
    assert all("Только у Б" not in str(x) for x in a.get("/audit").json()["items"])


def test_demo_load_into_non_empty_org_is_rejected(two):
    a, _ = two
    assert a.post("/demo/load", params={"kind": "metal"}).status_code == 409
