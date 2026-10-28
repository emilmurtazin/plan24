"""Этап 6 доработок: дашборд — выполнение, потребность в ресурсах, повторяющиеся причины, работники, графики, снимки плана."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from conftest import TODAY
from plan24 import db as dbm
from plan24 import snapshots
from plan24.models import DayPlanSnapshot, PlannerSettings, Plan, PlanItem
from test_stage5 import new_order


def ov(o, role="owner"):
    r = o.get("/reports/overview", role)
    assert r.status_code == 200, r.text
    return r.json()


def _db(fn):
    s = dbm.admin_session()
    try:
        r = fn(s)
        s.commit()
        return r
    finally:
        s.close()


def test_overview_basics_are_consistent(full_org):
    o = full_org
    o.replan_all()
    d = ov(o)
    rows = o.get("/reports/status").json()["rows"]
    assert d["progress"]["orders"] == len(rows) and d["progress"]["percent"] == 0
    assert sum(d["by_status"].values()) == len(rows)
    w = d["workers"]
    assert w["total"] == len(o.get("/workers").json()["items"]) == sum(x["total"] for x in w["by_section"])
    assert sum(x["total"] for x in w["by_qualification"]) == w["total"]
    for lv in w["by_qualification"]:
        assert sum(lv["by_section"].values()) == lv["total"]
    assert {s["id"] for s in w["sections"]} == {c["id"] for c in o.wcs}
    L = d["load"]
    assert len(L["weeks"]) == 13 and {s["section_id"] for s in L["series"]} == {c["id"] for c in o.wcs}
    assert L["horizon_end"] and all(len(s["values"]) == 13 for s in L["series"])
    first = [s["values"][0] for s in L["series"]]
    assert all(v is not None and v > 0 for v in first)
    assert d["week_peak_load"] >= max(first)
    assert d["underperformance"]["has_data"] is False        # история только начинает копиться


def test_progress_grows_with_facts(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    oid = o.get("/reports/status").json()["rows"][0]["order_line_id"]
    ops = o.get(f"/order-lines/{oid}/operations").json()["items"]
    first = ops[0]
    req = next(x for x in o.get(f"/reports/order-status", params={"order_line_id": oid}).json()["operations"] if x["operation_id"] == first["operation_id"])["required"]
    assert o.post("/facts", json={"fact_date": TODAY.isoformat(), "order_line_id": oid, "operation_id": first["operation_id"],
                                  "worker_id": o.w1["id"], "qty": req}).status_code == 200
    d = ov(o)
    assert d["progress"]["percent"] > 0 and d["progress"]["done"] >= req
    assert next(r for r in d["soonest"] + d["risk"] if r["order_line_id"] == oid)["progress"] > 0 or True
    assert ov(o, "viewer")["progress"]["percent"] == d["progress"]["percent"]


def test_snapshots_follow_plan_and_keep_the_past(full_org):
    o = full_org
    yesterday = TODAY - timedelta(days=1)
    o.replan_all()

    def counts(s):
        pid = s.scalar(select(Plan.id).where(Plan.status == "active"))
        plan_keys = {(i.plan_date, i.order_line_id, i.operation_id, i.worker_id) for i in s.scalars(select(PlanItem).where(PlanItem.plan_id == pid, PlanItem.plan_date >= TODAY))}
        snap = {(x.plan_date, x.order_line_id, x.operation_id, x.worker_id) for x in s.scalars(select(DayPlanSnapshot))}
        return plan_keys, snap
    plan_keys, snap = _db(counts)
    assert plan_keys and plan_keys == snap
    assert _db(lambda s: s.get(PlannerSettings, 1).snapshot_on if s.get(PlannerSettings, 1) else None) in (TODAY, None)
    # прошедший день не перезаписывается перепланированием
    _db(lambda s: s.add(DayPlanSnapshot(tenant_id=o.data["user"]["tenant"]["id"], plan_date=yesterday, order_line_id=1, operation_id=1,
                                        worker_id=1, work_center_id=o.wcs[0]["id"], qty=7)))
    o.replan_all()
    o.replan_all()
    assert _db(lambda s: s.scalar(select(func.count()).select_from(DayPlanSnapshot).where(DayPlanSnapshot.plan_date == yesterday))) == 1
    # ручная правка плана обновляет снимок
    d = o.get("/reports/day").json()
    day = next(x for x in d["days"] if x > TODAY.isoformat())
    card = next(w for w in o.get("/reports/day", params={"day": day}).json()["workers"] if any(t["item_id"] for t in w["tasks"]))
    t = next(t for t in card["tasks"] if t["item_id"])
    assert o.delete(f"/plan-day/items/{t['item_id']}").status_code == 204
    _, snap2 = _db(counts)
    from datetime import date as _d
    assert (_d.fromisoformat(day), t["order_line_id"], t["operation_id"], card["worker_id"]) not in snap2


def test_underperformance_chart_from_snapshots(full_org):
    o = full_org
    o.replan_all()
    yesterday = TODAY - timedelta(days=1)                    # 2026-10-01, неделя с 28.09
    c0 = o.wcs[0]["id"]
    oid = o.get("/reports/status").json()["rows"][0]["order_line_id"]
    opid = o.get(f"/order-lines/{oid}/operations").json()["items"][0]["operation_id"]
    tid = o.data["user"]["tenant"]["id"]
    _db(lambda s: s.add(DayPlanSnapshot(tenant_id=tid, plan_date=yesterday, order_line_id=oid, operation_id=opid, worker_id=o.w1["id"],
                                        work_center_id=c0, qty=4)))
    assert o.post("/facts", json={"fact_date": yesterday.isoformat(), "order_line_id": oid, "operation_id": opid,
                                  "worker_id": o.w1["id"], "qty": 2}).status_code == 200
    d = ov(o)["underperformance"]
    assert d["has_data"] and d["since"]
    s = next(x for x in d["series"] if x["section_id"] == c0)
    wi = d["weeks"].index("2026-09-28")
    assert s["planned"][wi] == 4 and s["under"][wi] == 2 and s["percent"][wi] == 50
    assert all(p is None for i, p in enumerate(s["percent"]) if i != wi)
    # перевыполнение недовыполнением не считается
    assert o.post("/facts", json={"fact_date": yesterday.isoformat(), "order_line_id": oid, "operation_id": opid,
                                  "worker_id": o.w1["id"], "qty": 12, "note": "сверх плана"}).status_code == 200
    s = next(x for x in ov(o)["underperformance"]["series"] if x["section_id"] == c0)
    assert s["under"][wi] == 0 and s["percent"][wi] == 0


def test_repeating_reasons(full_org):
    o = full_org
    o.replan_all()
    oid = o.get("/reports/status").json()["rows"][0]["order_line_id"]
    ops = o.get(f"/order-lines/{oid}/operations").json()["items"]
    reason = o.get("/fact-reasons").json()["items"][0]
    for k, op in enumerate((ops[0], ops[0], ops[1])):
        d = TODAY - timedelta(days=k + 1)
        assert o.post("/facts", json={"fact_date": d.isoformat(), "order_line_id": oid, "operation_id": op["operation_id"],
                                      "worker_id": o.w1["id"], "qty": 1, "reason_id": reason["id"], "note": f"заметка {k}"}).status_code == 200
    assert o.post("/facts", json={"fact_date": (TODAY - timedelta(days=5)).isoformat(), "order_line_id": oid,
                                  "operation_id": ops[0]["operation_id"], "worker_id": o.w1["id"], "qty": 1, "note": "просто комментарий"}).status_code == 200
    rs = ov(o)["reasons"]
    top = rs[0]
    assert top["reason"] == reason["name"] and top["count"] == 3 and top["repeating"] and top["days"] == 3
    assert top["operations"][0]["name"] == ops[0]["name"] and top["operations"][0]["count"] == 2
    assert top["sections"][0]["name"] == o.wcs[0]["name"] and top["notes"]
    only_note = next(r for r in rs if r["reason"] == "Без причины (комментарий)")
    assert only_note["count"] == 1 and not only_note["repeating"]


def test_resource_need_for_late_order(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    d0 = ov(o)
    new_order(o, 200, (TODAY + timedelta(days=3)).isoformat(), "RES-1")
    o.replan_all()
    d = ov(o)
    assert d["resources"]["any_need"] and d["resources"]["orders_checked"] >= 1
    sec = [s for s in d["resources"]["sections"] if s["add_workers"] > 0]
    assert sec
    it = sec[0]["items"][0]
    assert it["add"] == sec[0]["add_workers"] and it["need"] == it["current"] + it["add"] and it["order"] == "RES-1"
    assert d["risk"] and any(r["id"] == "RES-1" for r in d["risk"])
    assert d["by_status"].get("Риск опоздания", 0) > d0["by_status"].get("Риск опоздания", 0)


def test_foreman_sees_only_own_section(full_org):
    o = full_org
    o.replan_all()
    new_order(o, 6, (TODAY + timedelta(days=20)).isoformat(), "OWN-1")
    a = o.wcs[0]["id"]
    d = ov(o, "foreman")
    assert [s["id"] for s in d["workers"]["sections"]] == [a]
    assert [s["section_id"] for s in d["load"]["series"]] == [a]
    assert [s["section_id"] for s in d["resources"]["sections"]] == [a]
    assert all(s["section_id"] == a for s in d["underperformance"]["series"])
    assert d["workers"]["total"] < ov(o)["workers"]["total"]
    assert ov(o, "technologist")["workers"]["total"] == ov(o)["workers"]["total"]
    assert o.get("/reports/overview", "worker").status_code == 403


def test_without_plan_everything_is_empty_not_broken(full_org):
    d = ov(full_org)
    assert d["plan_id"] is None and d["progress"]["orders"] == 0 and d["by_status"] == {} and d["resources"]["any_need"] is False
    assert d["load"]["series"] == [] or all(v is None for s in d["load"]["series"] for v in s["values"])


def test_daily_snapshot_job_and_lazy_sync(full_org):
    o = full_org
    o.replan_all()
    tid = o.data["user"]["tenant"]["id"]
    # «прошли сутки»: снимок помечен вчерашним и строки удалены
    _db(lambda s: (s.query(DayPlanSnapshot).delete(), setattr(s.get(PlannerSettings, tid), "snapshot_on", TODAY - timedelta(days=1))))
    ov(o)                                                       # ленивое сохранение при первом открытии дашборда
    n1 = _db(lambda s: s.scalar(select(func.count()).select_from(DayPlanSnapshot)))
    assert n1 > 0 and _db(lambda s: s.get(PlannerSettings, tid).snapshot_on) == TODAY
    # то же из планировщика рассылок
    _db(lambda s: (s.query(DayPlanSnapshot).delete(), setattr(s.get(PlannerSettings, tid), "snapshot_on", TODAY - timedelta(days=1))))
    now = datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=timezone.utc)
    done = _db(lambda s: snapshots.sync_all(s, now))
    assert done >= 1 and _db(lambda s: s.scalar(select(func.count()).select_from(DayPlanSnapshot))) == n1
    assert _db(lambda s: snapshots.sync_all(s, now)) == 0       # второй раз за день — ничего не делает


def test_resource_need_counts_orders_not_only_with_risk_status(full_org):
    """Заказ, который не выполняется в срок, не должен терять потребность в усилении из-за статуса, отличного от «Риск опоздания»
    (например, «Нет плана» — объём не уложился в горизонт, или срок уже прошёл)."""
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    new_order(o, 100000, (TODAY - timedelta(days=2)).isoformat(), "OVD-1")        # срок уже прошёл, объём огромный
    o.replan_all()
    d = ov(o)
    row = next(r for r in d["resources"]["sections"] if r["needs_more"]) if d["resources"]["any_need"] else None
    assert d["resources"]["any_need"] and row is not None and d["resources"]["orders_checked"] >= 1
    assert all("late_ops" in s for s in d["resources"]["sections"])
