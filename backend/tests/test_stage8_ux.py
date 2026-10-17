"""Доработки 3: нагрузка работника не больше 100% дня, причины отклонений у каждого участка свои,
в сводке заказов — % выполнения и % простоя (план − факт)."""
from datetime import timedelta

from conftest import TODAY
from test_facts import body


# ------------------------------------------------------------------ не больше 100% дня
def test_planner_never_loads_worker_over_100_percent(full_org):
    o = full_org
    o.replan_all()
    L = o.get("/reports/load").json()
    over = [(n, d, v) for n, days in L["plan"].items() for d, v in days.items() if v > 1.0 + 1e-6]
    assert not over, f"перегруз больше 100% дня: {over[:5]}"
    assert any(v > 0 for days in L["plan"].values() for v in days.values())


def test_replan_after_todays_fact_still_never_exceeds_100_percent(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0, "today_load_threshold": 1.0})
    o.replan_all()
    today = TODAY.isoformat()
    w = next(x for x in o.get("/reports/day", params={"day": today}).json()["workers"] if any(t["plan_qty"] for t in x["tasks"]))
    t = next(t for t in w["tasks"] if t["plan_qty"] >= 2)
    base = {"fact_date": today, "order_line_id": t["order_line_id"], "operation_id": t["operation_id"], "worker_id": w["worker_id"]}
    assert o.post("/facts", json={**base, "qty": t["plan_qty"]}).status_code == 200
    o.replan_all()
    L = o.get("/reports/load").json()
    assert all(v <= 1.0 + 1e-6 for days in L["plan"].values() for v in days.values())


# ------------------------------------------------------------------ причины по участкам
def test_reasons_are_per_work_center(full_org):
    o = full_org
    c1, c2 = o.wcs[0]["id"], o.wcs[1]["id"]
    a = o.post("/fact-reasons", json={"name": "Нет заготовок", "work_center_id": c1})
    assert a.status_code == 201 and a.json()["work_center_id"] == c1
    assert o.post("/fact-reasons", json={"name": "Нет заготовок", "work_center_id": c2}).status_code == 201     # то же имя у другого участка
    assert o.post("/fact-reasons", json={"name": "нет заготовок", "work_center_id": c1}).status_code == 409
    assert o.post("/fact-reasons", json={"name": "Нет заготовок"}).status_code == 201                              # общая
    assert o.post("/fact-reasons", json={"name": "Нет заготовок"}).status_code == 409
    assert o.post("/fact-reasons", json={"name": "Х", "work_center_id": 999999}).status_code == 404
    rid = a.json()["id"]
    p = o.patch(f"/fact-reasons/{rid}", json={"name": "Нет заготовок A", "version": a.json()["version"]})
    assert p.status_code == 200 and p.json()["work_center_id"] == c1 and p.json()["name"] == "Нет заготовок A"
    assert o.patch(f"/fact-reasons/{rid}", json={"work_center_id": None, "version": p.json()["version"]}).json()["work_center_id"] is None
    assert o.post("/fact-reasons", "foreman", json={"name": "Свой"}).status_code == 403                             # правит руководство


def test_fact_accepts_only_common_or_own_center_reason(full_org):
    o = full_org
    w = o.w1
    own = o.post("/fact-reasons", json={"name": "Своя", "work_center_id": w["work_center_id"]}).json()["id"]
    other_c = next(c["id"] for c in o.wcs if c["id"] != w["work_center_id"])
    foreign = o.post("/fact-reasons", json={"name": "Чужая", "work_center_id": other_c}).json()["id"]
    common = o.post("/fact-reasons", json={"name": "Общая"}).json()["id"]
    b = body(o, qty=0)
    assert o.post("/facts", json={**b, "reason_id": foreign}).status_code == 422
    assert o.post("/facts", json={**b, "reason_id": own}).status_code == 200
    assert o.post("/facts", json={**b, "reason_id": common}).status_code == 200


def test_reason_unique_name_survives_migration_shape(org):
    # причина без участка и причины участков — разные записи с одним именем
    c = org.post("/work-centers", json={"name": "Сборка"}).json()["id"]
    assert org.post("/fact-reasons", json={"name": "Поломка"}).status_code == 201
    assert org.post("/fact-reasons", json={"name": "Поломка", "work_center_id": c}).status_code == 201
    names = [(r["name"], r["work_center_id"]) for r in org.get("/fact-reasons").json()["items"] if r["name"] == "Поломка"]
    assert len(names) == 2


# ------------------------------------------------------------------ сводка заказов
def test_status_rows_have_progress_and_idle_percent(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    rows = o.get("/reports/status").json()["rows"]
    assert rows and all("idle_pct" in r and "progress" in r for r in rows)
    assert all(r["idle_pct"] is None for r in rows)                        # прошлых дней в плане ещё нет


def _db(fn):
    from plan24 import db as dbm
    s = dbm.admin_session()
    try:
        r = fn(s)
        s.commit()
        return r
    finally:
        s.close()


def test_idle_percent_is_plan_minus_fact_of_past_days(full_org):
    from plan24.models import DayPlanSnapshot
    o = full_org
    o.replan_all()
    oid = o.get("/reports/status").json()["rows"][0]["order_line_id"]
    op = o.get(f"/order-lines/{oid}/operations").json()["items"][0]
    tid = o.data["user"]["tenant"]["id"]
    day = TODAY - timedelta(days=1)
    norm = op["norm_per_day"]
    _db(lambda s: s.add(DayPlanSnapshot(tenant_id=tid, plan_date=day, order_line_id=oid, operation_id=op["operation_id"],
                                        worker_id=o.w1["id"], work_center_id=o.w1["work_center_id"], qty=4)))
    pct = lambda: {r["order_line_id"]: r["idle_pct"] for r in o.get("/reports/status").json()["rows"]}[oid]
    assert pct() == 100.0                                                  # план был, факта нет — весь план «простоял»
    base = {"fact_date": day.isoformat(), "order_line_id": oid, "operation_id": op["operation_id"], "worker_id": o.w1["id"]}
    assert o.post("/facts", json={**base, "qty": 1, "note": "часть"}).status_code == 200
    assert pct() == 75.0                                                   # (4 − 1) / 4
    assert o.post("/facts", json={**base, "qty": 9, "note": "сверх плана"}).status_code == 200
    assert pct() == 0.0                                                    # перевыполнение простоем не считается
    assert norm > 0
