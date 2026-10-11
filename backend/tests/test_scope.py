"""Перепланирование участка: бригадир меняет только план своих исполнителей (решение владельца)."""
from collections import Counter
from datetime import timedelta

from sqlalchemy import select

from conftest import TODAY
from plan24 import db as dbm
from plan24.models import Plan, PlanItem, Worker


def items(org, plan_id=None):
    """Строки активного (или указанного) плана: (дата, позиция, операция, исполнитель, кол-во, участок исполнителя)."""
    s = dbm.admin_session()
    try:
        pid = plan_id or s.scalar(select(Plan.id).where(Plan.status == "active"))
        w = {x.id: x.work_center_id for x in s.scalars(select(Worker))}
        return [(i.plan_date, i.order_line_id, i.operation_id, i.worker_id, i.qty, w[i.worker_id])
                for i in s.scalars(select(PlanItem).where(PlanItem.plan_id == pid))]
    finally:
        s.close()


def by_center(rows, center):
    return Counter((d, o, op, w, q) for d, o, op, w, q, c in rows if c == center)


def test_foreman_replan_leaves_other_centers_untouched(full_org):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})                    # чтобы заморозка не маскировала результат
    p1 = o.replan_all().json()
    before = items(o)
    mine, other = o.wcs[0]["id"], o.wcs[1]["id"]
    # у исполнителя своего участка появилось отсутствие — это должно перестроить только его участок
    victim = next(w for w in o.workers if w["work_center_id"] == mine)
    o.post("/absences", json={"worker_id": victim["id"], "date_from": TODAY.isoformat(),
                              "date_to": (TODAY + timedelta(days=10)).isoformat()})
    r = o.post("/plans/replan", "foreman", json={})
    assert r.status_code == 201 and r.json()["params"]["scope_center_id"] == mine
    after = items(o)
    assert by_center(after, other) == by_center(before, other)       # чужой участок: побайтно тот же план
    assert by_center(after, mine) != by_center(before, mine)         # свой участок перестроен
    assert not [1 for d, _, _, w, _, c in after if w == victim["id"] and TODAY <= d <= TODAY + timedelta(days=10)]
    assert o.get("/plans/active").json()["plan"]["id"] != p1["id"]


def test_scoped_replan_keeps_total_work_per_operation(full_org):
    """Работа не теряется и не дублируется: сумма по (позиция, операция) та же, что и до перепланирования участка."""
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    o.replan_all()
    total = lambda rows: Counter({k: sum(q for (a, b, _, _, q, _) in rows if (a, b) == k) for k in {(x[1], x[2]) for x in rows}})
    before = total(items(o))
    # заготавливаем изменения участка: исполнитель недоступен на день
    victim = next(w for w in o.workers if w["work_center_id"] == o.wcs[0]["id"])
    o.post("/absences", json={"worker_id": victim["id"], "date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()})
    o.post("/plans/replan", "foreman", json={})
    after = total(items(o))
    # участок мог не вместить часть работы в горизонт — тогда это видно в предупреждениях, а не пропадает молча
    warns = o.get("/plans/active").json()["plan"]["warnings"]
    if after != before:
        assert any(w["code"] == "HORIZON_EXCEEDED" for w in warns), (before - after, after - before)
    else:
        assert after == before


def test_new_order_goes_only_to_the_foremans_workers(full_org):
    o = full_org
    o.replan_all()
    prj, prod = o.ids("/projects")[0], o.ids("/products")[0]
    new = o.post("/order-lines", json={"project_id": prj, "product_id": prod, "qty": 6, "ext_id": "NEW-1", "priority": 1}).json()
    o.post("/plans/replan", "foreman", json={})
    mine = o.wcs[0]["id"]
    rows = [r for r in items(o) if r[1] == new["id"]]
    assert rows, "новая позиция не запланирована на участок бригадира"
    assert {c for *_, c in rows} == {mine}
    # другой участок для неё ничего не получил (проверено выше); перерасчёт руководителем по участку не падает
    assert o.replan_all().status_code == 201


def test_foreman_cannot_target_another_center_or_replan_without_base_plan(full_org):
    o = full_org
    r = o.post("/plans/replan", "foreman", json={})
    assert r.status_code == 409 and "общий план" in r.json()["title"]       # общего плана ещё нет
    o.replan_all()
    assert o.post("/plans/replan", "foreman", json={"work_center_id": o.wcs[1]["id"]}).status_code == 403
    assert o.post("/plans/compare", "foreman", json={"work_center_id": o.wcs[1]["id"]}).status_code == 403
    assert o.post("/plans/replan", "foreman", json={"work_center_id": o.wcs[0]["id"]}).status_code == 201   # свой — можно


def test_manager_replans_by_center_only(full_org):
    o = full_org
    o.replan_all()
    r = o.post("/plans/replan", "manager", json={"work_center_id": o.wcs[1]["id"]})
    assert r.status_code == 201 and r.json()["params"]["scope_center_id"] == o.wcs[1]["id"]
    assert o.post("/plans/replan", "manager", json={"work_center_id": 999999}).status_code == 404
    # «вся организация» после первого расчёта недоступна
    assert o.post("/plans/replan", "manager", json={}).status_code == 422


def test_scoped_plan_is_not_stale_and_compare_is_scoped_and_pure(full_org):
    o = full_org
    o.replan_all()
    o.post("/plans/replan", "foreman", json={"strictness": 40})
    assert o.get("/plans/active").json()["plan"]["stale"] is False
    plans_before, rows_before = o.get("/plans").json(), items(o)
    cmp = o.post("/plans/compare", "foreman", json={}).json()
    assert {v["label"] for v in cmp["variants"]} >= {"Жёстко", "Сбалансировано", "Мягко"}
    assert o.get("/plans").json() == plans_before and items(o) == rows_before


def test_other_centers_frozen_flags_and_reasons_are_preserved(full_org):
    o = full_org
    o.replan_all()
    s = dbm.admin_session()
    try:
        wc = {x.id: x.work_center_id for x in s.scalars(select(Worker))}
        old = {(i.plan_date, i.order_line_id, i.operation_id, i.worker_id): (i.is_frozen, i.reason_code)
               for i in s.scalars(select(PlanItem).where(PlanItem.plan_id == s.scalar(select(Plan.id).where(Plan.status == "active"))))
               if wc[i.worker_id] == o.wcs[1]["id"]}
    finally:
        s.close()
    o.post("/plans/replan", "foreman", json={})
    s = dbm.admin_session()
    try:
        new = {(i.plan_date, i.order_line_id, i.operation_id, i.worker_id): (i.is_frozen, i.reason_code)
               for i in s.scalars(select(PlanItem).where(PlanItem.plan_id == s.scalar(select(Plan.id).where(Plan.status == "active"))))
               if wc[i.worker_id] == o.wcs[1]["id"]}
    finally:
        s.close()
    assert new == old                                                  # признаки «зафиксировано»/причина не затёрты


def test_engine_never_gives_new_work_to_fixed_workers():
    from dataclasses import replace
    from plan24.compat import build_input
    from plan24.planner import plan
    from plan24.synthetic import make_datasets
    ds, today = make_datasets()["furniture"]
    inp = build_input(ds, today)
    fixed = frozenset(w.name for w in inp.workers[:3])
    res = plan(replace(inp, fixed_workers=fixed))
    assert res.items and not [i for i in res.items if i.worker in fixed]
    assert {i.worker for i in res.items} <= {w.name for w in inp.workers[3:]}
