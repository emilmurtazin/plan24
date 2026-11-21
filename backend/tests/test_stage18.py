"""Этап 18: дашборд — заказы и трудодни; норма и «на изделие» в действующей версии."""
from datetime import timedelta

from conftest import TODAY
from test_telegram import tg  # noqa: F401  (фикстура)


def _wd(o):
    return o.get("/reports/overview").json()["orders_workdays"]


def test_dashboard_orders_workdays(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    w = _wd(o)
    assert w["count"] > 0
    for r in w["items"]:
        assert abs(r["total_days"] - r["done_days"] - r["left_days"]) < 0.11
        assert abs(r["done_pct"] + r["left_pct"] - 100) < 0.11
        assert r["finished"] or r["plan_end"]                       # дата завершения: по плану или прогноз
    assert abs(w["total_days"] - sum(r["total_days"] for r in w["items"])) < 0.5
    first = w["items"][0]
    oid = first["order_line_id"]
    ops = o.get("/reports/order-status", params={"order_line_id": oid}).json()["operations"]
    r = o.post("/facts", json={"fact_date": TODAY.isoformat(), "order_line_id": oid, "operation_id": ops[0]["operation_id"],
                               "worker_id": o.w1["id"], "qty": ops[0]["required"], "note": "тест"})
    assert r.status_code == 200, r.text
    after = next(x for x in _wd(o)["items"] if x["order_line_id"] == oid)
    assert after["done_days"] > first["done_days"] and after["left_days"] < first["left_days"]


def test_norm_change_in_used_version_makes_plan_stale_and_changes_workdays(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    before = _wd(o)["total_days"]
    ops = o.get("/reports/order-status", params={"order_line_id": _wd(o)["items"][0]["order_line_id"]}).json()["operations"]
    op = ops[0]
    cur = op["norm_per_day"]
    r = o.patch(f"/operations/{op['operation_id']}", json={"norm_per_day": cur * 2})
    assert r.status_code == 200 and r.json()["orders_affected"] >= 1
    o.replan_all()
    assert _wd(o)["total_days"] < before                            # норма выше — трудоёмкость в днях ниже


# ------------------------------------------------------------------ карта потока: карточка операции
def test_flow_map_card_has_facts_assignees_and_workers(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    lines = o.get("/flow-map/targets").json()["lines"]
    oid = lines[0]["id"]
    m = o.get("/flow-map", params={"order_line_id": oid}).json()
    assert m["can_fact"] and m["can_assign"] and m["today"] and m["workers"] and m["sections"]
    n = m["nodes"][0]
    assert {"center_ids", "assignees", "assign_strict", "facts"} <= set(n)
    # факт на заказ за прошедший день из карточки
    y = (TODAY - timedelta(days=3)).isoformat()
    wc = n["center_ids"][0] if n["center_ids"] else None
    r = o.post("/facts/order", json={"fact_date": y, "order_line_id": oid, "operation_id": n["id"], "qty": 1, "work_center_id": wc})
    assert r.status_code == 200, r.text
    n2 = next(x for x in o.get("/flow-map", params={"order_line_id": oid}).json()["nodes"] if x["id"] == n["id"])
    assert any(f["date"] == y and f["qty"] == 1 for f in n2["facts"])
    # назначение исполнителей
    ok = [w for w in m["workers"] if w["skill"] >= n["min_skill"] and (not n["center_ids"] or w["work_center_id"] in n["center_ids"])]
    assert ok
    r = o.put("/plan-grid/executors", json={"order_line_id": oid, "operation_id": n["id"], "worker_ids": [ok[0]["id"]], "strict": False})
    assert r.status_code == 200, r.text
    n3 = next(x for x in o.get("/flow-map", params={"order_line_id": oid}).json()["nodes"] if x["id"] == n["id"])
    assert n3["assignees"] == [ok[0]["id"]] and n3["assign_strict"] is False


# ------------------------------------------------------------------ Telegram: факт сборщика за другие дни
def test_tgapp_worker_backfills_past_day_even_without_plan(full_org, tg):
    from test_tgapp import open_app
    from test_telegram_ux import team
    o = full_org
    fchat, wchat, w, me = team(o)
    wa = open_app(o, wchat.chat_id)
    t = next(t for t in w["tasks"] if t["plan_qty"] >= 1)
    y = (TODAY - timedelta(days=5)).isoformat()
    r = wa.get(f"/day?date={y}")
    assert r.status_code == 200 and r.json()["date"] == y and "missing" in r.json()
    assert wa.get(f"/day?date={(TODAY + timedelta(days=1)).isoformat()}").status_code == 422
    body = {"order_line_id": t["order_line_id"], "operation_id": t["operation_id"], "qty": 3, "date": y}
    r = wa.post("/fact/other", body)
    assert r.status_code == 200, r.text
    assert wa.post("/fact/other", {**body, "date": (TODAY + timedelta(days=1)).isoformat()}).status_code == 422
    day = wa.get(f"/day?date={y}").json()["workers"][0]["tasks"]
    assert any(x["fact_qty"] == 3 for x in day)


def test_bot_day_picker_opens_past_day_and_other_work_goes_to_that_day(full_org, tg):
    from test_telegram import buttons
    from test_telegram_ux import team
    o = full_org
    fchat, wchat, w, me = team(o)
    msgs = wchat.say("/day")
    btns = buttons(msgs)
    y = (TODAY - timedelta(days=2)).isoformat()
    assert any(b["callback_data"] == f"dy:{y}" for b in btns) and any(b["callback_data"] == "dy:0" for b in btns)
    msgs = wchat.press(f"dy:{y}")
    texts = " ".join(m["text"] for m in msgs)
    assert "прошедший день" in texts
    assert any(b["callback_data"] == "ow:0" for b in buttons(msgs))                 # «Другая работа» доступна и в день без плана
    assert any(b["callback_data"] == "dy:0" for b in buttons(msgs))
    # будущий день не открывается: экран остаётся прежним (прошедший день)
    fut = (TODAY + timedelta(days=2)).isoformat()
    after = wchat.press(f"dy:{fut}")
    assert not any("прошедший день" not in m["text"] and "Задания на" in m["text"] for m in after) or after == []
    # возврат в сегодня
    msgs = wchat.press("dy:0")
    assert "прошедший день" not in " ".join(m["text"] for m in msgs)
