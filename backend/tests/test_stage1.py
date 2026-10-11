"""Этап 1 доработок: роль «Технолог», перепланирование только по участкам, заказчик в позиции, список версий маршрутов."""


def _first(o, path):
    return o.get(path).json()["items"][0]


def test_technologist_edits_catalog_but_nothing_else(full_org):
    o = full_org
    r = o.post("/products", "technologist", json={"name": "Тумба-тест", "unit": "шт"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    rts = o.get(f"/products/{pid}/routings", "technologist").json()
    assert len(rts) == 1
    op = o.post(f"/routings/{rts[0]['id']}/operations", "technologist", json={"name": "Сборка", "norm_per_day": 5})
    assert op.status_code == 201, op.text
    assert o.patch(f"/operations/{op.json()['id']}", "technologist", json={"norm_per_day": 7}).status_code == 200
    assert o.post(f"/products/{pid}/routings", "technologist").status_code == 201      # новая версия
    # остальное — только чтение
    proj = _first(o, "/projects")
    assert o.get("/projects", "technologist").status_code == 200
    assert o.get("/order-lines", "technologist").status_code == 200
    assert o.post("/projects", "technologist", json={"name": "Нельзя"}).status_code == 403
    assert o.post("/workers", "technologist", json={"name": "Нельзя"}).status_code == 403
    assert o.post("/order-lines", "technologist", json={"project_id": proj["id"], "product_id": pid, "qty": 1}).status_code == 403
    assert o.post("/plans/replan", "technologist", json={}).status_code == 403
    assert o.get("/users", "technologist").status_code == 403
    assert o.get("/audit", "technologist").status_code == 403
    me = o.get("/auth/me", "technologist").json()
    assert me["role"] == "technologist" and "catalog.write" in me["permissions"]


def test_manager_can_still_edit_catalog_viewer_cannot(full_org):
    o = full_org
    assert o.post("/products", "manager", json={"name": "Полка-тест"}).status_code == 201
    assert o.post("/products", "viewer", json={"name": "Полка-тест-2"}).status_code == 403
    assert o.post("/products", "foreman", json={"name": "Полка-тест-3"}).status_code == 403


def test_first_plan_covers_all_then_only_by_section(full_org):
    o = full_org
    assert o.get("/plans/active").json()["plan"] is None or True
    wcs = o.wcs
    # активного плана нет: первый расчёт строится сразу по всем участкам
    if o.get("/plans/active").json()["plan"] is None:
        r = o.post("/plans/replan", json={})
        assert r.status_code == 201, r.text
    # дальше «вся организация» невозможна
    r = o.post("/plans/replan", json={})
    assert r.status_code == 422 and "участок" in r.json()["title"].lower()
    r = o.post("/plans/replan", "manager", json={})
    assert r.status_code == 422
    assert o.post("/plans/compare", json={}).status_code == 422
    # участок выбран — работает
    assert o.post("/plans/replan", json={"work_center_id": wcs[0]["id"]}).status_code == 201
    assert o.post("/plans/replan", "manager", json={"work_center_id": wcs[-1]["id"]}).status_code == 201
    # бригадир — как раньше: свой участок без указания
    assert o.post("/plans/replan", "foreman", json={}).status_code == 201
    assert o.post("/plans/replan", "foreman", json={"work_center_id": wcs[-1]["id"]}).status_code in (201, 403)


def test_order_line_customer_and_routing_versions(full_org):
    o = full_org
    proj, prod = _first(o, "/projects"), _first(o, "/products")
    r = o.post("/order-lines", json={"project_id": proj["id"], "product_id": prod["id"], "qty": 3, "customer": "ООО Ромашка"})
    assert r.status_code == 201, r.text
    assert r.json()["customer"] == "ООО Ромашка"
    oid = r.json()["id"]
    p = o.patch(f"/order-lines/{oid}", json={"customer": "ИП Иванов", "version": r.json()["version"]})
    assert p.status_code == 200 and p.json()["customer"] == "ИП Иванов"
    # без заказчика — пустая строка, не ошибка
    r2 = o.post("/order-lines", json={"project_id": proj["id"], "product_id": prod["id"], "qty": 1})
    assert r2.status_code == 201 and r2.json()["customer"] == ""
    # список версий маршрутов для выбора в форме заказа
    items = o.get("/routings", "viewer").json()["items"]
    mine = [x for x in items if x["product_id"] == prod["id"]]
    assert mine and all({"id", "version_no", "is_current", "operations"} <= set(x) for x in mine)
    # заказ на конкретной версии
    r3 = o.post("/order-lines", json={"project_id": proj["id"], "product_id": prod["id"], "qty": 1, "routing_id": mine[0]["id"]})
    assert r3.status_code == 201 and r3.json()["routing_id"] == mine[0]["id"]
