"""Доработки 4: факт за прошлые дни (где не внесён), работа в нерабочие дни, доступ бригадира к работникам своего участка."""
from datetime import timedelta

from conftest import TODAY


def _db(fn):
    from plan24 import db as dbm
    s = dbm.admin_session()
    try:
        r = fn(s)
        s.commit()
        return r
    finally:
        s.close()


def _first_task(o):
    """(order_line_id, operation_id, worker) из сегодняшнего плана."""
    rep = o.get("/reports/day", params={"day": TODAY.isoformat()}).json()
    for w in rep["workers"]:
        for t in w["tasks"]:
            if t["plan_qty"] >= 2:
                return t["order_line_id"], t["operation_id"], w
    raise AssertionError("в плане на сегодня нет заданий")


def _snapshot(o, day, ol, op, w, qty):
    from plan24.models import DayPlanSnapshot
    tid = o.data["user"]["tenant"]["id"]
    _db(lambda s: s.add(DayPlanSnapshot(tenant_id=tid, plan_date=day, order_line_id=ol, operation_id=op, worker_id=w["worker_id"],
                                        work_center_id=w["work_center_id"], qty=qty)))


# ------------------------------------------------------------------ факт за прошлые дни
def test_past_day_shows_what_was_planned_and_accepts_missing_fact(full_org):
    o = full_org
    o.replan_all()
    ol, op, w = _first_task(o)
    day = TODAY - timedelta(days=2)
    _snapshot(o, day, ol, op, w, 5)
    rep = o.get("/reports/day", params={"day": day.isoformat()}).json()
    card = next(x for x in rep["workers"] if x["worker_id"] == w["worker_id"])
    t = next(t for t in card["tasks"] if (t["order_line_id"], t["operation_id"]) == (ol, op))
    assert t["plan_qty"] == 5 and t["fact_qty"] is None and t["state"] == "none"
    assert rep["can_enter"] is True and rep["can_edit"] is False
    # день числится как «факт не внесён»
    miss = {m["date"]: m for m in rep["missing"]}
    assert miss[day.isoformat()]["count"] >= 1
    # вносим факт за прошлый день — день выпадает из списка
    r = o.post("/facts", json={"fact_date": day.isoformat(), "order_line_id": ol, "operation_id": op, "worker_id": w["worker_id"], "qty": 5})
    assert r.status_code == 200, r.text
    rep2 = o.get("/reports/day", params={"day": day.isoformat()}).json()
    assert day.isoformat() not in {m["date"] for m in rep2["missing"]}
    t2 = next(t for x in rep2["workers"] if x["worker_id"] == w["worker_id"] for t in x["tasks"] if (t["order_line_id"], t["operation_id"]) == (ol, op))
    assert t2["fact_qty"] == 5 and t2["state"] == "done"


def test_zero_fact_counts_as_entered_and_worker_sees_only_today(full_org):
    o = full_org
    o.replan_all()
    ol, op, w = _first_task(o)
    day = TODAY - timedelta(days=1)
    _snapshot(o, day, ol, op, w, 3)
    assert o.post("/facts", json={"fact_date": day.isoformat(), "order_line_id": ol, "operation_id": op, "worker_id": w["worker_id"],
                                  "qty": 0, "note": "не было материала"}).status_code == 200
    assert day.isoformat() not in {m["date"] for m in o.get("/reports/day", params={"day": day.isoformat()}).json()["missing"]}
    # сборщик за прошлые дни факт не вносит — обращается к бригадиру
    assert o.post("/facts", "worker", json={"fact_date": day.isoformat(), "order_line_id": ol, "operation_id": op,
                                            "worker_id": o.w1["id"], "qty": 1}).status_code == 403
    assert o.get("/reports/day", "worker", params={"day": day.isoformat()}).json()["missing"] == []


def test_foreman_sees_missing_fact_only_of_own_section(full_org):
    o = full_org
    o.replan_all()
    own = next(w for w in o.workers if w["work_center_id"] == o.wcs[0]["id"])
    other = next(w for w in o.workers if w["work_center_id"] != o.wcs[0]["id"])
    ol, op, _ = _first_task(o)
    day = TODAY - timedelta(days=3)
    for w in (own, other):
        _snapshot(o, day, ol, op, {"worker_id": w["id"], "work_center_id": w["work_center_id"]}, 2)
    owner_m = {m["date"]: m for m in o.get("/reports/day", params={"day": day.isoformat()}).json()["missing"]}[day.isoformat()]
    fore_m = {m["date"]: m for m in o.get("/reports/day", "foreman", params={"day": day.isoformat()}).json()["missing"]}[day.isoformat()]
    assert owner_m["workers"] == 2 and fore_m["workers"] == 1
    ov = o.get("/reports/overview").json()["missing_fact"]
    assert ov["days"] >= 1 and ov["tasks"] >= 2 and ov["first"] == day.isoformat()
    assert o.get("/reports/overview", "foreman").json()["missing_fact"]["tasks"] == 1


# ------------------------------------------------------------------ работа в нерабочие дни
def next_weekday(d, weekday):
    return d + timedelta(days=(weekday - d.weekday()) % 7 or 7)


def test_work_on_weekend_is_allowed_pinned_and_survives_replan(full_org):
    o = full_org
    o.replan_all()
    sat = next_weekday(TODAY, 5)
    ol, op, w = _first_task(o)
    rep = o.get("/reports/day", params={"day": sat.isoformat()}).json()
    assert rep["is_workday"] is False and rep["can_edit"] is True
    r = o.post("/plan-day/items", json={"plan_date": sat.isoformat(), "worker_id": w["worker_id"], "order_line_id": ol, "operation_id": op, "qty": 2})
    assert r.status_code == 201, r.text
    assert r.json()["auto_pinned"] is True and r.json()["item"]["is_locked"] is True
    # перепланирование: работа в выходной остаётся и не планируется второй раз на рабочие дни
    o.replan_all()
    rep = o.get("/reports/day", params={"day": sat.isoformat()}).json()
    card = next(x for x in rep["workers"] if x["worker_id"] == w["worker_id"])
    t = next(t for t in card["tasks"] if (t["order_line_id"], t["operation_id"]) == (ol, op))
    assert t["plan_qty"] == 2 and t["locked"] and card["pinned"]
    assert sat.isoformat() in rep["days"]                                    # выходной попал в список дней плана
    # на выходной автопланирование никому ничего не ставит
    assert sum(sum(x["plan_qty"] for x in c["tasks"]) for c in rep["workers"]) == 2


def test_weekend_work_counts_towards_the_need_not_twice(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})            # замороженные строки перепланирование не уменьшает (BR-11)
    o.replan_all()
    ol, op, w = _first_task(o)
    before = _total_plan_qty(o, ol, op)
    sat = next_weekday(TODAY, 5)
    assert o.post("/plan-day/items", json={"plan_date": sat.isoformat(), "worker_id": w["worker_id"], "order_line_id": ol, "operation_id": op, "qty": 2}).status_code == 201
    o.replan_all()
    assert _total_plan_qty(o, ol, op) == before                              # выходная работа вычтена из потребности: всего столько же, сколько нужно


def _total_plan_qty(o, ol, op):
    days = o.get("/reports/day", params={"day": TODAY.isoformat()}).json()["days"]
    tot = 0
    for d in days:
        for c in o.get("/reports/day", params={"day": d}).json()["workers"]:
            tot += sum(t["plan_qty"] for t in c["tasks"] if (t["order_line_id"], t["operation_id"]) == (ol, op))
    return tot


def test_workday_task_is_not_auto_pinned_and_holiday_counts_as_off_day(full_org):
    o = full_org
    o.replan_all()
    ol, op, w = _first_task(o)
    wk = next_weekday(TODAY, 2) if next_weekday(TODAY, 2) > TODAY else TODAY + timedelta(days=1)
    while wk.weekday() > 4:
        wk += timedelta(days=1)
    r = o.post("/plan-day/items", json={"plan_date": wk.isoformat(), "worker_id": w["worker_id"], "order_line_id": ol, "operation_id": op, "qty": 1})
    assert r.status_code == 201 and r.json()["auto_pinned"] is False and r.json()["item"]["is_locked"] is False
    assert o.get("/reports/day", params={"day": wk.isoformat()}).json()["is_workday"] is True
    # нерабочая дата из календаря организации — тоже нерабочий день
    h = o.post("/holidays", json={"holiday_date": wk.isoformat(), "name": "Выходной"})
    assert h.status_code == 201, h.text
    assert o.get("/reports/day", params={"day": wk.isoformat()}).json()["is_workday"] is False


# ------------------------------------------------------------------ бригадир и работники его участка
def test_foreman_sees_only_own_section_workers(full_org):
    o = full_org
    own_c = o.wcs[0]["id"]
    allw = o.get("/workers").json()["items"]
    mine = o.get("/workers", "foreman").json()["items"]
    assert mine and {w["work_center_id"] for w in mine} == {own_c}
    assert len(mine) < len(allw)
    other = next(w for w in allw if w["work_center_id"] != own_c)
    assert o.get(f"/workers/{other['id']}", "foreman").status_code == 404
    assert o.get(f"/workers/{mine[0]['id']}", "foreman").status_code == 200


def test_foreman_manages_own_workers_only(full_org):
    o = full_org
    own_c, other_c = o.wcs[0]["id"], o.wcs[1]["id"]
    # создаёт — сразу на своём участке
    r = o.post("/workers", "foreman", json={"name": "Новичок участка", "perf": 0.8})
    assert r.status_code == 201 and r.json()["work_center_id"] == own_c
    wid = r.json()["id"]
    assert o.post("/workers", "foreman", json={"name": "Чужой", "work_center_id": other_c}).status_code == 403
    # правит своего
    p = o.patch(f"/workers/{wid}", "foreman", json={"perf": 1.2, "status": "unavailable", "version": r.json()["version"]})
    assert p.status_code == 200 and p.json()["perf"] == 1.2 and p.json()["status"] == "unavailable"
    assert o.patch(f"/workers/{wid}", "foreman", json={"work_center_id": other_c}).status_code == 403          # перевод — не его право
    assert o.patch(f"/workers/{wid}", "foreman", json={"work_center_id": own_c}).status_code == 200
    # чужого не видит и не правит
    foreign = next(w for w in o.get("/workers").json()["items"] if w["work_center_id"] == other_c)
    assert o.patch(f"/workers/{foreign['id']}", "foreman", json={"perf": 2}).status_code == 404
    # удаляет своего (полный доступ к работникам участка)
    assert o.delete(f"/workers/{wid}", "foreman").status_code == 204
    foreign_w = next(w for w in o.get("/workers").json()["items"] if w["work_center_id"] == other_c)
    assert o.delete(f"/workers/{foreign_w['id']}", "foreman").status_code == 404


def test_foreman_absences_and_telegram_only_for_own_workers(full_org):
    o = full_org
    own_c, other_c = o.wcs[0]["id"], o.wcs[1]["id"]
    mine = next(w for w in o.get("/workers").json()["items"] if w["work_center_id"] == own_c)
    foreign = next(w for w in o.get("/workers").json()["items"] if w["work_center_id"] == other_c)
    body = {"date_from": (TODAY + timedelta(days=5)).isoformat(), "date_to": (TODAY + timedelta(days=6)).isoformat(), "reason": "отпуск"}
    a = o.post("/absences", "foreman", json={"worker_id": mine["id"], **body})
    assert a.status_code == 201, a.text
    assert o.post("/absences", "foreman", json={"worker_id": foreign["id"], **body}).status_code == 404
    fa = o.post("/absences", json={"worker_id": foreign["id"], **body}).json()               # чужое отсутствие создал администратор
    seen = {x["worker_id"] for x in o.get("/absences", "foreman").json()["items"]}
    assert seen == {mine["id"]}
    assert o.patch(f"/absences/{fa['id']}", "foreman", json={"reason": "x"}).status_code == 404
    assert o.delete(f"/absences/{a.json()['id']}", "foreman").status_code == 204
    # Telegram: код — только работнику своего участка
    assert o.post("/telegram/codes", "foreman", json={"worker_id": foreign["id"]}).status_code == 403
    assert o.post("/telegram/codes", "foreman", json={"user_id": 1}).status_code == 403
    assert o.post("/telegram/codes", "foreman", json={"worker_id": mine["id"]}).status_code in (201, 409)     # 409 — бот не настроен
    assert o.get("/telegram/status", "foreman").status_code == 200
    assert o.get("/telegram/status", "worker").status_code == 403


def test_foreman_manages_access_of_own_workers_within_own_orders(full_org):
    """Допуски к заказам: бригадир ставит их своим работникам и только по позициям своего участка; чужие допуски не трогает."""
    o = full_org
    own_c, other_c = o.wcs[0]["id"], o.wcs[1]["id"]
    mine = next(w for w in o.get("/workers").json()["items"] if w["work_center_id"] == own_c)
    foreign = next(w for w in o.get("/workers").json()["items"] if w["work_center_id"] == other_c)
    lines = [x["id"] for x in o.get("/order-lines").json()["items"]]
    outside_id = lines[-1]                                                                          # эту позицию целиком отдаём другому участку
    ops = o.get(f"/order-lines/{outside_id}/operations").json()["items"]
    assert o.put(f"/order-lines/{outside_id}/operations", json={"assignments": [
        {"operation_id": x["operation_id"], "work_center_ids": [other_c]} for x in ops]}).status_code == 200
    g = o.get(f"/workers/{mine['id']}/access", "foreman").json()
    sel = set(g["selectable_ids"])
    assert sel and outside_id not in sel and sel < set(lines)                                       # только позиции его участка, не все
    assert o.get(f"/workers/{mine['id']}/access").json()["selectable_ids"] is None                  # у руководителя ограничения нет
    outside = outside_id
    one = sorted(sel)[0]
    # руководитель ранее допустил работника к позиции вне участка бригадира — она сохранится
    assert o.put(f"/workers/{mine['id']}/access", json={"order_line_ids": [outside]}).status_code == 200
    r = o.put(f"/workers/{mine['id']}/access", "foreman", json={"order_line_ids": [one]})
    assert r.status_code == 200 and set(r.json()["order_line_ids"]) == {one, outside}
    assert o.put(f"/workers/{mine['id']}/access", "foreman", json={"order_line_ids": [outside]}).status_code == 403
    # снятие своих допусков не стирает чужие
    r = o.put(f"/workers/{mine['id']}/access", "foreman", json={"order_line_ids": []})
    assert r.json()["order_line_ids"] == [outside]
    # чужой работник недоступен
    assert o.get(f"/workers/{foreign['id']}/access", "foreman").status_code == 404
    assert o.put(f"/workers/{foreign['id']}/access", "foreman", json={"order_line_ids": [one]}).status_code == 404
    assert o.put(f"/workers/{mine['id']}/access", "worker", json={"order_line_ids": []}).status_code == 403
