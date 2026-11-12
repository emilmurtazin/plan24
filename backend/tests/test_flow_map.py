"""«Карта потока»: карточки-операции, связи-предшественники, выполнение по заказу, положение карточек."""
import pytest

from conftest import TODAY

D = TODAY.isoformat()


def new_routing(o, names=("Резка", "Сборка", "Покраска"), role="owner"):
    """Изделие с маршрутом из операций с порядком 1, 2, 3 (предшественники не заданы — связи «по порядку»)."""
    pid = o.post("/products", role, json={"name": "Карта-тест"}).json()["id"]
    rid = o.get(f"/products/{pid}/routings", role).json()[0]["id"]
    ids = {}
    for i, n in enumerate(names, start=1):
        r = o.post(f"/routings/{rid}/operations", role, json={"name": n, "norm_per_day": 10, "sequence": i})
        assert r.status_code == 201, r.text
        ids[n] = r.json()["id"]
    return rid, ids


def fmap(o, rid, role="owner", **kw):
    r = o.get("/flow-map", role, params={"routing_id": rid, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def pred(m, op_id):
    return next(n for n in m["nodes"] if n["id"] == op_id)["predecessors"]


def test_implicit_links_follow_sequence(full_org):
    o = full_org
    rid, ids = new_routing(o)
    m = fmap(o, rid)
    assert [n["name"] for n in m["nodes"]] == ["Резка", "Сборка", "Покраска"] and m["can_edit"] is True and m["line"] is None
    assert {(e["from"], e["to"], e["implicit"]) for e in m["edges"]} == {(ids["Резка"], ids["Сборка"], True), (ids["Сборка"], ids["Покраска"], True)}


def test_add_and_delete_links(full_org):
    o = full_org
    rid, ids = new_routing(o)
    a, b, c = ids["Резка"], ids["Сборка"], ids["Покраска"]
    assert o.post(f"/flow-map/{rid}/links", json={"from_id": a, "to_id": c}).status_code == 200
    m = fmap(o, rid)
    assert pred(m, c) == ["Сборка", "Резка"] and all(not e["implicit"] for e in m["edges"] if e["to"] == c)      # список стал явным, неявная связь сохранилась
    assert o.post(f"/flow-map/{rid}/links", json={"from_id": a, "to_id": c}).status_code == 409                 # повтор
    assert o.post(f"/flow-map/{rid}/links", json={"from_id": c, "to_id": a}).status_code == 422                 # замкнутый круг
    assert o.post(f"/flow-map/{rid}/links", json={"from_id": c, "to_id": c}).status_code == 422
    assert o.delete(f"/flow-map/{rid}/links", params={"from_id": b, "to_id": c}).status_code == 200
    assert pred(fmap(o, rid), c) == ["Резка"]
    assert o.delete(f"/flow-map/{rid}/links", params={"from_id": a, "to_id": c}).status_code == 200
    m = fmap(o, rid)
    assert pred(m, c) == [] and not [e for e in m["edges"] if e["to"] == c]                                      # без предшественников
    assert o.delete(f"/flow-map/{rid}/links", params={"from_id": a, "to_id": c}).status_code == 404
    assert o.post(f"/flow-map/{rid}/links", json={"from_id": a, "to_id": 999999}).status_code == 404


def test_link_against_order_shifts_sequence_and_keeps_cycles_out(full_org):
    o = full_org
    rid, ids = new_routing(o)
    c = ids["Покраска"]
    x = o.post(f"/routings/{rid}/operations", json={"name": "Контроль", "norm_per_day": 5, "sequence": 3, "predecessors": []}).json()["id"]
    p = o.post(f"/routings/{rid}/operations", json={"name": "Упаковка", "norm_per_day": 5, "sequence": 5, "predecessors": ["Покраска"]}).json()["id"]
    assert o.post(f"/flow-map/{rid}/links", json={"from_id": p, "to_id": x}).status_code == 200            # «Упаковка» → «Контроль»: порядок Контроля сдвигается
    m = fmap(o, rid)
    seq = {n["id"]: n["sequence"] for n in m["nodes"]}
    assert seq[x] > seq[p] and pred(m, x) == ["Упаковка"]
    assert o.post(f"/flow-map/{rid}/links", json={"from_id": x, "to_id": p}).status_code == 422             # Упаковка → Контроль → Упаковка: круг
    assert o.post(f"/flow-map/{rid}/links", json={"from_id": x, "to_id": c}).status_code == 422             # Покраска → Упаковка → Контроль → Покраска: круг


def test_layout_saved_and_returned(full_org):
    o = full_org
    rid, ids = new_routing(o)
    assert all(n["x"] is None for n in fmap(o, rid)["nodes"])
    r = o.put(f"/flow-map/{rid}/layout", json={"positions": [{"id": ids["Резка"], "x": 40.5, "y": 80}, {"id": ids["Сборка"], "x": 300, "y": 80}]})
    assert r.status_code == 200 and r.json()["saved"] == 2
    n = next(n for n in fmap(o, rid)["nodes"] if n["id"] == ids["Резка"])
    assert (n["x"], n["y"]) == (40.5, 80)
    assert o.put(f"/flow-map/{rid}/layout", json={"positions": [{"id": 999999, "x": 1, "y": 1}]}).status_code == 404
    assert o.put(f"/flow-map/{rid}/layout", json={"positions": [{"id": 1, "x": 1e9, "y": 1}]}).status_code == 422


def test_add_card_with_position_and_delete_card_cleans_links(full_org):
    o = full_org
    rid, ids = new_routing(o)
    new = o.post(f"/routings/{rid}/operations", json={"name": "Упаковка", "norm_per_day": 20, "sequence": 4, "predecessors": ["Покраска"]})
    assert new.status_code == 201
    nid = new.json()["id"]
    assert o.put(f"/flow-map/{rid}/layout", json={"positions": [{"id": nid, "x": 900, "y": 10}]}).status_code == 200
    assert pred(fmap(o, rid), nid) == ["Покраска"]
    assert o.delete(f"/operations/{ids['Покраска']}").status_code == 204                                         # карточку с связями можно удалить
    m = fmap(o, rid)
    assert [n["name"] for n in m["nodes"]] == ["Резка", "Сборка", "Упаковка"] and not [e for e in m["edges"] if ids["Покраска"] in (e["from"], e["to"])]


def line_and_op(o):
    for it in o.get("/facts/order-options").json()["items"]:
        if len(it["operations"]) >= 2:
            return it
    pytest.skip("нет позиции с двумя операциями")


def test_progress_colors_come_from_facts(full_org):
    o = full_org
    it = line_and_op(o)
    a, b = it["operations"][:2]
    m = o.get("/flow-map", params={"order_line_id": it["id"]}).json()
    assert m["line"]["id"] == it["id"] and m["line"]["percent"] == 0 and all(n["done"] == 0 and n["pct"] == 0 for n in m["nodes"])
    full = next(n for n in m["nodes"] if n["id"] == a["id"])["required"]
    assert o.post("/facts/order", json={"fact_date": D, "order_line_id": it["id"], "operation_id": a["id"], "qty": full}).status_code == 200
    m2 = o.get("/flow-map", params={"order_line_id": it["id"]}).json()
    na = next(n for n in m2["nodes"] if n["id"] == a["id"])
    assert na["done"] == full and na["pct"] == 100 and m2["line"]["percent"] > 0
    assert all(n["pct"] == 0 for n in m2["nodes"] if n["id"] != a["id"])


def test_targets_and_validation(full_org):
    o = full_org
    t = o.get("/flow-map/targets").json()
    assert t["lines"] and t["routings"] and {"id", "project", "order", "product", "routing_id"} <= set(t["lines"][0])
    assert o.get("/flow-map").status_code == 422                                           # нужно что-то одно
    assert o.get("/flow-map", params={"order_line_id": 1, "routing_id": 1}).status_code == 422
    assert o.get("/flow-map", params={"routing_id": 999999}).status_code == 404


def test_roles(full_org):
    o = full_org
    rid, ids = new_routing(o)
    a, b = ids["Резка"], ids["Сборка"]
    assert fmap(o, rid, "viewer")["can_edit"] is False and fmap(o, rid, "director")["can_edit"] is False
    assert o.get("/flow-map", "worker", params={"routing_id": rid}).status_code == 403
    assert o.post(f"/flow-map/{rid}/links", "viewer", json={"from_id": a, "to_id": b}).status_code == 403
    assert o.put(f"/flow-map/{rid}/layout", "viewer", json={"positions": []}).status_code == 403
    assert o.post(f"/flow-map/{rid}/links", "foreman", json={"from_id": a, "to_id": b}).status_code == 200      # бригадир правит каталог
    assert fmap(o, rid, "foreman")["can_edit"] is True


def test_foreman_does_not_see_foreign_progress(full_org):
    o = full_org
    it = line_and_op(o)
    mine_op = next(op for op in it["operations"] if o.wcs[0]["id"] in op["centers"]) if any(o.wcs[0]["id"] in op["centers"] for op in it["operations"]) else None
    if mine_op is None:
        pytest.skip("нет операции участка бригадира")
    other = o.wcs[1]["id"]
    foreign = next((op for op in it["operations"] if op["id"] != mine_op["id"]), None)
    assert o.put(f"/order-lines/{it['id']}/operations", "manager", json={"assignments": [{"operation_id": foreign["id"], "work_center_ids": [other]}]}).status_code == 200
    m = o.get("/flow-map", "foreman", params={"order_line_id": it["id"]}).json()
    nf = next(n for n in m["nodes"] if n["id"] == foreign["id"])
    assert nf["foreign"] is True and nf["done"] is None and nf["pct"] is None
    nm = next(n for n in m["nodes"] if n["id"] == mine_op["id"])
    assert nm["foreign"] is False and nm["done"] == 0
