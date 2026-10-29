"""Синтетические наборы данных (ТЗ 8.4, п. 2): 5 отраслей + граничные случаи, фиксированный seed.

Формат — как в tools/emulate.js (листы скрипта). Те же данные служат демо-данными онбординга (FR-41).
"""
from __future__ import annotations

import random
from datetime import date, timedelta

ROUTES = {
    "furniture": {
        "Шкаф": [("Раскрой", 40, 1, 1, 1), ("Кромка", 30, 1, 1, 2), ("Присадка", 25, 1, 1, 3),
                 ("Сборка", 5, 2, 1, 4), ("Упаковка", 20, 1, 1, 5)],
        "Тумба": [("Раскрой", 60, 1, 1, 1), ("Сборка", 8.5, 2, 1, 2), ("Упаковка", 25, 1, 0.5, 3)],
    },
    "windows": {
        "Окно ПВХ": [("Резка", 30, 1, 1, 1), ("Сварка", 12, 2, 1, 2), ("Зачистка", 15, 1, 1, 3),
                     ("Сборка", 10, 2, 1, 4), ("Остекление", 14, 1, 1, 5)],
        "Витраж": [("Резка", 8, 1, 1, 1), ("Сборка", 3, 3, 1, 2), ("Остекление", 4, 2, 1, 3)],
    },
    "metal": {
        "Ферма": [("Заготовка", 6, 1, 1, 1), ("Сборка", 2, 2, 1, 2), ("Сварка", 1.5, 3, 1, 3), ("Покраска", 4, 1, 1, 4)],
        "Лестница": [("Заготовка", 5, 1, 1, 1), ("Сварка", 2, 3, 1, 2), ("Покраска", 3, 1, 1, 2)],
    },
    "panels": {
        "Щит ЩР-12": [("Комплектация", 12, 1, 1, 1), ("Монтаж", 3, 2, 1, 2), ("Тесты", 6, 3, 1, 3)],
        "Шкаф управления": [("Комплектация", 6, 1, 1, 1), ("Монтаж", 1.5, 2, 1, 2), ("Тесты", 3, 3, 1, 3)],
    },
    "sewing": {
        "Куртка": [("Крой", 40, 1, 1, 1), ("Пошив", 6, 2, 1, 2), ("ОТК", 30, 1, 1, 3)],
        "Брюки": [("Крой", 50, 1, 1, 1), ("Пошив", 9, 2, 1, 2), ("ОТК", 40, 1, 1, 3)],
    },
}
NAMES = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Орлов", "Смирнов", "Попов", "Волков",
         "Соколов", "Лебедев", "Козлов", "Новиков"]


def _ops(route: dict, rng: random.Random, blocked_for: str = "") -> list:
    out = []
    for item, ops in route.items():
        for (name, norm, skill, per_item, order) in ops:
            out.append({"item": item, "op": name, "norm": norm, "skill": skill,
                        "perItem": per_item, "order": order,
                        "blocked": 3 if (name == blocked_for) else 0})
    return out


def _workers(rng: random.Random, n: int, max_skill: int, restricted: dict | None = None) -> list:
    ws = []
    for i in range(n):
        name = NAMES[i % len(NAMES)] + ("" if i < len(NAMES) else f" {i}")
        ws.append({"name": name, "perf": round(rng.choice([0.8, 0.9, 1, 1, 1.1, 1.2, 1.5]), 1),
                   "skill": rng.randint(1, max_skill), "status": "доступен",
                   "projects": (restricted or {}).get(name, "")})
    return ws


def _orders(route: dict, rng: random.Random, today: date, n: int, prio_levels: int = 3,
            same_project_batches: bool = False) -> list:
    items = list(route)
    orders = []
    for i in range(n):
        item = rng.choice(items)
        project = f"Проект {chr(65 + (0 if same_project_batches else i % 4))}"
        orders.append({
            "id": f"{300 + i}", "project": project, "item": item,
            "qty": rng.choice([5, 8, 12, 20, 30, 45]),
            "due": (today + timedelta(days=rng.randint(5, 40))).isoformat(),
            "priority": rng.randint(1, prio_levels),
        })
    return orders


def make_datasets(today: date = date(2026, 10, 2)) -> dict:
    """name -> (dataset, today)."""
    sets: dict = {}
    for k, (key, nw, ms, no) in enumerate([("furniture", 6, 3, 8), ("windows", 8, 3, 9),
                                           ("metal", 7, 3, 7), ("panels", 6, 3, 8), ("sewing", 10, 2, 10)]):
        rng = random.Random(1000 + k)
        route = ROUTES[key]
        sets[key] = ({"orders": _orders(route, rng, today, no), "ops": _ops(route, rng),
                      "workers": _workers(rng, nw, ms), "holidays": [(today + timedelta(days=9)).isoformat()],
                      "facts": [], "settings": {}}, today)

    # граничный 1: сегодня — суббота, срок в прошлом, блок, допуск по проектам, мало людей
    rng = random.Random(2001)
    route = ROUTES["furniture"]
    ds = {"orders": _orders(route, rng, today, 6), "ops": _ops(route, rng, blocked_for="Сборка"),
          "workers": _workers(rng, 4, 3, restricted={"Иванов": "300, 301", "Петров": "302"}),
          "holidays": [], "facts": [], "settings": {"maxWorkers": 2}}
    ds["orders"][0]["due"] = (today - timedelta(days=5)).isoformat()
    sets["edge_weekend_pastdue"] = (ds, date(2026, 10, 3))

    # граничный 2: партии одного проекта + факт, внесённый «не на ту» партию
    rng = random.Random(2002)
    route = ROUTES["furniture"]
    ds = {"orders": [
        {"id": "307-71", "project": "П1", "item": "Шкаф", "qty": 18, "due": (today + timedelta(days=7)).isoformat(), "priority": 1},
        {"id": "307-72", "project": "П1", "item": "Шкаф", "qty": 6, "due": (today + timedelta(days=17)).isoformat(), "priority": 1},
        {"id": "307-73", "project": "П1", "item": "Шкаф", "qty": 7, "due": (today + timedelta(days=24)).isoformat(), "priority": 2},
        {"id": "400", "project": "П2", "item": "Тумба", "qty": 25, "due": (today + timedelta(days=10)).isoformat(), "priority": 3}],
        "ops": _ops(route, rng), "workers": _workers(rng, 6, 3), "holidays": [],
        "facts": [
            {"date": (today - timedelta(days=1)).isoformat(), "id": "307-72", "project": "П1", "item": "Шкаф",
             "op": "Раскрой", "worker": "Иванов", "plan": 20, "qty": 20},
            {"date": today.isoformat(), "id": "307-71", "project": "П1", "item": "Шкаф",
             "op": "Кромка", "worker": "Петров", "plan": 10, "qty": 9, "reason": "нет материала"},
            {"date": today.isoformat(), "id": "400", "project": "П2", "item": "Тумба",
             "op": "Раскрой", "worker": "Сидоров", "plan": 0, "qty": 40}],
        "settings": {}}
    sets["edge_batches_fact"] = (ds, today)

    # граничный 3: жёсткость 50%, 4 приоритета
    rng = random.Random(2003)
    route = ROUTES["windows"]
    ds = {"orders": _orders(route, rng, today, 10, prio_levels=4), "ops": _ops(route, rng),
          "workers": _workers(rng, 7, 3), "holidays": [], "facts": [],
          "settings": {"strictness": 50}}
    sets["edge_strictness_levels"] = (ds, today)

    # граничный 4: недоступные, нет подходящей квалификации, изделие без операций
    rng = random.Random(2004)
    route = ROUTES["metal"]
    ds = {"orders": _orders(route, rng, today, 5) + [
        {"id": "900", "project": "Z", "item": "Несуществующее", "qty": 3, "due": today.isoformat(), "priority": 1}],
        "ops": _ops(route, rng), "workers": _workers(rng, 3, 1) + [
        {"name": "Отпускник", "perf": 1, "skill": 3, "status": "отпуск", "projects": ""}],
        "holidays": [], "facts": [], "settings": {}}
    sets["edge_no_skill_no_ops"] = (ds, today)

    # граничный 5: заморозка (в скрипте переживает только строка с причиной) и загрузка «сегодня» ≥ 60%
    rng = random.Random(2005)
    route = ROUTES["furniture"]
    tm = (today + timedelta(days=3)).isoformat()   # пятница → следующий рабочий день — понедельник
    ds = {"orders": [
        {"id": "500", "project": "F", "item": "Шкаф", "qty": 30, "due": (today + timedelta(days=14)).isoformat(), "priority": 1},
        {"id": "501", "project": "F", "item": "Тумба", "qty": 20, "due": (today + timedelta(days=21)).isoformat(), "priority": 2}],
        "ops": _ops(route, rng), "workers": [
            {"name": "Иванов", "perf": 1.5, "skill": 3, "status": "доступен", "projects": ""},
            {"name": "Петров", "perf": 1.2, "skill": 3, "status": "доступен", "projects": ""},
            {"name": "Сидоров", "perf": 1, "skill": 1, "status": "доступен", "projects": ""}],
        "holidays": [],
        "facts": [
            {"date": today.isoformat(), "id": "500", "project": "F", "item": "Шкаф", "op": "Раскрой",
             "worker": "Иванов", "plan": 40, "qty": 40},                       # 40/(40·1.5) = 0.67 ≥ 0.6 → загружен, хотя он самый быстрый
            {"date": tm, "id": "500", "project": "F", "item": "Шкаф", "op": "Кромка",
             "worker": "Петров", "plan": 12, "qty": 0, "reason": "перенос"},   # замороженная строка
            {"date": tm, "id": "501", "project": "F", "item": "Тумба", "op": "Раскрой",
             "worker": "Сидоров", "plan": 25, "qty": 0, "reason": "ждём станок"}],
        "settings": {"todayThreshold": 0.6}}
    sets["edge_frozen_loaded"] = (ds, today)

    # граничный 6: у всех исполнителей допуск только к заказу 700 — заказ 701 никому не доступен
    rng = random.Random(2006)
    route = ROUTES["panels"]
    ds = {"orders": [
        {"id": "700", "project": "Q", "item": "Щит ЩР-12", "qty": 10, "due": (today + timedelta(days=12)).isoformat(), "priority": 1},
        {"id": "701", "project": "Q", "item": "Щит ЩР-12", "qty": 10, "due": (today + timedelta(days=12)).isoformat(), "priority": 1}],
        "ops": _ops(route, rng), "workers": _workers(rng, 3, 3, restricted={"Иванов": "700", "Петров": "700", "Сидоров": "700"}),
        "holidays": [], "facts": [], "settings": {}}
    sets["edge_order_nobody_allowed"] = (ds, today)
    return sets
