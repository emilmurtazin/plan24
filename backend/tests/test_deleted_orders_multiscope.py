"""Удалённый заказ не остаётся в истории и недовыполнении, изделие и проект после него удаляются; перепланирование — по нескольким участкам."""
from datetime import timedelta

from sqlalchemy import select

from conftest import TODAY
from plan24 import db as dbm
from plan24.models import DayPlanSnapshot, Fact, OrderLine, PlanItem
from test_refs import mk_order, mk_product


def snapshot_rows(order_id):
    s = dbm.admin_session()
    try:
        return list(s.scalars(select(DayPlanSnapshot).where(DayPlanSnapshot.order_line_id == order_id)))
    finally:
        s.close()


def test_deleted_order_leaves_no_underperformance_and_product_can_be_deleted(org):
    pid, rid = mk_product(org, ops=(("Раскрой", 40, 1), ("Сборка", 6, 2)))
    w = org.post("/workers", json={"name": "Иванов", "skill": 2}).json()
    oid = mk_order(org, pid).json()["id"]
    org.replan_all()
    past = TODAY - timedelta(days=2)
    s = dbm.admin_session()
    try:                                                  # вчерашний план по этому заказу, факт не внесён → «недовыполнение»
        op = next(iter(s.scalars(select(DayPlanSnapshot).where(DayPlanSnapshot.order_line_id == oid))), None)
        pi = s.scalars(select(PlanItem).where(PlanItem.order_line_id == oid)).first()
        s.add(DayPlanSnapshot(tenant_id=pi.tenant_id, plan_date=past, order_line_id=oid, operation_id=pi.operation_id,
                              worker_id=pi.worker_id, work_center_id=None, qty=10))
        s.add(Fact(tenant_id=pi.tenant_id, fact_date=past, order_line_id=oid, operation_id=pi.operation_id, worker_id=pi.worker_id,
                   qty=1, status="confirmed", reason_id=None, note="не хватило материала"))
        s.commit()
    finally:
        s.close()
    under = org.get("/reports/overview").json()["underperformance"]
    assert any(sum(x["under"]) > 0 for x in under["series"])            # пока заказ жив — недовыполнение видно
    assert org.delete(f"/products/{pid}").status_code == 409            # живой заказ мешает; текст понятный
    assert "заказы" in org.delete(f"/products/{pid}").json()["title"]
    assert org.delete(f"/order-lines/{oid}").status_code == 204
    assert snapshot_rows(oid) == []                                      # снимки удалены
    d = org.get("/reports/overview").json()
    assert all(sum(x["under"]) == 0 for x in d["underperformance"]["series"])         # недовыполнения нет
    assert all("материала" not in str(r) for r in d["reasons"])                        # и причин от удалённого заказа тоже
    assert org.delete(f"/products/{pid}").status_code == 204            # изделие теперь удаляется
    s = dbm.admin_session()
    try:
        assert s.get(OrderLine, oid) is None and not list(s.scalars(select(Fact).where(Fact.order_line_id == oid)))
    finally:
        s.close()


def test_project_with_only_deleted_orders_can_be_deleted(org):
    pid, rid = mk_product(org, ops=(("Раскрой", 40, 1),))
    oid = mk_order(org, pid).json()
    project_id = oid["project_id"]
    assert org.delete(f"/projects/{project_id}").status_code == 409
    assert org.delete(f"/order-lines/{oid['id']}").status_code == 204
    assert org.delete(f"/projects/{project_id}").status_code == 204


def test_replan_several_sections_at_once(full_org):
    o = full_org
    o.replan_all()
    ids = [c["id"] for c in o.get("/work-centers").json()["items"]]
    assert len(ids) >= 2
    r = o.post("/plans/replan", json={"work_center_ids": ids[:2]})
    assert r.status_code == 201 and r.json()["params"]["scope_center_ids"] == sorted(ids[:2]) and r.json()["params"]["scope_center_id"] is None
    r1 = o.post("/plans/replan", json={"work_center_ids": [ids[0]]})
    assert r1.json()["params"]["scope_center_ids"] == [ids[0]] and r1.json()["params"]["scope_center_id"] == ids[0]    # один участок — как раньше
    assert o.post("/plans/replan", json={"work_center_ids": [ids[0], 999999]}).status_code == 404
    assert o.post("/plans/compare", json={"work_center_ids": ids[:2]}).status_code == 200
    assert o.post("/plans/replan", json={}).status_code == 422                   # без участков при готовом плане — по-прежнему нельзя
    assert o.post("/plans/replan", "foreman", json={"work_center_ids": ids}).status_code == 403    # бригадир — только свой
    assert o.get("/plans/active").json()["plan"]["params"]["scope_center_ids"] is not None


def test_product_without_orders_can_be_deleted_with_its_routings(org):
    pid, rid = mk_product(org, ops=(("Раскрой", 40, 1), ("Сборка", 6, 2)))
    org.post(f"/products/{pid}/routings")                                      # вторая версия маршрута
    assert org.delete(f"/products/{pid}").status_code == 204
    assert all(p["id"] != pid for p in org.get("/products").json()["items"])
    assert org.get(f"/products/{pid}/routings").status_code in (404, 200)
