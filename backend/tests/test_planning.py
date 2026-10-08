"""План из БД: паритет с эталоном, снапшоты, актуальность, заморозка, влияние факта и отсутствий."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from conftest import Org, TODAY
from plan24 import db as dbm
from plan24.compat import build_input, plan_rows_script_format
from plan24.models import (Fact, Operation, OrderLine, Plan, PlanItem, Product, Project, User, Worker)
from plan24.planner import plan as run_ref
from plan24.routers.demo import seed_dataset
from plan24.synthetic import make_datasets

DATASETS = {k: v for k, v in make_datasets().items() if k not in ("edge_batches_fact", "edge_frozen_loaded")}


def session():
    return dbm.admin_session()


def db_rows(tenant_id, plan_id=None):
    """Строки активного плана в формате листа «Задания на день» (как у эталона)."""
    s = session()
    try:
        pid = plan_id or s.scalar(select(Plan.id).where(Plan.tenant_id == tenant_id, Plan.status == "active"))
        q = (select(PlanItem, OrderLine, Operation, Worker, Project, Product)
             .join(OrderLine, OrderLine.id == PlanItem.order_line_id).join(Operation, Operation.id == PlanItem.operation_id)
             .join(Worker, Worker.id == PlanItem.worker_id).join(Project, Project.id == OrderLine.project_id)
             .join(Product, Product.id == OrderLine.product_id).where(PlanItem.plan_id == pid).order_by(PlanItem.id))
        return [[it.plan_date.strftime("%d.%m.%Y"), ol.ext_id or f"#{ol.id}", pr.name, pd.name, op.name, it.qty, w.name]
                for it, ol, op, w, pr, pd in s.execute(q).all()]
    finally:
        s.close()


@pytest.mark.parametrize("name", list(DATASETS))
def test_db_plan_equals_reference_plan(env, name):
    """Данные в БД → API → тот же план, что у эталонного планировщика (а тот совпадает со скриптом, test_parity)."""
    ds, today = DATASETS[name]
    o = Org(env)
    s = session()
    seed_dataset(s, s.get(User, o.data["user"]["id"]), ds, 0)
    s.commit(); s.close()
    env.state["today"] = today
    assert o.post("/plans/replan").status_code == 201
    ref = plan_rows_script_format(run_ref(build_input(ds, today)))
    mine = db_rows(o.data["user"]["tenant"]["id"])
    assert ref, "эталонный план пуст — тест бессмысленен"
    assert len(mine) == len(ref)
    for a, b in zip(mine, ref):
        assert [str(x) for x in a] == [str(x) for x in b]


def test_replan_keeps_single_active_and_archives_previous(full_org):
    o = full_org
    p1 = o.post("/plans/replan").json()
    p2 = o.post("/plans/replan", json={"strictness": 0}).json()
    hist = o.get("/plans").json()["items"]
    assert [p["status"] for p in hist] == ["active", "archived"] and hist[0]["id"] == p2["id"]
    assert o.get("/plans/active").json()["plan"]["id"] == p2["id"]
    assert p2["params"]["strictness"] == 0 and p1["params"]["strictness"] == 100


def test_stale_flag_tracks_input_changes(full_org):
    o = full_org
    assert o.get("/plans/active").json() == {"plan": None}
    o.post("/plans/replan")
    assert o.get("/plans/active").json()["plan"]["stale"] is False
    prj, pid = o.ids("/projects")[0], o.ids("/products")[0]
    o.post("/order-lines", json={"project_id": prj, "product_id": pid, "qty": 3})
    assert o.get("/plans/active").json()["plan"]["stale"] is True            # данные изменились после расчёта
    o.post("/plans/replan")
    assert o.get("/plans/active").json()["plan"]["stale"] is False
    env_today = o.c.state
    env_today["today"] = TODAY + timedelta(days=3)                            # наступил другой день
    assert o.get("/plans/active").json()["plan"]["stale"] is True


def test_stale_with_per_run_override_is_not_permanently_stale(full_org):
    o = full_org
    o.post("/plans/replan", json={"strictness": 30})
    assert o.get("/plans/active").json()["plan"]["stale"] is False           # настройка запуска учитывается


def test_rollback_to_archived_plan(full_org):
    o = full_org
    p1 = o.post("/plans/replan", json={"strictness": 100}).json()["id"]
    day1 = o.get("/reports/day").json()
    o.post("/plans/replan", json={"strictness": 0})
    assert o.post(f"/plans/{p1}/activate").status_code == 200
    assert o.get("/plans/active").json()["plan"]["id"] == p1
    assert o.get("/reports/day").json()["workers"] == day1["workers"]
    assert sum(1 for p in o.get("/plans").json()["items"] if p["status"] == "active") == 1


def test_compare_does_not_change_anything(full_org):
    o = full_org
    o.post("/plans/replan")
    before = (o.get("/plans").json(), o.get("/settings").json(), db_rows(o.data["user"]["tenant"]["id"]))
    cmp = o.post("/plans/compare").json()
    after = (o.get("/plans").json(), o.get("/settings").json(), db_rows(o.data["user"]["tenant"]["id"]))
    assert before == after
    v = {x["label"]: x for x in cmp["variants"]}
    assert set(v) == {"Текущая настройка", "Жёстко", "Сбалансировано", "Мягко"}
    assert v["Жёстко"]["avg_shift_days"] == 0 and v["Текущая настройка"]["avg_shift_days"] == 0


def test_frozen_days_survive_replan(full_org):
    o = full_org
    o.post("/plans/replan", json={"strictness": 100})
    d0 = o.get("/reports/day").json()
    days = d0["days"]
    d1 = o.get("/reports/day", params={"day": days[1]}).json()
    assert d0["workers"] and d1["workers"]
    o.post("/plans/replan", json={"strictness": 0})                           # другой режим расчёта
    key = lambda r: {(w["worker_id"], t["order_line_id"], t["operation_id"]): t for w in r["workers"] for t in w["tasks"] if t["plan_qty"]}
    for day, before in ((days[0], d0), (days[1], d1)):
        was, now = key(before), key(o.get("/reports/day", params={"day": day}).json())
        assert was
        for k, t in was.items():                  # всё ранее назначенное на эти дни сохранено без изменений
            assert k in now and now[k]["plan_qty"] == t["plan_qty"] and now[k]["frozen"], (day, k)
        # новое в освободившуюся ёмкость добавлять можно — оно не заморожено
        assert all(not t["frozen"] for k, t in now.items() if k not in was)


def test_freeze_days_zero_disables_freezing(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.post("/plans/replan")
    o.post("/plans/replan")
    assert not any(t["frozen"] for w in o.get("/reports/day").json()["workers"] for t in w["tasks"])


def first_task(o, day=None):
    r = o.get("/reports/day", params={"day": day} if day else {}).json()
    w = next(w for w in r["workers"] if any(t["plan_qty"] for t in w["tasks"]))
    t = next(t for t in w["tasks"] if t["plan_qty"])
    return r, w, t


def total_planned(o, order_line_id, operation_id):
    s = session()
    try:
        p = s.scalar(select(Plan.id).where(Plan.status == "active"))
        return sum(i.qty for i in s.scalars(select(PlanItem).where(
            PlanItem.plan_id == p, PlanItem.order_line_id == order_line_id, PlanItem.operation_id == operation_id)))
    finally:
        s.close()


def test_fact_reduces_remaining_plan(full_org):
    o = full_org
    o.post("/plans/replan")
    _, w, t = first_task(o)
    before = total_planned(o, t["order_line_id"], t["operation_id"])
    q = t["plan_qty"]
    r = o.post("/facts", json={"fact_date": TODAY.isoformat(), "order_line_id": t["order_line_id"],
                               "operation_id": t["operation_id"], "worker_id": w["worker_id"], "qty": q})
    assert r.status_code == 200
    assert o.get("/plans/active").json()["plan"]["stale"] is True
    o.post("/plans/replan")
    assert total_planned(o, t["order_line_id"], t["operation_id"]) == before - q     # сделанное из плана вычтено
    st = {r["order_line_id"]: r for r in o.get("/reports/status").json()["rows"]}[t["order_line_id"]]
    assert st["done_ops"] >= q and st["left_ops"] == st["required_ops"] - st["done_ops"]


def test_loaded_worker_gets_no_new_tasks_today(full_org):
    """BR-12: исполнитель, уже выработавший ≥ порога дневной нормы сегодня, новых заданий на сегодня не получает."""
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.post("/plans/replan")
    _, w, t = first_task(o)
    op = next(x for r in o.get(f"/products/{o.get(f'/order-lines/{t['order_line_id']}').json()['product_id']}/routings").json()
              for x in r["operations"] if x["id"] == t["operation_id"])
    perf = next(x["perf"] for x in o.workers if x["id"] == w["worker_id"])
    big = op["norm_per_day"] * perf * 0.9                                     # 90% дневной нормы
    o.post("/facts", json={"fact_date": TODAY.isoformat(), "order_line_id": t["order_line_id"],
                           "operation_id": t["operation_id"], "worker_id": w["worker_id"], "qty": big})
    o.post("/plans/replan")
    day = o.get("/reports/day").json()
    mine = next((x for x in day["workers"] if x["worker_id"] == w["worker_id"]), None)
    assert mine is not None and all(tt["plan_qty"] == 0 for tt in mine["tasks"])
    assert w["worker"] in o.get("/plans/active").json()["plan"]["loaded_today"]


def test_absence_is_respected(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.post("/plans/replan")
    _, w, _ = first_task(o)
    o.post("/absences", json={"worker_id": w["worker_id"], "date_from": TODAY.isoformat(), "date_to": (TODAY + timedelta(days=6)).isoformat(), "reason": "отпуск"})
    o.post("/plans/replan")
    for d in o.get("/reports/day").json()["days"][:5]:
        day = o.get("/reports/day", params={"day": d}).json()
        assert all(x["worker_id"] != w["worker_id"] for x in day["workers"]), d


def test_unavailable_worker_and_cancelled_order_are_not_planned(full_org):
    o = full_org
    o.post("/plans/replan")
    _, w, t = first_task(o)
    o.patch(f"/workers/{w['worker_id']}", json={"status": "unavailable"})
    o.patch(f"/order-lines/{t['order_line_id']}", json={"status": "cancelled"})
    o.patch("/settings", json={"freeze_days": 0})
    o.post("/plans/replan")
    st = {r["order_line_id"] for r in o.get("/reports/status").json()["rows"]}
    assert t["order_line_id"] not in st
    for d in o.get("/reports/day").json()["days"][:6]:
        assert all(x["worker_id"] != w["worker_id"] for x in o.get("/reports/day", params={"day": d}).json()["workers"])


def test_access_restriction_limits_candidates(full_org):
    o = full_org
    ol = o.ids("/order-lines")[0]
    others = [w["id"] for w in o.workers]
    only = others[0]
    for w in others:                                 # все исполнители допущены только к одной позиции
        o.put(f"/workers/{w}/access", json={"order_line_ids": [ol] if w == only else [o.ids("/order-lines")[1]]})
    o.patch("/settings", json={"freeze_days": 0})
    o.post("/plans/replan")
    s = session()
    try:
        pid = s.scalar(select(Plan.id).where(Plan.status == "active"))
        workers = {i.worker_id for i in s.scalars(select(PlanItem).where(PlanItem.plan_id == pid, PlanItem.order_line_id == ol))}
    finally:
        s.close()
    assert workers <= {only}


def test_plan_warnings_visible_to_staff_not_to_worker(full_org):
    o = full_org
    o.patch("/settings", json={"max_workers_per_op": 1})
    p = o.post("/plans/replan").json()
    assert "warnings" in p
    assert "warnings" not in o.get("/plans/active", "worker").json()["plan"]
    assert "warnings" in o.get("/plans/active", "foreman").json()["plan"]
