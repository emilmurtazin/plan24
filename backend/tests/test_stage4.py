"""Этап 4 доработок: правка «Плана на день» (день закрепляется только вручную; закреплённый переживает перепланирование),
работы вне перечня, временный работник, причина отклонения только при необходимости."""
from sqlalchemy import select

from conftest import TODAY
from plan24 import db as dbm
from plan24.models import DayPin, PlanItem, Plan


def _db(fn):
    s = dbm.admin_session()
    try:
        return fn(s)
    finally:
        s.close()


def items(o, worker_id=None, day=None):
    """Строки активного плана: (дата, позиция, операция, работник, кол-во, закреплено, причина)."""
    def q(s):
        pid = s.scalar(select(Plan.id).where(Plan.status == "active"))
        qq = select(PlanItem).where(PlanItem.plan_id == pid)
        if worker_id is not None:
            qq = qq.where(PlanItem.worker_id == worker_id)
        return [(i.plan_date.isoformat(), i.order_line_id, i.operation_id, i.worker_id, i.qty, i.is_locked, i.reason_code)
                for i in s.scalars(qq) if day is None or i.plan_date.isoformat() == day]
    return _db(q)


def pins(o):
    return _db(lambda s: sorted((p.worker_id, p.plan_date.isoformat()) for p in s.scalars(select(DayPin))))


def find_task(o, role="owner", skip_today=True):
    """Первое задание с работником: (день, карточка работника, задача)."""
    d = o.get("/reports/day", role).json()
    for day in d["days"]:
        if skip_today and day <= TODAY.isoformat():
            continue
        r = o.get("/reports/day", role, params={"day": day}).json()
        for w in r["workers"]:
            for t in w["tasks"]:
                if t["plan_qty"] and t["item_id"]:
                    return day, w, t
    raise AssertionError("в плане нет заданий")


def mate(o, w):
    """Другой работник того же участка."""
    return next(x for x in o.get("/workers").json()["items"] if x["work_center_id"] == w["work_center_id"] and x["id"] != w["worker_id"])


def add(o, day, worker_id, t, qty=3, role="owner"):
    return o.post("/plan-day/items", role, json={"plan_date": day, "worker_id": worker_id, "order_line_id": t["order_line_id"],
                                                 "operation_id": t["operation_id"], "qty": qty})


def pin(o, worker_id, day, role="owner"):
    """Явное закрепление дня работника (правки плана сами день не закрепляют)."""
    r = o.post("/plan-day/pins", role, json={"worker_id": worker_id, "plan_date": day})
    assert r.status_code == 201, r.text
    return r


def replan_center(o, center_id, role="owner"):
    r = o.post("/plans/replan", role, json={"work_center_id": center_id})
    assert r.status_code == 201, r.text
    return r


def test_manual_edit_does_not_pin_by_itself(full_org):
    o = full_org
    o.replan_all()
    day, w, t = find_task(o)
    w2 = mate(o, w)
    r = add(o, day, w2["id"], t, 3)
    assert r.status_code == 201, r.text
    assert r.json()["item"]["qty"] == 3 and r.json()["item"]["reason_code"] == "MANUAL" and not r.json()["item"]["is_locked"]
    assert (w2["id"], day) not in pins(o)
    card = next(x for x in o.get("/reports/day", params={"day": day}).json()["workers"] if x["worker_id"] == w2["id"])
    assert not card["pinned"]
    # изменение и удаление тоже не закрепляют
    o.patch(f"/plan-day/items/{r.json()['item']['id']}", json={"qty": 2})
    assert (w2["id"], day) not in pins(o)
    assert o.delete(f"/plan-day/items/{r.json()['item']['id']}").status_code == 204
    assert (w2["id"], day) not in pins(o)
    x = o.post("/extra-tasks", json={"plan_date": day, "worker_id": w2["id"], "title": "Уборка", "hours": 1})
    assert x.status_code == 201
    assert (w2["id"], day) not in pins(o)


def test_manual_task_is_pinned_and_survives_replan(full_org):
    o = full_org
    o.replan_all()
    day, w, t = find_task(o)
    w2 = mate(o, w)
    r = add(o, day, w2["id"], t, 3)
    assert r.status_code == 201, r.text
    pin(o, w2["id"], day)                                              # закрепление — только вручную
    assert (w2["id"], day) in pins(o)
    rep = o.get("/reports/day", params={"day": day}).json()
    card = next(x for x in rep["workers"] if x["worker_id"] == w2["id"])
    assert card["pinned"] and any(x["manual"] and x["locked"] and x["plan_qty"] == 3 for x in card["tasks"])
    # перепланирование участка не меняет закреплённый день: он остаётся ровно таким, каким его оставили
    before = sorted(items(o, w2["id"], day))
    assert any((i[1], i[2], i[4]) == (t["order_line_id"], t["operation_id"], 3) for i in before)
    assert all(i[5] for i in before)                                  # весь день работника закреплён, а не только добавленная строка
    replan_center(o, w["work_center_id"])
    assert sorted(items(o, w2["id"], day)) == before
    assert (w2["id"], day) in pins(o)
    # и после ещё одного перепланирования, и при пересчёте другого участка
    replan_center(o, w["work_center_id"])
    other = next(c for c in o.wcs if c["id"] != w["work_center_id"])
    replan_center(o, other["id"])
    assert sorted(items(o, w2["id"], day)) == before


def test_manual_delete_is_not_undone_by_replan(full_org):
    o = full_org
    o.replan_all()
    day, w, t = find_task(o)
    assert o.delete(f"/plan-day/items/{t['item_id']}").status_code == 204
    assert not [i for i in items(o, w["worker_id"], day) if (i[1], i[2]) == (t["order_line_id"], t["operation_id"])]
    assert (w["worker_id"], day) not in pins(o)
    pin(o, w["worker_id"], day)
    assert (w["worker_id"], day) in pins(o)
    replan_center(o, w["work_center_id"])
    assert not [i for i in items(o, w["worker_id"], day) if (i[1], i[2]) == (t["order_line_id"], t["operation_id"])]
    # день закреплён целиком: планировщик не добавил этому работнику ничего нового
    assert all(i[5] for i in items(o, w["worker_id"], day))


def test_edit_moves_task_and_pinned_days_survive(full_org):
    o = full_org
    o.replan_all()
    day, w, t = find_task(o)
    w2 = mate(o, w)
    r = o.patch(f"/plan-day/items/{t['item_id']}", json={"worker_id": w2["id"], "qty": 2})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["worker_id"] == w2["id"] and r.json()["item"]["qty"] == 2
    got = [i for i in items(o, w2["id"], day) if (i[1], i[2]) == (t["order_line_id"], t["operation_id"])]
    assert got and got[0][4] >= 2 and not got[0][5]
    assert not {(w["worker_id"], day), (w2["id"], day)} & set(pins(o))
    pin(o, w["worker_id"], day)
    pin(o, w2["id"], day)
    got = [i for i in items(o, w2["id"], day) if (i[1], i[2]) == (t["order_line_id"], t["operation_id"])]
    assert got[0][5]
    # слияние с уже существующей строкой того же ключа
    r2 = add(o, day, w2["id"], t, 1)
    assert r2.status_code == 201
    one = [i for i in items(o, w2["id"], day) if (i[1], i[2]) == (t["order_line_id"], t["operation_id"])]
    assert len(one) == 1 and one[0][4] == got[0][4] + 1
    replan_center(o, w["work_center_id"])
    assert [i for i in items(o, w2["id"], day) if (i[1], i[2]) == (t["order_line_id"], t["operation_id"])][0][4] == one[0][4]


def test_several_operations_per_worker_per_day(full_org):
    o = full_org
    o.replan_all()
    day, w, t = find_task(o)
    ops = o.get(f"/order-lines/{t['order_line_id']}/operations").json()["items"]
    mine = [i for i in ops if w["work_center_id"] in i["work_center_ids"] and i["operation_id"] != t["operation_id"]]
    assert mine, "в демо у позиции больше одной операции участка"
    r = o.post("/plan-day/items", json={"plan_date": day, "worker_id": w["worker_id"], "order_line_id": t["order_line_id"],
                                        "operation_id": mine[0]["operation_id"], "qty": 2})
    assert r.status_code == 201, r.text
    got = items(o, w["worker_id"], day)
    assert {(i[1], i[2]) for i in got} >= {(t["order_line_id"], t["operation_id"]), (t["order_line_id"], mine[0]["operation_id"])}
    replan_center(o, w["work_center_id"])
    assert {(i[1], i[2]) for i in items(o, w["worker_id"], day)} >= {(t["order_line_id"], mine[0]["operation_id"])}


def test_validation_and_permissions(full_org):
    o = full_org
    # плана нет
    assert o.post("/plan-day/items", json={"plan_date": TODAY.isoformat(), "worker_id": o.w1["id"], "order_line_id": 1,
                                           "operation_id": 1, "qty": 1}).status_code == 409
    o.replan_all()
    day, w, t = find_task(o)
    w2 = mate(o, w)
    other_center_worker = next(x for x in o.get("/workers").json()["items"] if x["work_center_id"] != w["work_center_id"])
    # прошедший день
    assert add(o, "2020-01-01", w2["id"], t).status_code == 422
    # операция другого участка / не распределена
    foreign = next((x for x in o.get("/workers").json()["items"] if x["work_center_id"] == other_center_worker["work_center_id"]), None)
    ops = o.get(f"/order-lines/{t['order_line_id']}/operations").json()["items"]
    ol = o.get(f"/order-lines/{t['order_line_id']}").json()
    o.put(f"/order-lines/{t['order_line_id']}/operations", json={"assignments": [
        {"operation_id": i["operation_id"], "work_center_ids": [w["work_center_id"]] if i["operation_id"] == t["operation_id"] else []} for i in ops]})
    bad = add(o, day, other_center_worker["id"], t)
    assert bad.status_code == 422 and "другим участкам" in bad.json()["title"]
    undist = next(i for i in ops if i["operation_id"] != t["operation_id"])
    bad2 = o.post("/plan-day/items", json={"plan_date": day, "worker_id": w2["id"], "order_line_id": t["order_line_id"],
                                           "operation_id": undist["operation_id"], "qty": 1})
    assert bad2.status_code == 422 and "не распределена" in bad2.json()["title"]
    # роли: бригадир — только свой участок; сборщик и наблюдатель — не правят
    assert add(o, day, other_center_worker["id"], t, role="foreman").status_code == 403
    assert add(o, day, w2["id"], t, role="foreman").status_code == 201
    assert add(o, day, w2["id"], t, role="worker").status_code == 403
    assert add(o, day, w2["id"], t, role="viewer").status_code == 403
    assert add(o, day, w2["id"], t, role="technologist").status_code == 403
    assert o.post("/plan-day/items", json={"plan_date": day, "worker_id": w2["id"], "order_line_id": t["order_line_id"],
                                           "operation_id": t["operation_id"], "qty": 0}).status_code == 422
    # предупреждение о квалификации не блокирует
    assert isinstance(add(o, day, w2["id"], t).json()["warnings"], list)
    # чужая строка плана
    it = items(o, w2["id"], day)[0]
    assert o.delete("/plan-day/items/999999").status_code == 404


def test_unpin_returns_day_to_planner_and_blocked_by_extras(full_org):
    o = full_org
    o.replan_all()
    day, w, t = find_task(o)
    w2 = mate(o, w)
    add(o, day, w2["id"], t, 3)
    x = o.post("/extra-tasks", json={"plan_date": day, "worker_id": w2["id"], "title": "Уборка участка", "hours": 1})
    assert x.status_code == 201, x.text
    assert o.delete("/plan-day/pins", params={"worker_id": w2["id"], "plan_date": day}).status_code == 409
    assert o.delete(f"/extra-tasks/{x.json()['id']}").status_code == 204
    assert o.delete("/plan-day/pins", params={"worker_id": w2["id"], "plan_date": day}).status_code == 204
    assert (w2["id"], day) not in pins(o)
    assert not any(i[5] for i in items(o, w2["id"], day))
    replan_center(o, w["work_center_id"])                # день снова считается автоматически — без ошибок


def test_extra_tasks_flow(full_org):
    o = full_org
    o.replan_all()
    day, w, t = find_task(o)
    wid = w["worker_id"]
    # по заказу и не по заказу
    a = o.post("/extra-tasks", json={"plan_date": day, "worker_id": wid, "order_line_id": t["order_line_id"],
                                     "title": "Доработка фурнитуры", "hours": 2}).json()
    b = o.post("/extra-tasks", json={"plan_date": day, "worker_id": wid, "title": "Уборка участка", "hours": 1.5}).json()
    assert a["order_line_id"] == t["order_line_id"] and b["order_line_id"] is None
    assert (wid, day) not in pins(o)
    pin(o, wid, day)
    card = next(x for x in o.get("/reports/day", params={"day": day}).json()["workers"] if x["worker_id"] == wid)
    assert {e["title"] for e in card["extras"]} == {"Доработка фурнитуры", "Уборка участка"}
    assert next(e for e in card["extras"] if e["title"] == "Доработка фурнитуры")["order"]
    # правка: отвязать от заказа, изменить часы
    p = o.patch(f"/extra-tasks/{a['id']}", json={"detach_order": True, "hours": 3})
    assert p.status_code == 200 and p.json()["order_line_id"] is None and p.json()["hours"] == 3
    # перепланирование участка не трогает закреплённый день и работы остаются
    replan_center(o, w["work_center_id"])
    assert len(next(x for x in o.get("/reports/day", params={"day": day}).json()["workers"] if x["worker_id"] == wid)["extras"]) == 2
    # факт нельзя вносить на будущую дату
    assert o.put(f"/extra-tasks/{a['id']}/fact", json={"fact_hours": 3}).status_code == 422
    assert o.post("/extra-tasks", json={"plan_date": "2020-01-01", "worker_id": wid, "title": "x", "hours": 1}).status_code == 422
    assert o.post("/extra-tasks", "worker", json={"plan_date": day, "worker_id": wid, "title": "x", "hours": 1}).status_code == 403


def test_extra_task_fact_needs_reason_only_when_less(full_org):
    o = full_org
    o.replan_all()
    w1 = o.w1
    today = TODAY.isoformat()
    x = o.post("/extra-tasks", json={"plan_date": today, "worker_id": w1["id"], "title": "Наладка станка", "hours": 2}).json()
    less = o.put(f"/extra-tasks/{x['id']}/fact", json={"fact_hours": 1})
    assert less.status_code == 422 and "причину" in less.json()["title"]
    assert o.put(f"/extra-tasks/{x['id']}/fact", json={"fact_hours": 1, "note": "станок сломался"}).status_code == 200
    ok = o.put(f"/extra-tasks/{x['id']}/fact", json={"fact_hours": 2})            # полностью — причина не нужна
    assert ok.status_code == 200 and ok.json()["fact_hours"] == 2
    assert o.put(f"/extra-tasks/{x['id']}/fact", "worker", json={"fact_hours": 2}).status_code == 200     # сборщик — свою за сегодня
    assert o.delete(f"/extra-tasks/{x['id']}").status_code == 409                 # с фактом не удаляется


def test_temp_worker_is_saved_but_not_auto_planned(full_org):
    o = full_org
    o.replan_all()
    c = o.wcs[0]["id"]
    r = o.post("/plan-day/workers", "foreman", json={"name": "Временный Пётр", "work_center_id": o.wcs[1]["id"], "skill": 1})
    assert r.status_code == 403                                                    # бригадир — только свой участок
    r = o.post("/plan-day/workers", "foreman", json={"name": "Временный Пётр", "skill": 1})
    assert r.status_code == 201, r.text
    tw = r.json()
    assert tw["is_temporary"] and tw["auto_plan"] is False and tw["work_center_id"] == c
    assert o.post("/plan-day/workers", "foreman", json={"name": "Временный Пётр"}).status_code == 409
    assert o.post("/plan-day/workers", "viewer", json={"name": "Кто-то", "work_center_id": c}).status_code == 403
    # он есть в списке работников
    lst = {x["id"]: x for x in o.get("/workers").json()["items"]}
    assert lst[tw["id"]]["is_temporary"] is True and lst[tw["id"]]["auto_plan"] is False
    # заказы автоматически ему не назначаются, сколько ни перепланируй
    for _ in range(2):
        replan_center(o, c)
    assert not items(o, tw["id"])
    # но вручную задание дать можно, и оно сохраняется
    day, w, t = find_task(o)
    r = add(o, day, tw["id"], t, 2) if w["work_center_id"] == c else None
    if r is None:
        day, w, t = next((d, ww, tt) for d in o.get("/reports/day").json()["days"] if d > TODAY.isoformat()
                         for ww in o.get("/reports/day", params={"day": d}).json()["workers"] if ww["work_center_id"] == c
                         for tt in ww["tasks"] if tt["plan_qty"] and tt["item_id"])
        r = add(o, day, tw["id"], t, 2)
    assert r.status_code == 201, r.text
    pin(o, tw["id"], day)
    replan_center(o, c)
    assert [i[4] for i in items(o, tw["id"], day)] == [2]
    # в настройках можно включить автопланирование
    cur = o.get("/workers").json()["items"]
    me = next(x for x in cur if x["id"] == tw["id"])
    assert o.patch(f"/workers/{tw['id']}", json={"auto_plan": True, "version": me["version"]}).status_code == 200


def test_reason_required_only_when_deviation(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    today = TODAY.isoformat()
    rep = o.get("/reports/day", params={"day": today}).json()
    w = next(x for x in rep["workers"] if any(t["plan_qty"] for t in x["tasks"]))
    t = next(t for t in w["tasks"] if t["plan_qty"] >= 2)
    base = {"fact_date": today, "order_line_id": t["order_line_id"], "operation_id": t["operation_id"], "worker_id": w["worker_id"]}
    # меньше плана: без причины нельзя
    r = o.post("/facts", json={**base, "qty": t["plan_qty"] - 1})
    assert r.status_code == 422 and "меньше плана" in r.json()["title"]
    reason = o.get("/fact-reasons").json()["items"][0]
    assert o.post("/facts", json={**base, "qty": t["plan_qty"] - 1, "reason_id": reason["id"]}).status_code == 200
    assert o.post("/facts", json={**base, "qty": 0, "note": "не приступали"}).status_code == 200
    # ровно по плану — причина не нужна
    assert o.post("/facts", json={**base, "qty": t["plan_qty"]}).status_code == 200
    # больше, чем нужно по заказу: просим причину
    need = o.get("/reports/day", params={"day": today}).json()
    tt = next(x for ww in need["workers"] if ww["worker_id"] == w["worker_id"] for x in ww["tasks"]
              if (x["order_line_id"], x["operation_id"]) == (t["order_line_id"], t["operation_id"]))
    over = tt["need_left"] + 1
    r = o.post("/facts", json={**base, "qty": over})
    assert r.status_code == 422 and "больше, чем нужно" in r.json()["title"]
    assert o.post("/facts", json={**base, "qty": over, "note": "запас на брак"}).status_code == 200


def test_rollback_rebuilds_pins_from_the_activated_plan(full_org):
    o = full_org
    o.replan_all()
    day, w, t = find_task(o)
    w2 = mate(o, w)
    add(o, day, w2["id"], t, 3)
    pin(o, w2["id"], day)
    plans = o.get("/plans").json()["items"]
    first = next(p for p in plans if p["status"] == "active")["id"]
    replan_center(o, w["work_center_id"])                              # план № 2 унаследовал закрепление
    assert (w2["id"], day) in pins(o)
    assert o.post(f"/plans/{first}/activate").status_code == 200       # откат на план № 1 (в нём это закреплено)
    assert (w2["id"], day) in pins(o)
    assert [i[4] for i in items(o, w2["id"], day) if (i[1], i[2]) == (t["order_line_id"], t["operation_id"])] == [3]


def test_pinned_quantity_is_not_planned_twice(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})          # без заморозки: иначе первоначальный план стоит на месте
    o.replan_all()
    day, w, t = find_task(o)
    w2 = mate(o, w)
    add(o, day, w2["id"], t, 4)
    for _ in range(2):
        replan_center(o, w["work_center_id"])
    required = next(x for ww in o.get("/reports/day", params={"day": day}).json()["workers"] for x in ww["tasks"]
                    if (x["order_line_id"], x["operation_id"]) == (t["order_line_id"], t["operation_id"]))["required"]
    total = sum(i[4] for i in items(o) if (i[1], i[2]) == (t["order_line_id"], t["operation_id"]))
    assert total <= required, (total, required)


def test_manual_overplan_gives_a_warning(full_org):
    o = full_org
    o.replan_all()
    day, w, t = find_task(o)
    r = add(o, day, mate(o, w)["id"], t, 4)               # по операции уже запланировано всё нужное количество
    assert r.status_code == 201
    assert any("больше, чем нужно" in x for x in r.json()["warnings"]), r.json()
