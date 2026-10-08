"""Паритет с оригинальным скриптом через Node-эмулятор (ТЗ 8.4). Жёсткость 100% и любые значения —
скрипт сам поддерживает жёсткость, поэтому сравниваем все наборы целиком."""
import json, os, subprocess, shutil
from datetime import date
import pytest
from plan24.compat import build_input, plan_rows_script_format
from plan24.planner import plan, reports
from plan24.synthetic import make_datasets

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="нужен node для эмулятора")
DATASETS = make_datasets()


def run_script(ds, today):
    payload = dict(ds, today=today.isoformat())
    p = subprocess.run(["node", os.path.join(ROOT, "tools", "emulate.js")], input=json.dumps(payload),
                       capture_output=True, text=True, env=dict(os.environ, TZ="UTC"), timeout=120)
    out = json.loads(p.stdout)
    assert "error" not in out, out.get("error")
    return out


@pytest.mark.parametrize("name", list(DATASETS))
def test_plan_matches_script(name):
    ds, today = DATASETS[name]
    ref = run_script(ds, today)
    res = plan(build_input(ds, today))
    mine = plan_rows_script_format(res)
    assert len(mine) == len(ref["plan"]), (len(mine), len(ref["plan"]))
    for a, b in zip(mine, ref["plan"]):
        assert [str(x) for x in a] == [str(x) for x in b], (a, b)


@pytest.mark.parametrize("name", list(DATASETS))
def test_status_matches_script(name):
    ds, today = DATASETS[name]
    ref = run_script(ds, today)
    inp = build_input(ds, today)
    mine = reports.status_rows(inp, plan(inp))
    ref_by_id = {str(r[0]): r for r in ref["status"]}
    assert set(r["id"] for r in mine) == set(ref_by_id)
    for r in mine:
        b = ref_by_id[r["id"]]
        # [ID, Проект, Изделие, Всего, Всего оп, Сделано, Осталось, %, Прогноз, Срок, Статус]
        assert r["required_ops"] == b[4] and r["done_ops"] == b[5] and r["left_ops"] == b[6]
        assert r["status"] == b[10], (name, r["id"], r["status"], b[10])
        assert r["forecast_text"] == (b[8] if isinstance(b[8], str) else b[8]) or r["forecast_text"] in str(b[8])
