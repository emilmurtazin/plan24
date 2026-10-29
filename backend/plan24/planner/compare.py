"""Сравнение вариантов жёсткости приоритета (BR-20). Чистая функция: активный план не затрагивается."""
from __future__ import annotations

from dataclasses import replace

from .engine import plan
from . import reports
from .models import PlanInput

VARIANTS = (("Жёстко", 100.0), ("Сбалансировано", 50.0), ("Мягко", 0.0))


def _metrics(inp: PlanInput):
    res = plan(inp)
    rows = reports.status_rows(inp, res)
    lm = reports.load_matrix(inp, res)
    max_load = max((v for w in lm["plan"].values() for v in w.values()), default=0)
    return res, rows, round(max_load, 2)


def compare_strictness(inp: PlanInput, variants=VARIANTS) -> dict:
    """Сдвиг прогноза — в рабочих днях относительно текущей настройки."""
    base_res, base_rows, base_max = _metrics(inp)
    idx = {d.isoformat(): i for i, d in enumerate(base_res.work_days)}
    base_fc = {r["id"]: idx.get(r["forecast"]) for r in base_rows}

    def describe(label, strictness, rows, max_load, current):
        shifts, worse = [], []
        for r in rows:
            a, b = base_fc.get(r["id"]), idx.get(r["forecast"])
            if a is not None and b is not None:
                shifts.append(b - a)
                if b > a:
                    worse.append((b - a, r["id"], r["product"], r["project"]))
        worse.sort(reverse=True)
        return {"label": label, "strictness": strictness, "current": current,
                "at_risk": sum(1 for r in rows if r["status"] == reports.STATUS_RISK),
                "avg_shift_days": round(sum(shifts) / len(shifts), 2) if shifts else 0.0,
                "max_load": max_load,
                "most_affected": [{"id": i, "product": p, "project": pj, "shift_days": s} for s, i, p, pj in worse[:3]]}

    out = [describe("Текущая настройка", inp.config.priority_strictness, base_rows, base_max, True)]
    for label, s in variants:
        cfg = replace(inp.config, priority_strictness=s)
        _, rows, mx = _metrics(replace(inp, config=cfg))
        out.append(describe(label, s, rows, mx, False))
    return {"variants": out, "basis": "рабочие дни; сдвиг считается относительно текущей настройки"}
