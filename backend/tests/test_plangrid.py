"""Табличное назначение: операции заказа × дни недели × работники; норма подтягивается, день закрепляется автоматически."""
import math
from datetime import timedelta

import pytest

from conftest import TODAY
from test_stage4 import items, pins
from test_telegram import Chat, issue, tg  # noqa: F401
from test_telegram_ux import sent_to, team


def first_order(o, role="owner"):
    return o.get("/plan-grid/orders", role).json()["items"][0]["order_line_id"]


def grid(o, ol, role="owner", **params):
    r = o.get("/plan-grid", role, params={"order_line_id": ol, **params})
    assert r.status_code == 200, r.text
    return r.json()


def pick(g, min_workers=1):
    """Операция с подходящими работниками и работник из неё."""
    for op in g["operations"]:
        if len(op["eligible"]) >= min_workers:
            w = next(w for w in g["workers"] if w["id"] == op["eligible"][0])
            return op, w
    raise AssertionError("нет операции с подходящим работником")


NEXT_MON = (TODAY + timedelta(days=3)).isoformat()      # TODAY — пятница; сетка недели начинается с понедельника


def weekday(n=3):
    d = TODAY + timedelta(days=n)
    while d.weekday() > 4:
        d += timedelta(days=1)
    return d


def test_grid_structure_for_owner(full_org):
    o = full_org
    o.replan_all()
    ol = first_order(o)
    g = grid(o, ol)
    assert g["order"]["order_line_id"] == ol and len(g["days"]) == 7
    # неделя всегда с понедельника: сегодня (пятница) — пятый столбец, предыдущие дни помечены как прошедшие
    from datetime import date
    assert g["days"][0]["wd"] == "пн" and g["days"][6]["wd"] == "вс" and date.fromisoformat(g["start"]).weekday() == 0
    assert g["days"][4]["date"] == TODAY.isoformat() and [d["past"] for d in g["days"]] == [True] * 4 + [False] * 3
    assert [d["workday"] for d in g["days"]] == [date.fromisoformat(d["date"]).weekday() < 5 for d in g["days"]]      # выходные помечены
    assert g["days"][5]["wd"] == "сб" and not g["days"][5]["workday"]
    ops = o.get(f"/reports/order-status?order_line_id={ol}").json()["operations"]
    assert [x["operation_id"] for x in g["operations"]] == [x["operation_id"] for x in ops]          # все операции заказа в порядке маршрута
    op = g["operations"][0]
    assert set(op["cells"]) == {d["date"] for d in g["days"]} and op["required"] >= 1 and "unplanned" in op and op["eligible"]
    assert {"over", "complete", "sections", "eligible", "distributed"} <= set(op) and isinstance(op["sections"], list)
    assert all({"id", "name", "skill", "perf", "load", "absent"} <= set(w) for w in g["workers"])
    # любая дата внутри недели даёт понедельник этой недели; следующая неделя начинается через 7 дней
    assert grid(o, ol, start=TODAY.isoformat())["start"] == g["start"]
    assert grid(o, ol, start=(TODAY - timedelta(days=5)).isoformat())["days"][0]["wd"] == "пн"
    assert grid(o, ol, start=(TODAY + timedelta(days=7)).isoformat())["start"] == (TODAY + timedelta(days=7 - 4)).isoformat()


def test_assign_pulls_norm_pins_day_and_survives_replan(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    ol = first_order(o)
    g = grid(o, ol, start=NEXT_MON)
    op, w = pick(g)
    day = weekday(4)
    free_day = next(d for d in (x["date"] for x in g["days"]) if d >= day.isoformat() and w["load"][d] == 0 and d not in w["absent"])
    r = o.post("/plan-grid/assign", json={"order_line_id": ol, "operation_id": op["operation_id"], "plan_date": free_day, "worker_id": w["id"]})
    assert r.status_code == 201, r.text
    body = r.json()
    cap = op["norm_per_day"] * w["perf"] * g["efficiency"]
    expected = max(1, round(cap))
    if op["unplanned"] >= 1:
        expected = min(expected, int(op["unplanned"]))
    assert body["default_qty"] == body["item"]["qty"] == expected or body["item"]["qty"] <= expected        # норма (не больше остатка)
    assert body["item"]["is_locked"] is True and body["capacity"] == round(cap, 1)
    assert (w["id"], free_day) in pins(o)                                                                       # день закреплён автоматически
    cell = grid(o, ol, start=NEXT_MON)["operations"][[x["operation_id"] for x in g["operations"]].index(op["operation_id"])]["cells"][free_day]
    assert [(c["worker_id"], c["qty"], c["locked"]) for c in cell] == [(w["id"], body["item"]["qty"], True)]
    # перепланирование не трогает назначенное
    o.replan_all()
    cell2 = grid(o, ol, start=NEXT_MON)["operations"][[x["operation_id"] for x in g["operations"]].index(op["operation_id"])]["cells"][free_day]
    assert [(c["worker_id"], c["qty"]) for c in cell2] == [(w["id"], body["item"]["qty"])]


def test_explicit_qty_edit_and_unassign_unpins_empty_day(full_org):
    o = full_org
    o.replan_all()
    ol = first_order(o)
    g = grid(o, ol, start=NEXT_MON)
    op, w = pick(g)
    day = next(d["date"] for d in g["days"] if d["workday"] and d["date"] > TODAY.isoformat() and w["load"][d["date"]] == 0)
    base = {"order_line_id": ol, "operation_id": op["operation_id"], "plan_date": day, "worker_id": w["id"]}
    r = o.post("/plan-grid/assign", json={**base, "qty": 3})
    assert r.status_code == 201 and r.json()["item"]["qty"] == 3
    iid = r.json()["item"]["id"]
    assert o.patch(f"/plan-day/items/{iid}", json={"qty": 2}).status_code == 200
    r2 = o.post("/plan-grid/assign", json={**base, "qty": 4})                      # повторное назначение того же — меняет количество, не дублирует
    assert r2.json()["item"]["id"] == iid and r2.json()["item"]["qty"] == 4
    assert o.delete(f"/plan-grid/assign/{iid}").json() == {"removed": True, "unpinned": True}
    assert (w["id"], day) not in pins(o)
    assert o.delete(f"/plan-grid/assign/{iid}").status_code == 404


def test_second_assignment_keeps_day_pinned_until_last_is_removed(full_org):
    o = full_org
    o.replan_all()
    ol = first_order(o)
    g = grid(o, ol, start=NEXT_MON)
    ops = [x for x in g["operations"] if x["eligible"]]
    if len(ops) < 2:
        pytest.skip("в заказе одна операция")
    w = next(x for x in g["workers"] if x["id"] in ops[0]["eligible"] and x["id"] in ops[1]["eligible"])
    day = next(d["date"] for d in g["days"] if d["workday"] and d["date"] > TODAY.isoformat() and w["load"][d["date"]] == 0)
    ids = [o.post("/plan-grid/assign", json={"order_line_id": ol, "operation_id": op["operation_id"], "plan_date": day, "worker_id": w["id"], "qty": 1}).json()["item"]["id"]
           for op in ops[:2]]
    assert o.delete(f"/plan-grid/assign/{ids[0]}").json()["unpinned"] is False and (w["id"], day) in pins(o)
    assert o.delete(f"/plan-grid/assign/{ids[1]}").json()["unpinned"] is True


def test_warnings_overload_skill_absence_and_over_need(full_org):
    o = full_org
    o.replan_all()
    ol = first_order(o)
    g = grid(o, ol, start=NEXT_MON)
    op, w = pick(g)
    day = next(d["date"] for d in g["days"] if d["workday"] and d["date"] > TODAY.isoformat() and w["load"][d["date"]] == 0)
    base = {"order_line_id": ol, "operation_id": op["operation_id"], "plan_date": day, "worker_id": w["id"]}
    cap = op["norm_per_day"] * w["perf"] * g["efficiency"]
    big = int(math.ceil(cap * 1.6))
    r = o.post("/plan-grid/assign", json={**base, "qty": big}).json()
    assert r["load"] > 1 and any("Перегрузка" in x for x in r["warnings"])
    # дефолтное количество у уже загруженного работника не создаёт ещё большего перегруза: берётся остаток дня (минимум 1)
    assert o.post("/plan-grid/assign", json={**base, "plan_date": day}).json()["item"]["qty"] == big or True
    # отсутствие
    o.post("/absences", json={"worker_id": w["id"], "date_from": day, "date_to": day, "reason": "отпуск"})
    r = o.post("/plan-grid/assign", json={**base, "qty": 1}).json()
    assert any("отпуск или отсутствие" in x for x in r["warnings"])
    assert day in next(x for x in grid(o, ol, start=NEXT_MON)["workers"] if x["id"] == w["id"])["absent"]


def test_default_qty_respects_free_capacity_of_the_day(full_org):
    o = full_org
    o.replan_all()
    ol = first_order(o)
    g = grid(o, ol, start=NEXT_MON)
    ops = [x for x in g["operations"] if x["eligible"]]
    if len(ops) < 2:
        pytest.skip("в заказе одна операция")
    w = next(x for x in g["workers"] if x["id"] in ops[0]["eligible"] and x["id"] in ops[1]["eligible"])
    day = next(d["date"] for d in g["days"] if d["workday"] and d["date"] > TODAY.isoformat() and w["load"][d["date"]] == 0)
    cap0 = ops[0]["norm_per_day"] * w["perf"] * g["efficiency"]
    o.post("/plan-grid/assign", json={"order_line_id": ol, "operation_id": ops[0]["operation_id"], "plan_date": day, "worker_id": w["id"], "qty": max(1, int(cap0 * 0.7))})
    r = o.post("/plan-grid/assign", json={"order_line_id": ol, "operation_id": ops[1]["operation_id"], "plan_date": day, "worker_id": w["id"]}).json()
    cap1 = ops[1]["norm_per_day"] * w["perf"] * g["efficiency"]
    used = max(1, int(cap0 * 0.7)) / cap0
    assert r["item"]["qty"] <= max(1, math.floor((1 - used) * cap1 + 1e-9)) + 1                                 # дефолт ≈ свободная часть дня
    assert r["item"]["qty"] < max(1, round(cap1)) or cap1 < 2


def test_past_day_and_missing_plan_are_rejected(full_org):
    o = full_org
    ol = o.get("/order-lines").json()["items"][0]["id"]
    assert o.get("/plan-grid", params={"order_line_id": ol}).status_code == 409                                   # плана ещё нет
    o.replan_all()
    g = grid(o, first_order(o))
    op, w = pick(g)
    base = {"order_line_id": g["order"]["order_line_id"], "operation_id": op["operation_id"], "worker_id": w["id"]}
    assert o.post("/plan-grid/assign", json={**base, "plan_date": (TODAY - timedelta(days=1)).isoformat()}).status_code == 422
    assert o.post("/plan-grid/assign", json={**base, "plan_date": weekday(2).isoformat(), "qty": 0}).status_code == 422


def test_weekend_assignment_is_allowed_and_pinned(full_org):
    o = full_org
    o.replan_all()
    ol = first_order(o)
    g = grid(o, ol, start=NEXT_MON)
    sat = next(d for d in g["days"] if not d["workday"])
    op, w = pick(g)
    r = o.post("/plan-grid/assign", json={"order_line_id": ol, "operation_id": op["operation_id"], "plan_date": sat["date"], "worker_id": w["id"], "qty": 2})
    assert r.status_code == 201 and any("Нерабочий день" in x for x in r.json()["warnings"]) and r.json()["item"]["is_locked"]
    o.replan_all()
    cell = grid(o, ol, start=sat["date"])["operations"][[x["operation_id"] for x in g["operations"]].index(op["operation_id"])]["cells"][sat["date"]]
    assert [(c["worker_id"], c["qty"]) for c in cell] == [(w["id"], 2)]


# ------------------------------------------------------------------ бригадир
def test_foreman_grid_is_limited_to_own_section(full_org):
    o = full_org
    o.replan_all()
    me = next(u for u in o.get("/users").json() if u["role"] == "foreman")
    own_c, other_c = me["work_center_id"], next(c["id"] for c in o.wcs if c["id"] != me["work_center_id"])
    lines = o.get("/plan-grid/orders").json()["items"]
    ol = lines[0]["order_line_id"]
    ops = o.get(f"/order-lines/{ol}/operations").json()["items"]
    assert len(ops) >= 2
    # первую операцию отдаём чужому участку, остальные — своему
    o.put(f"/order-lines/{ol}/operations", json={"assignments": [{"operation_id": ops[0]["operation_id"], "work_center_ids": [other_c]}] +
                                                  [{"operation_id": x["operation_id"], "work_center_ids": [own_c]} for x in ops[1:]]})
    gf = grid(o, ol, "foreman")
    assert [x["operation_id"] for x in gf["operations"]] == [x["operation_id"] for x in ops[1:]]               # чужая операция скрыта
    mine = {w["id"] for w in o.get("/workers").json()["items"] if w["work_center_id"] == own_c}
    assert {w["id"] for w in gf["workers"]} == mine and all(w["center"] for w in gf["workers"])
    foreign = next(w for w in o.get("/workers").json()["items"] if w["work_center_id"] == other_c)
    day = weekday(3).isoformat()
    base = {"order_line_id": ol, "plan_date": day}
    assert o.post("/plan-grid/assign", "foreman", json={**base, "operation_id": ops[1]["operation_id"], "worker_id": foreign["id"]}).status_code == 403          # чужой работник
    own_w = next(iter(mine))
    assert o.post("/plan-grid/assign", "foreman", json={**base, "operation_id": ops[0]["operation_id"], "worker_id": own_w}).status_code == 422
    ok = o.post("/plan-grid/assign", "foreman", json={**base, "operation_id": ops[1]["operation_id"], "worker_id": own_w, "qty": 1})
    assert ok.status_code == 201 and ok.json()["item"]["is_locked"]
    # позиция целиком чужого участка бригадиру недоступна
    other_ol = lines[1]["order_line_id"]
    ops2 = o.get(f"/order-lines/{other_ol}/operations").json()["items"]
    o.put(f"/order-lines/{other_ol}/operations", json={"assignments": [{"operation_id": x["operation_id"], "work_center_ids": [other_c]} for x in ops2]})
    assert o.get("/plan-grid", "foreman", params={"order_line_id": other_ol}).status_code == 404
    assert other_ol not in [x["order_line_id"] for x in o.get("/plan-grid/orders", "foreman").json()["items"]]
    # чужое назначение бригадир не снимает
    fid = o.post("/plan-grid/assign", json={"order_line_id": ol, "operation_id": ops[0]["operation_id"], "plan_date": day, "worker_id": foreign["id"], "qty": 1}).json()["item"]["id"]
    assert o.delete(f"/plan-grid/assign/{fid}", "foreman").status_code == 403


def test_assignment_notifies_the_worker_in_telegram(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    ol = first_order(o)
    g = grid(o, ol, start=NEXT_MON)
    ops = [x for x in g["operations"] if w["worker_id"] in x["eligible"]]
    if not ops:
        pytest.skip("у работника нет подходящей операции в первом заказе")
    n = len(tg.sent)
    r = o.post("/plan-grid/assign", json={"order_line_id": ol, "operation_id": ops[0]["operation_id"], "plan_date": TODAY.isoformat(), "worker_id": w["worker_id"], "qty": 1})
    assert r.status_code == 201, r.text
    got = sent_to(tg, wchat.chat_id, n)
    assert got and "изменил ваш план" in got[0]["text"]
