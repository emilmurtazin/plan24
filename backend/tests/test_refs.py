"""Правила данных: версии маршрутов (BR-7), допуски, настройки, валидация, пагинация, оптимистичная блокировка."""
from conftest import Org


def mk_product(o, name="Стол", ops=(("Раскрой", 40, 1), ("Сборка", 6, 2))):
    pid = o.post("/products", json={"name": name}).json()["id"]
    rid = o.get(f"/products/{pid}/routings").json()[0]["id"]
    for name_, norm, seq in ops:
        assert o.post(f"/routings/{rid}/operations", json={"name": name_, "norm_per_day": norm, "sequence": seq}).status_code == 201
    return pid, rid


def mk_order(o, pid, **kw):
    prj = (o.ids("/projects") or [o.post("/projects", json={"name": "П"}).json()["id"]])[0]
    return o.post("/order-lines", json={"project_id": prj, "product_id": pid, "qty": 10, **kw})


def test_product_gets_routing_v1_and_order_needs_operations(org):
    pid = org.post("/products", json={"name": "Пустышка"}).json()["id"]
    rs = org.get(f"/products/{pid}/routings").json()
    assert len(rs) == 1 and rs[0]["version_no"] == 1 and rs[0]["is_current"] and rs[0]["operations"] == []
    r = mk_order(org, pid)
    assert r.status_code == 422 and "операц" in r.json()["title"]


def test_routing_is_immutable_once_used_but_hold_is_editable(org):
    pid, rid = mk_product(org)
    assert mk_order(org, pid).status_code == 201
    op = org.get(f"/products/{pid}/routings").json()[0]["operations"][0]
    assert org.post(f"/routings/{rid}/operations", json={"name": "Покраска", "norm_per_day": 5}).status_code == 409
    assert org.patch(f"/operations/{op['id']}", json={"norm_per_day": 99}).status_code == 409
    assert org.delete(f"/operations/{op['id']}").status_code == 409
    ok = org.patch(f"/operations/{op['id']}", json={"hold_qty": 4, "hold_reason": "ждём фурнитуру"})
    assert ok.status_code == 200 and ok.json()["hold_qty"] == 4


def test_new_routing_version_keeps_old_orders_on_old_version(org):
    pid, rid1 = mk_product(org)
    o1 = mk_order(org, pid, ext_id="A1").json()
    v2 = org.post(f"/products/{pid}/routings").json()
    assert v2["version_no"] == 2 and v2["is_current"] and len(v2["operations"]) == 2
    rs = {r["version_no"]: r for r in org.get(f"/products/{pid}/routings").json()}
    assert rs[1]["is_current"] is False and rs[1]["used"] is True and rs[2]["used"] is False
    # новая версия редактируется свободно
    op2 = v2["operations"][0]["id"]
    assert org.patch(f"/operations/{op2}", json={"norm_per_day": 55}).status_code == 200
    o2 = mk_order(org, pid, ext_id="A2").json()
    assert o1["routing_id"] == rid1 and o2["routing_id"] == v2["id"]
    # перевод старой позиции на новую версию — явное действие
    assert org.patch(f"/order-lines/{o1['id']}", json={"routing_id": v2["id"]}).json()["routing_id"] == v2["id"]


def test_order_ext_id_unique_and_cross_product_routing_rejected(org):
    p1, r1 = mk_product(org, "Стол")
    p2, r2 = mk_product(org, "Стул", (("Сборка", 9, 1),))
    assert mk_order(org, p1, ext_id="X-1").status_code == 201
    assert mk_order(org, p1, ext_id="X-1").status_code == 409
    assert mk_order(org, p2, routing_id=r1).status_code == 422               # маршрут другого изделия
    o = mk_order(org, p2, ext_id="X-2").json()
    assert org.patch(f"/order-lines/{o['id']}", json={"ext_id": "X-1"}).status_code == 409
    assert org.patch(f"/order-lines/{o['id']}", json={"routing_id": r1}).status_code == 422


def test_soft_delete_order_unlinks_access_and_frees_ext_id(full_org):
    o = full_org
    ol = o.ids("/order-lines")[0]
    w = o.workers[0]["id"]
    o.put(f"/workers/{w}/access", json={"order_line_ids": [ol]})
    ext = o.get(f"/order-lines/{ol}").json()["ext_id"]
    assert o.delete(f"/order-lines/{ol}").status_code == 204
    assert o.get(f"/order-lines/{ol}").status_code == 404
    assert o.get(f"/workers/{w}/access").json()["order_line_ids"] == []
    pid = o.get("/products").json()["items"][0]["id"]
    assert o.post("/order-lines", json={"project_id": o.ids("/projects")[0], "product_id": pid, "qty": 1, "ext_id": ext}).status_code == 201


def test_cannot_delete_used_reference_data(full_org):
    o = full_org
    pid = o.get("/products").json()["items"][0]["id"]
    assert o.delete(f"/products/{pid}").status_code == 409
    prj = o.ids("/projects")[0]
    assert o.delete(f"/projects/{prj}").status_code == 409
    wc = o.wcs[0]["id"]
    assert o.delete(f"/work-centers/{wc}").status_code == 409                  # к участку привязаны исполнители


def test_optimistic_locking(org):
    wc = org.post("/work-centers", json={"name": "Сборка"}).json()
    assert wc["version"] == 1
    ok = org.patch(f"/work-centers/{wc['id']}", json={"name": "Сборка-1", "version": 1})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = org.patch(f"/work-centers/{wc['id']}", json={"name": "Сборка-2", "version": 1})
    assert stale.status_code == 409
    assert org.get(f"/work-centers/{wc['id']}").json()["name"] == "Сборка-1"


def test_validation_errors_are_structured_ru(org):
    r = org.post("/workers", json={"name": "Иван", "perf": 0})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["errors"][0]["field"] == "perf"
    assert org.post("/absences", json={"worker_id": 1, "date_from": "2026-10-09", "date_to": "2026-10-01"}).status_code == 422
    assert org.patch("/settings", json={"priority_strictness": 150}).status_code == 422
    assert org.patch("/settings", json={"strictness_by_level": {"x": 10}}).status_code == 422
    assert org.patch("/settings", json={"strictness_by_level": {"2": 120}}).status_code == 422
    assert org.patch("/settings", json={"work_weekdays": "7,8"}).status_code == 422


def test_settings_patch_is_audited(org):
    s = org.patch("/settings", json={"priority_strictness": 60, "freeze_days": 1}).json()
    assert s["priority_strictness"] == 60 and s["freeze_days"] == 1
    ev = [a for a in org.get("/audit").json()["items"] if a["entity"] == "settings"]
    assert ev and ev[0]["changes"]["priority_strictness"] == [100.0, 60.0]


def test_pagination_cursor_collects_everything(org):
    for i in range(7):
        org.post("/work-centers", json={"name": f"Уч-{i}"})
    seen, cursor = [], 0
    while True:
        r = org.get("/work-centers", params={"limit": 3, "cursor": cursor}).json()
        seen += [x["id"] for x in r["items"]]
        if r["next_cursor"] is None:
            break
        cursor = r["next_cursor"]
    assert len(seen) == 7 and len(set(seen)) == 7


def test_duplicate_names_conflict(org):
    assert org.post("/work-centers", json={"name": "Сборка"}).status_code == 201
    assert org.post("/work-centers", json={"name": "Сборка"}).status_code == 409
    pid, rid = mk_product(org)
    assert org.post(f"/routings/{rid}/operations", json={"name": "Раскрой", "norm_per_day": 1}).status_code == 409
