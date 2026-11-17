"""Горизонт планирования не ограничен двумя месяцами: план строится до завершения заказов."""
from datetime import date, timedelta

from conftest import TODAY


def active(o):
    return o.get("/plans/active").json()["plan"]


def test_small_orders_keep_minimum_horizon(full_org):
    o = full_org
    p = o.post("/plans/replan").json()
    assert p["params"]["horizon_weeks"] == 8 and not p["stale"]
    last = date.fromisoformat(p["work_days"][-1])
    assert last >= TODAY + timedelta(days=55)                      # как и раньше: минимум 8 недель


def test_big_order_extends_horizon_until_it_fits(full_org):
    o = full_org
    ol = o.get("/order-lines").json()["items"][0]
    assert o.patch(f"/order-lines/{ol['id']}", json={"qty": ol["qty"] * 20, "version": ol["version"]}).status_code == 200
    p = o.post("/plans/replan").json()
    assert p["params"]["horizon_weeks"] > 8                                                       # горизонт расширился
    assert date.fromisoformat(p["work_days"][-1]) > TODAY + timedelta(days=8 * 7)                  # план идёт дальше двух месяцев
    assert not [w for w in p["warnings"] if w["code"] in ("HORIZON_EXCEEDED", "PRIORITY_HORIZON") and w["order_line_id"] == ol["id"]]
    assert p["stale"] is False                                                                    # проверка «устарел» берёт тот же горизонт
    assert o.get("/plans/active").json()["plan"]["stale"] is False


def test_horizon_has_a_cap(full_org):
    """Если работа не помещается и за три года (нет мощности), расчёт не зацикливается: горизонт упирается в 156 недель, предупреждение остаётся."""
    o = full_org
    ol = o.get("/order-lines").json()["items"][0]
    assert o.patch(f"/order-lines/{ol['id']}", json={"qty": ol["qty"] * 400, "version": ol["version"]}).status_code == 200
    p = o.post("/plans/replan").json()
    assert p["params"]["horizon_weeks"] == 156
    assert any(w["code"] == "HORIZON_EXCEEDED" and w["order_line_id"] == ol["id"] for w in p["warnings"])
