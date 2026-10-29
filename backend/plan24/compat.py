"""Адаптер «формат листов скрипта» → PlanInput (ТЗ, раздел 11 и Приложение A).

Повторяет разбор данных из distributeTasks(): значения по умолчанию, parseQty,
фильтр по статусу «доступен», допуск «ID проектов».
Используется и для импорта Excel, и для тестов паритета с эмулятором.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from .planner import (FactRow, Operation, OrderLine, PlanConfig, PlanInput, Worker,
                      build_work_calendar)


def to_num(v: Any, default: float) -> float:
    s = "" if v is None else str(v).replace(",", ".").strip()
    if s == "":
        return default
    try:
        return float(s)
    except ValueError:
        return default


def num_or(v: Any, default: float) -> float:
    """JS: Number(x) || default — ноль и NaN дают значение по умолчанию."""
    n = to_num(v, 0)
    return n if n else default


def parse_qty(v: Any) -> float:
    """«18шт. к» → 18, «1 200» → 1200, «2,5» → 2.5 (parseQty скрипта)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    t = "" if v is None else str(v)
    t = re.sub(r"(\d)[\s\u00a0]+(?=\d)", r"\1", t).replace(",", ".", 1)
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else 0.0


def norm_id(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return re.sub(r"\.0+$", "", s)


def parse_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    parts = s.split(".")
    if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
        y = int(parts[2])
        if y < 100:
            y += 2000
        try:
            return date(y, int(parts[1]), int(parts[0]))
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(s[:10]).date()
    except ValueError:
        return None


def _fmt_num(x: float):
    return int(x) if float(x).is_integer() else x


def build_input(ds: dict, today: date, weekdays: tuple = (0, 1, 2, 3, 4), efficiency: float = 1.0, cap_load: bool = False) -> PlanInput:
    """ds — словарь формата tools/emulate.js: orders, ops, workers, holidays, facts, settings."""
    s = ds.get("settings") or {}

    # --- исполнители ---
    avail, perf_all = [], {}
    worker_projects = {}
    for r in ds["workers"]:
        name = str(r["name"]).strip()
        if name:
            perf_all[name] = num_or(r.get("perf"), 1)
        if str(r.get("status") or "доступен").strip().lower() != "доступен":
            continue
        avail.append(Worker(id=len(avail), name=name, perf=num_or(r.get("perf"), 1),
                            skill=num_or(r.get("skill"), 1)))
        worker_projects[name] = str(r.get("projects") or "").strip()

    # --- маршруты ---
    products: dict = {}
    for r in ds["ops"]:
        item = str(r["item"]).strip()
        if not item:
            continue
        products.setdefault(item, []).append(Operation(
            name=str(r["op"]).strip(),
            norm_per_day=num_or(r.get("norm"), 1),
            min_skill=num_or(r.get("skill"), 1),
            per_item=num_or(r.get("perItem"), 1),
            sequence=num_or(r.get("order"), 999),
            hold_qty=to_num(r.get("blocked"), 0) or 0,
        ))
    products = {k: tuple(v) for k, v in products.items()}

    # --- позиции ---
    holidays = [parse_date(h) for h in ds.get("holidays", []) if parse_date(h)]
    orders = []
    for r in ds["orders"]:
        oid, item = str(r["id"]).strip(), str(r["item"]).strip()
        if not oid or not item:
            continue
        allowed = frozenset(
            n for n, p in worker_projects.items()
            if not p or norm_id(oid) in [norm_id(x) for x in p.split(",") if norm_id(x)]
        )
        if len(allowed) == len(worker_projects):
            allowed = None                 # None = все
        orders.append(OrderLine(
            id=oid, project=str(r["project"]).strip(), product=item,
            qty=parse_qty(r.get("qty")), due=parse_date(r.get("due")),
            priority=num_or(r.get("priority"), 999), allowed_workers=allowed,
        ))

    # --- факт (как в replanAll: нули без причины удаляются) ---
    facts = []
    for f in ds.get("facts") or []:
        qty = to_num(f.get("qty"), 0)
        reason = str(f.get("reason") or "").strip()
        if qty == 0 and not reason:
            continue
        facts.append(FactRow(date=parse_date(f["date"]), order_id=str(f["id"]).strip(),
                             project=str(f.get("project") or "").strip(), product=str(f["item"]).strip(),
                             op=str(f["op"]).strip(), worker=str(f["worker"]).strip(),
                             plan=to_num(f.get("plan"), 0), qty=qty, reason=reason))

    cal = build_work_calendar(today, [o.due for o in orders], holidays, weekdays)
    cfg = PlanConfig(
        today=cal[0] if cal else today, work_days=cal, efficiency=efficiency,
        max_workers_per_op=max(1, int(_js_round(to_num(s.get("maxWorkers"), 4)))),
        today_load_threshold=min(1.0, max(0.0, to_num(s.get("todayThreshold"), 0.6))),
        min_start_share=min(1.0, max(0.1, to_num(s.get("minStartShare"), 0.5))),
        priority_strictness=min(100.0, max(0.0, to_num(s.get("strictness"), 100))),
        cap_load=cap_load,
    )
    return PlanInput(order_lines=tuple(orders), products=products, workers=tuple(avail),
                     facts=tuple(facts), config=cfg, all_workers_perf=perf_all)


def _js_round(x):
    import math
    return math.floor(x + 0.5)


def plan_rows_script_format(res) -> list:
    """Строки как на листе «Задания на день» (для сравнения с эмулятором)."""
    return [[it.date.strftime("%d.%m.%Y"), it.order_id, it.project, it.product, it.operation,
             it.qty, it.worker] for it in res.items]
