"""Этап 3 доработок: операции позиции заказа распределяются по участкам; нераспределённая операция — ошибка."""
from sqlalchemy import select

from plan24 import db as dbm
from plan24.models import Plan, PlanItem, Worker


def _new_order(o, qty=8, ext="D-1", prio=1):
    prj, prod = o.ids("/projects")[0], o.ids("/products")[0]
    return o.post("/order-lines", json={"project_id": prj, "product_id": prod, "qty": qty, "ext_id": ext, "priority": prio}).json()


def _ops(o, oid, role="owner"):
    return o.get(f"/order-lines/{oid}/operations", role).json()


def _put(o, oid, mapping, role="owner"):
    """mapping: {имя операции: [id участков]}"""
    cur = _ops(o, oid)["items"]
    return o.put(f"/order-lines/{oid}/operations", role,
                 json={"assignments": [{"operation_id": i["operation_id"], "work_center_ids": mapping.get(i["name"], [])} for i in cur]})


def _plan_items(o, oid):
    """Строки активного плана позиции: (операция, исполнитель, участок исполнителя, кол-во)."""
    s = dbm.admin_session()
    try:
        pid = s.scalar(select(Plan.id).where(Plan.status == "active"))
        w = {x.id: x.work_center_id for x in s.scalars(select(Worker))}
        return [(i.operation_id, i.worker_id, w[i.worker_id], i.qty)
                for i in s.scalars(select(PlanItem).where(PlanItem.plan_id == pid, PlanItem.order_line_id == oid))]
    finally:
        s.close()


def test_new_order_is_not_distributed_and_not_planned(full_org):
    o = full_org
    o.replan_all()
    new = _new_order(o)
    v = _ops(o, new["id"])
    assert v["total"] >= 2 and v["distributed"] == 0 and len(v["centers"]) == 2
    summ = {x["order_line_id"]: x for x in o.get("/order-distribution").json()["items"]}
    assert summ[new["id"]]["distributed"] == 0 and summ[new["id"]]["total"] == v["total"]
    # старые (демо) позиции распределены целиком
    old = next(x for x in summ.values() if x["order_line_id"] != new["id"])
    assert old["distributed"] == old["total"]
    o.post("/plans/replan", json={"work_center_id": o.wcs[0]["id"]})
    warns = [w for w in o.get("/plans/active").json()["plan"]["warnings"] if w["order_line_id"] == new["id"]]
    assert warns and {w["code"] for w in warns} == {"NOT_DISTRIBUTED"}
    assert "не распределена по участкам" in warns[0]["text"]
    assert not _plan_items(o, new["id"])


def test_operations_go_only_to_assigned_sections(full_org):
    o = full_org
    o.replan_all()
    new = _new_order(o)
    ops = [i["name"] for i in _ops(o, new["id"])["items"]]
    a, b = o.wcs[0]["id"], o.wcs[1]["id"]
    # первая операция — на участок A, остальные — на участок B
    r = _put(o, new["id"], {ops[0]: [a], **{n: [b] for n in ops[1:]}})
    assert r.status_code == 200 and r.json()["distributed"] == len(ops)
    for wc in (a, b):
        assert o.post("/plans/replan", json={"work_center_id": wc}).status_code == 201
    plan = o.get("/plans/active").json()["plan"]
    its = _plan_items(o, new["id"])
    opname = {i["operation_id"]: i["name"] for i in _ops(o, new["id"])["items"]}
    assert its, "позиция распределена, но в плане её нет"
    for op_id, _, center, _ in its:
        want = a if opname[op_id] == ops[0] else b
        assert center == want, (opname[op_id], center)
    assert {opname[i[0]] for i in its} <= set(ops)
    assert not [w for w in plan["warnings"] if w["order_line_id"] == new["id"] and w["code"] == "NOT_DISTRIBUTED"]


def test_distribution_validation_permissions_and_audit(full_org):
    o = full_org
    new = _new_order(o)
    ops = _ops(o, new["id"])["items"]
    a = o.wcs[0]["id"]
    assert _put(o, new["id"], {ops[0]["name"]: [a]}, "foreman").status_code == 403
    assert _put(o, new["id"], {}, "technologist").status_code == 403
    bad = o.put(f"/order-lines/{new['id']}/operations", json={"assignments": [{"operation_id": 999999, "work_center_ids": [a]}]})
    assert bad.status_code == 422
    bad = o.put(f"/order-lines/{new['id']}/operations", json={"assignments": [{"operation_id": ops[0]["operation_id"], "work_center_ids": [999999]}]})
    assert bad.status_code == 422
    # несколько участков у одной операции; повторное сохранение того же ничего не пишет в журнал
    ok = _put(o, new["id"], {ops[0]["name"]: [a, o.wcs[1]["id"]]})
    assert ok.status_code == 200
    assert next(i for i in ok.json()["items"] if i["name"] == ops[0]["name"])["work_center_ids"] == sorted([a, o.wcs[1]["id"]])
    n = len([x for x in o.get("/audit").json()["items"] if x["action"] == "distribute"])
    _put(o, new["id"], {ops[0]["name"]: [a, o.wcs[1]["id"]]})
    assert len([x for x in o.get("/audit").json()["items"] if x["action"] == "distribute"]) == n == 1
    # пустой список — операция снова не распределена
    assert _put(o, new["id"], {ops[0]["name"]: []}).json()["distributed"] == 0


def test_foreman_sees_only_own_operations_and_orders(full_org):
    o = full_org
    o.replan_all()
    new = _new_order(o, ext="F-1")
    ops = [i["name"] for i in _ops(o, new["id"])["items"]]
    a, b = o.wcs[0]["id"], o.wcs[1]["id"]            # бригадир — участок A
    _put(o, new["id"], {ops[0]: [a], ops[1]: [b]})
    mine = _ops(o, new["id"], "foreman")
    assert [i["name"] for i in mine["items"]] == [ops[0]] and mine["centers"] == [{"id": a, "name": o.wcs[0]["name"]}]
    assert all(i["work_center_ids"] == [a] for i in mine["items"])
    assert len(_ops(o, new["id"], "owner")["items"]) == len(ops)
    # позиция, у которой операций участка A нет, бригадиру в статусе не видна
    only_b = _new_order(o, ext="F-2")
    _put(o, only_b["id"], {n["name"]: [b] for n in _ops(o, only_b["id"])["items"]})
    o.replan_all()
    ids = {r["order_line_id"] for r in o.get("/reports/status", "foreman").json()["rows"]}
    assert new["id"] in ids and only_b["id"] not in ids
    assert only_b["id"] in {r["order_line_id"] for r in o.get("/reports/status", "owner").json()["rows"]}


def test_routing_change_resets_distribution(full_org):
    o = full_org
    prod = o.get("/products").json()["items"][0]
    new = _new_order(o)
    _put(o, new["id"], {i["name"]: [o.wcs[0]["id"]] for i in _ops(o, new["id"])["items"]})
    assert _ops(o, new["id"])["distributed"] == _ops(o, new["id"])["total"]
    v2 = o.post(f"/products/{prod['id']}/routings", params={"name": "Новая"}).json()
    o.patch(f"/order-lines/{new['id']}", json={"routing_id": v2["id"], "version": new["version"]})
    v = _ops(o, new["id"])
    assert v["routing_id"] == v2["id"] and v["distributed"] == 0


def _mini_org(env, centers=("Цех",), workers=(("Иван", 0), ("Пётр", 0))):
    """Пустая организация: участки, работники (индекс участка или None), изделие из двух операций, проект."""
    from conftest import Org
    o = Org(env)
    wc = [o.post("/work-centers", json={"name": n}).json()["id"] for n in centers]
    for name, idx in workers:
        o.post("/workers", json={"name": name, "perf": 1, "skill": 1, "work_center_id": None if idx is None else wc[idx]})
    prod = o.post("/products", json={"name": "Стол"}).json()["id"]
    rid = o.get(f"/products/{prod}/routings").json()[0]["id"]
    for k, n in enumerate(("Рама", "Сборка")):
        assert o.post(f"/routings/{rid}/operations", json={"name": n, "norm_per_day": 4, "min_skill": 1, "sequence": k + 1}).status_code == 201
    prj = o.post("/projects", json={"name": "П1"}).json()["id"]
    o.wc, o.prod, o.prj = wc, prod, prj
    return o


def _order(o, ext):
    return o.post("/order-lines", json={"project_id": o.prj, "product_id": o.prod, "qty": 6, "ext_id": ext, "priority": 1, "due_date": "2099-01-01"}).json()


def test_single_section_org_assigns_automatically(env):
    o = _mini_org(env)
    new = _order(o, "A-1")
    v = _ops(o, new["id"])
    assert v["total"] == 2 and v["distributed"] == 2 and all(i["work_center_ids"] == o.wc for i in v["items"])
    assert o.post("/plans/replan", json={}).status_code == 201
    assert _plan_items(o, new["id"])
    assert not [w for w in o.get("/plans/active").json()["plan"]["warnings"] if w["code"] == "NOT_DISTRIBUTED"]


def test_org_without_sections_has_no_restriction(env):
    o = _mini_org(env, centers=(), workers=(("Иван", None), ("Пётр", None)))
    new = _order(o, "B-1")
    assert _ops(o, new["id"])["distributed"] == 0 and _ops(o, new["id"])["centers"] == []
    assert o.post("/plans/replan", json={}).status_code == 201
    assert _plan_items(o, new["id"])                         # участков нет — функция не используется, считаем как раньше
    codes = {w["code"] for w in o.get("/plans/active").json()["plan"]["warnings"]}
    assert not codes & {"NOT_DISTRIBUTED", "WORKER_NO_SECTION"}


def test_worker_without_section_is_reported_and_not_planned(env):
    o = _mini_org(env, centers=("А", "Б"), workers=(("Иван", 0), ("Пётр", 1), ("Без участка", None)))
    new = _order(o, "C-1")
    _put(o, new["id"], {"Рама": o.wc, "Сборка": o.wc})
    assert o.post("/plans/replan", json={}).status_code == 201
    plan = o.get("/plans/active").json()["plan"]
    stray = next(w for w in plan["warnings"] if w["code"] == "WORKER_NO_SECTION")
    assert "Без участка" in stray["text"]
    free = next(w["id"] for w in o.get("/workers").json()["items"] if w["name"] == "Без участка")
    assert free not in {i[1] for i in _plan_items(o, new["id"])}
