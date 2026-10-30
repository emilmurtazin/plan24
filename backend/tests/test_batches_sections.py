"""Партии на операциях (старт следующей операции, когда выполнена часть предыдущей) и настройки планирования по участкам."""
from datetime import date, timedelta

from conftest import TODAY
from plan24.planner.engine import plan
from plan24.planner.models import Operation, OrderLine, PlanConfig, PlanInput, Worker
from test_refs import mk_order, mk_product

MON = date(2026, 10, 5)
DAYS = tuple(MON + timedelta(days=i) for i in range(30) if (MON + timedelta(days=i)).weekday() < 5)


def run(ops, workers, op_workers=None, qty=40, **cfg):
    order = OrderLine(id="1", project="П", product="P", qty=qty, due=DAYS[-1], priority=1, op_workers=op_workers)
    return plan(PlanInput(order_lines=(order,), products={"P": tuple(ops)}, workers=tuple(workers), facts=(),
                          config=PlanConfig(today=DAYS[0], work_days=DAYS, cap_load=True, **cfg)))


def by_op(res, name):
    out: dict = {}
    for it in res.items:
        if it.operation == name:
            out[DAYS.index(it.date)] = out.get(DAYS.index(it.date), 0) + it.qty
    return out


def two_ops(pct):
    return [Operation("A", 5, sequence=1), Operation("B", 20, sequence=2, start_after_pct=pct)]


W = [Worker(1, "w1"), Worker(2, "w2")]
ASSIGN = {"A": frozenset({"w1"}), "B": frozenset({"w2"})}


def test_batch_starts_next_operation_before_previous_is_finished():
    legacy = run(two_ops(None), W, ASSIGN)
    batch = run(two_ops(25), W, ASSIGN)
    a, b0, b = by_op(batch, "A"), by_op(legacy, "B"), by_op(batch, "B")
    assert max(a) == 7 and sum(a.values()) == 40                      # предыдущая операция идёт все 8 дней
    assert min(b0) == 7                                               # без партии следующая начинается только на её последний день
    assert min(b) == 1 and sum(b.values()) == 40                      # при 25% (10 из 40) — на второй день, дальше по мере готовности
    assert max(b) <= max(a) and max(b) < max(b0) + 1                 # и заканчивает не позже, чем без партии
    ca = cb = 0                                                       # следующая операция никогда не делает больше, чем уже сделано предыдущей
    for d in range(len(DAYS)):
        ca += a.get(d, 0)
        cb += b.get(d, 0)
        assert cb <= ca, f"день {d}: {cb} > {ca}"


def test_batch_default_and_hundred_percent_equal_legacy_behaviour():
    legacy = run(two_ops(None), W, ASSIGN).items
    assert run(two_ops(100), W, ASSIGN).items == legacy


def test_batch_not_reached_means_next_operation_waits():
    r = run([Operation("A", 5, sequence=1), Operation("B", 20, sequence=2, start_after_pct=90)], W, ASSIGN)
    b = by_op(r, "B")
    assert min(b) == 7                                                # 90% = 36 из 40 изделий: к концу 7-го дня сделано 35, к концу 8-го (индекс 7) — 40
    assert sum(b.values()) == 40


def test_batch_ignored_for_first_operation():
    ops = [Operation("A", 5, sequence=1, start_after_pct=10), Operation("B", 20, sequence=2)]
    assert run(ops, W, ASSIGN).items == run([Operation("A", 5, sequence=1), Operation("B", 20, sequence=2)], W, ASSIGN).items


def test_section_efficiency_changes_worker_output():
    ws = [Worker(1, "slow", efficiency=0.5), Worker(2, "norm")]
    r = run([Operation("A", 10, sequence=1)], ws, qty=100)
    day0 = {it.worker: it.qty for it in r.items if it.date == DAYS[0]}
    assert day0 == {"slow": 5, "norm": 10}


def test_section_max_workers_limits_each_section_separately():
    ws = [Worker(1, "a1", section=1, max_workers_per_op=1), Worker(2, "a2", section=1, max_workers_per_op=1),
          Worker(3, "b1", section=2, max_workers_per_op=1)]
    r = run([Operation("A", 10, sequence=1)], ws, qty=200)
    assert {it.worker for it in r.items if it.date == DAYS[0]} == {"a1", "b1"}          # по одному от каждого участка
    free = run([Operation("A", 10, sequence=1)], [Worker(i, f"x{i}") for i in range(1, 4)], qty=200)
    assert len({it.worker for it in free.items if it.date == DAYS[0]}) == 3              # без настроек участков — общий предел 4


def test_section_load_threshold_and_start_share_fall_back_to_general():
    ws = [Worker(1, "w", today_load_threshold=None, min_start_share=None)]
    r = run([Operation("A", 10, sequence=1)], ws)
    assert r.items and r.work_days == DAYS


# ------------------------------------------------------------------ API
def test_api_section_settings_and_batch_field(org):
    c = org.post("/work-centers", json={"name": "Сборка", "efficiency": 0.8, "max_workers_per_op": 2}).json()
    assert c["efficiency"] == 0.8 and c["max_workers_per_op"] == 2 and c["today_load_threshold"] is None
    assert org.post("/work-centers", json={"name": "Х", "efficiency": 5}).status_code == 422
    assert org.patch(f"/work-centers/{c['id']}", json={"efficiency": None, "min_start_share": 0.3}).json()["efficiency"] is None
    got = org.get(f"/work-centers/{c['id']}").json()
    assert got["min_start_share"] == 0.3 and got["max_workers_per_op"] == 2
    s = org.get("/settings").json()
    assert "strictness_by_level" not in s and s["priority_strictness"] == 100             # «жёсткость по уровням» убрана
    pid, rid = mk_product(org, ops=(("Раскрой", 40, 1), ("Сборка", 6, 2)))
    op = org.get(f"/products/{pid}/routings").json()[0]["operations"][1]
    assert op["start_after_pct"] is None
    assert org.patch(f"/operations/{op['id']}", json={"start_after_pct": 0}).status_code == 422
    assert org.patch(f"/operations/{op['id']}", json={"start_after_pct": 30}).json()["start_after_pct"] == 30
    assert mk_order(org, pid).status_code == 201
    # маршрут уже в заказах: партию менять можно (состав работ не меняется), норму — нельзя
    assert org.patch(f"/operations/{op['id']}", json={"start_after_pct": 50}).status_code == 200
    assert org.patch(f"/operations/{op['id']}", json={"norm_per_day": 99}).status_code == 409
    v2 = org.post(f"/products/{pid}/routings").json()
    assert [o["start_after_pct"] for o in v2["operations"]] == [None, 50]                   # новая версия копирует партию


def test_replan_with_section_settings_and_batches(full_org):
    o = full_org
    for c in o.get("/work-centers").json()["items"]:
        assert o.patch(f"/work-centers/{c['id']}", json={"efficiency": 0.9, "max_workers_per_op": 3}).status_code == 200
    pid = o.get("/order-lines").json()["items"][0]["product_id"]
    for op in o.get(f"/products/{pid}/routings").json()[0]["operations"][1:]:
        assert o.patch(f"/operations/{op['id']}", json={"start_after_pct": 40}).status_code in (200, 409)
    assert o.replan_all().status_code == 201


# ------------------------------------------------------------------ предшественники операций
def stage_ops(**preds):
    """Порядок 1: A (медленная, 5 в день) и B (быстрая, 20 в день); порядок 2: C. Все — на разных работниках."""
    return [Operation("A", 5, sequence=1), Operation("B", 20, sequence=1),
            Operation("C", 20, sequence=2, **preds)]


W3 = [Worker(1, "w1"), Worker(2, "w2"), Worker(3, "w3")]
ASSIGN3 = {"A": frozenset({"w1"}), "B": frozenset({"w2"}), "C": frozenset({"w3"})}


def test_predecessor_removes_wait_for_the_whole_stage():
    legacy = run(stage_ops(), W3, ASSIGN3)
    only_b = run(stage_ops(predecessors=("B",)), W3, ASSIGN3)
    c0, c1 = by_op(legacy, "C"), by_op(only_b, "C")
    assert min(c0) == max(by_op(legacy, "A"))                          # по порядку: C ждёт самую медленную операцию этапа (A)
    assert max(by_op(only_b, "B")) == 1 and min(c1) == 1               # от B одной: B готова за 2 дня — C стартует на второй день
    assert sum(c1.values()) == 40 and max(c1) < max(c0) + 1
    assert by_op(legacy, "A") == by_op(only_b, "A")                    # остальные операции не изменились


def test_several_predecessors_wait_for_all_of_them():
    both = run(stage_ops(predecessors=("A", "B")), W3, ASSIGN3)
    assert min(by_op(both, "C")) == max(by_op(both, "A"))             # ждёт и A


def test_empty_predecessors_start_immediately_and_unknown_names_are_ignored():
    free = run(stage_ops(predecessors=()), W3, ASSIGN3)
    assert min(by_op(free, "C")) == 0 and sum(by_op(free, "C").values()) == 40
    unknown = run(stage_ops(predecessors=("нет такой",)), W3, ASSIGN3)
    assert min(by_op(unknown, "C")) == 0


def test_predecessor_with_batch_start():
    r = run([Operation("A", 5, sequence=1), Operation("B", 20, sequence=2, start_after_pct=25, predecessors=("A",))], W, ASSIGN)
    a, b = by_op(r, "A"), by_op(r, "B")
    assert min(b) == 1 and sum(b.values()) == 40
    ca = cb = 0
    for d in range(len(DAYS)):
        ca += a.get(d, 0)
        cb += b.get(d, 0)
        assert cb <= ca


def test_chain_of_predecessors_is_transitive():
    ops = [Operation("A", 5, sequence=1), Operation("B", 20, sequence=2, predecessors=("A",)),
           Operation("C", 20, sequence=3, predecessors=("B",))]
    r = run(ops, [Worker(1, "w1"), Worker(2, "w2"), Worker(3, "w3")], {"A": frozenset({"w1"}), "B": frozenset({"w2"}), "C": frozenset({"w3"})})
    assert min(by_op(r, "B")) >= max(by_op(r, "A")) and min(by_op(r, "C")) >= max(by_op(r, "B"))


def test_api_predecessors_validation_rename_delete_and_copy(org):
    pid, rid = mk_product(org, ops=(("Раскрой", 40, 1), ("Кромка", 40, 1), ("Сборка", 6, 2)))
    ops = {o["name"]: o for o in org.get(f"/products/{pid}/routings").json()[0]["operations"]}
    assert all(o["predecessors"] is None for o in ops.values())
    sb = ops["Сборка"]["id"]

    def preds_of_sb():
        r = org.get(f"/products/{pid}/routings").json()[0]["operations"]
        return next(o["predecessors"] for o in r if o["id"] == sb)

    bad = lambda preds: org.patch(f"/operations/{sb}", json={"predecessors": preds}).status_code
    assert bad(["Сборка"]) == 422 and bad(["Нет такой"]) == 422                       # сама себя / несуществующая
    assert org.patch(f"/operations/{ops['Раскрой']['id']}", json={"predecessors": ["Сборка"]}).status_code == 422     # порядок не меньше
    assert org.patch(f"/operations/{sb}", json={"predecessors": ["Кромка", "Кромка"]}).json()["predecessors"] == ["Кромка"]
    org.patch(f"/operations/{ops['Кромка']['id']}", json={"name": "Кромление"})        # переименование обновляет ссылки
    assert preds_of_sb() == ["Кромление"]
    assert org.patch(f"/operations/{sb}", json={"predecessors": []}).json()["predecessors"] == []       # без предшественников
    assert org.patch(f"/operations/{sb}", json={"predecessors": ["Кромление", "Раскрой"]}).status_code == 200
    assert org.patch(f"/operations/{ops['Раскрой']['id']}", json={"sequence": 5}).status_code == 422   # от неё зависит «Сборка»
    assert org.delete(f"/operations/{ops['Кромка']['id']}").status_code == 204
    assert preds_of_sb() == ["Раскрой"]
    org.delete(f"/operations/{ops['Раскрой']['id']}")
    assert preds_of_sb() is None                 # опустел — снова «по порядку»
    org.patch(f"/operations/{sb}", json={"predecessors": []})
    assert mk_order(org, pid).status_code == 201
    assert org.patch(f"/operations/{sb}", json={"predecessors": None}).status_code == 200      # в маршруте, стоящем в заказах — можно
    org.patch(f"/operations/{sb}", json={"predecessors": []})
    v2 = org.post(f"/products/{pid}/routings").json()
    assert [o["predecessors"] for o in v2["operations"]] == [[]]                               # копируется в новую версию
