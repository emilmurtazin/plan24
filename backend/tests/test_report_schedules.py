"""Регулярные отчёты в группы Telegram: настройка администратором, сборка текста, расписание по Москве, ошибки отправки."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from conftest import Org, TODAY
from plan24 import db as dbm, report_schedules as svc
from plan24.models import ReportSchedule, utcnow
from plan24.telegram import bot, client as tgclient

GROUP = "-1001234567890"


class Fake:
    def __init__(self):
        self.sent, self.fail = [], None

    def send_message(self, chat_id, text, reply_markup=None):
        if self.fail:
            raise self.fail
        self.sent.append((chat_id, text))

    def __getattr__(self, name):                      # остальные методы бота этим тестам не нужны
        return lambda *a, **k: None


@pytest.fixture()
def tg(full_org):
    f = Fake()
    tgclient.set_client(f)
    full_org.post("/plans/replan")
    yield f
    tgclient.set_client(None)


def at_msk(hhmm, day=TODAY):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=ZoneInfo("Europe/Moscow")).astimezone(timezone.utc)


def make(o, **kw):
    body = {"name": "Утро", "chat_ref": GROUP, "send_time": "08:30", **kw}
    r = o.post("/report-schedules", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def run(when):
    s = dbm.admin_session(); s.info["write"] = True
    try:
        return svc.run_due(s, when)
    finally:
        s.close()


# ---------------------------------------------------------------- настройка
def test_create_list_update_delete(full_org):
    o = full_org
    r = make(o, weekdays=[0, 2, 4], metrics=["risk_orders", "progress"], section_ids=[o.wcs[0]["id"]])
    assert r["metrics"] == ["progress", "risk_orders"]                          # порядок — как в каталоге
    assert r["weekdays"] == [0, 2, 4] and r["enabled"] is True
    lst = o.get("/report-schedules").json()
    assert [x["id"] for x in lst["items"]] == [r["id"]] and lst["timezone"] == "Europe/Moscow"
    assert {s["id"] for s in lst["sections"]} == {c["id"] for c in o.wcs} and len(lst["metrics"]) == len(svc.METRICS)
    p = o.patch(f"/report-schedules/{r['id']}", json={"send_time": "17:45", "enabled": False, "section_ids": []})
    assert p.status_code == 200 and p.json()["send_time"] == "17:45" and p.json()["section_ids"] == [] and not p.json()["enabled"]
    assert o.delete(f"/report-schedules/{r['id']}").status_code == 204
    assert o.get("/report-schedules").json()["items"] == []


def test_several_reports_and_unique_name(full_org):
    o = full_org
    make(o, name="Утро"); make(o, name="Вечер", send_time="18:00", chat_ref="@plan24_group")
    assert len(o.get("/report-schedules").json()["items"]) == 2
    assert o.post("/report-schedules", json={"name": "Утро", "chat_ref": GROUP, "send_time": "09:00"}).status_code == 409


@pytest.mark.parametrize("patch", [
    {"send_time": "25:00"}, {"send_time": "8:30"}, {"chat_ref": "группа"}, {"chat_ref": "123"}, {"weekdays": []}, {"weekdays": [7]},
    {"metrics": []}, {"metrics": ["nope"]}, {"section_ids": [999999]}, {"name": "  "}])
def test_validation(full_org, patch):
    o = full_org
    body = {"name": "X", "chat_ref": GROUP, "send_time": "08:30", **patch}
    assert o.post("/report-schedules", json=body).status_code == 422


def test_chat_ref_forms():
    assert svc.normalize_chat(" -1001234567890 ") == "-1001234567890"
    assert svc.normalize_chat("https://t.me/plan24_group") == "@plan24_group"
    assert svc.normalize_chat("@plan24_group") == "@plan24_group"
    assert svc.chat_target("-1001234567890") == -1001234567890 and svc.chat_target("@plan24_group") == "@plan24_group"


def test_other_organization_cannot_see_or_change(full_org, env):
    r = make(full_org)
    other = Org(env, "other@b.ru", "Цех Б")
    assert other.get("/report-schedules").json()["items"] == []
    for m, path in (("PATCH", ""), ("DELETE", ""), ("POST", "/send"), ("POST", "/preview")):
        assert other.call(m, f"/report-schedules/{r['id']}{path}", **({"json": {"name": "z"}} if m == "PATCH" else {})).status_code == 404
    assert other.post("/report-schedules", json={"name": "Утро", "chat_ref": GROUP, "send_time": "08:30", "section_ids": [full_org.wcs[0]["id"]]}).status_code == 422


# ---------------------------------------------------------------- текст отчёта
def test_preview_all_metrics_whole_organization(full_org, tg):
    o = full_org
    r = o.post("/report-schedules/preview", json={"name": "Полный", "chat_ref": GROUP, "send_time": "08:30", "metrics": svc.METRIC_KEYS})
    assert r.status_code == 200, r.text
    text = "\n".join(r.json()["messages"])
    assert text.startswith("📊 Полный\nЦех А") and "Выполнение" in text and "Статусы заказов" in text and "Загрузка" in text
    assert "Участок «" not in text                                              # вся организация — одним блоком
    assert all(len(m) <= svc.CHUNK + 5 for m in r.json()["messages"])


def test_preview_by_section_has_block_per_section(full_org, tg):
    o = full_org
    ids = [c["id"] for c in o.wcs[:2]]
    text = "\n".join(o.post("/report-schedules/preview", json={"name": "Уч", "chat_ref": GROUP, "send_time": "08:30", "section_ids": ids,
                                                          "metrics": ["workers", "today_plan", "resources"]}).json()["messages"])
    for c in o.wcs[:2]:
        assert f"Участок «{c['name']}»" in text
    for c in o.wcs[2:]:
        assert f"Участок «{c['name']}»" not in text


def test_only_selected_metrics_are_shown(full_org, tg):
    text = "\n".join(full_org.post("/report-schedules/preview", json={"name": "Один", "chat_ref": GROUP, "send_time": "08:30",
                                                                      "metrics": ["order_statuses"]}).json()["messages"])
    assert "Статусы заказов" in text and "Выполнение:" not in text and "Загрузка" not in text and "Работники" not in text


def test_without_plan_says_so(full_org):
    o = full_org                                           # план не строили
    text = "\n".join(o.post("/report-schedules/preview", json={"name": "Н", "chat_ref": GROUP, "send_time": "08:30", "metrics": ["progress"]}).json()["messages"])
    assert "плана ещё нет" in text


def test_long_report_is_split():
    parts = svc._chunks("шапка", "\n\n".join("строка " * 100 for _ in range(30)))
    assert len(parts) > 1 and all(len(p) <= svc.CHUNK for p in parts) and parts[0].startswith("шапка")


# ---------------------------------------------------------------- отправка вручную
def test_send_now_and_errors(full_org, tg):
    o = full_org
    r = make(o, metrics=["order_statuses"])
    ok = o.post(f"/report-schedules/{r['id']}/send")
    assert ok.status_code == 200 and ok.json()["last_status"] == "ok"
    assert tg.sent and tg.sent[-1][0] == int(GROUP) and "Статусы заказов" in tg.sent[-1][1]
    tg.fail = RuntimeError("telegram sendMessage: 403 Forbidden: bot was kicked from the supergroup chat")
    bad = o.post(f"/report-schedules/{r['id']}/send")
    assert bad.status_code == 502 and "добавьте бота" in bad.json()["title"]
    item = o.get("/report-schedules").json()["items"][0]
    assert item["last_status"] == "error" and "добавьте бота" in item["last_error"]


def test_bot_not_configured(full_org, monkeypatch):
    monkeypatch.delenv("PLAN24_TG_TOKEN")
    r = make(full_org)
    full_org.post("/plans/replan")
    resp = full_org.post(f"/report-schedules/{r['id']}/send")
    assert resp.status_code == 502 and "не настроен" in resp.json()["title"]


# ---------------------------------------------------------------- расписание (московское время)
def test_sent_once_per_day_after_msk_time(full_org, tg):
    o = full_org
    make(o, weekdays=[4], metrics=["order_statuses"])                          # TODAY — пятница
    assert run(at_msk("08:00")) == 0                                           # рано
    assert run(at_msk("12:00")) == 0                                           # окно (3 часа) закрыто
    assert run(at_msk("08:45")) == 1 and len(tg.sent) == 1 and tg.sent[0][0] == int(GROUP)
    assert run(at_msk("09:00")) == 0 and len(tg.sent) == 1                     # второй раз за день — нет
    assert o.get("/report-schedules").json()["items"][0]["last_status"] == "ok"


def test_time_is_moscow_not_utc(full_org, tg):
    make(full_org, send_time="08:30", metrics=["order_statuses"])
    utc_0830 = datetime(TODAY.year, TODAY.month, TODAY.day, 8, 45, tzinfo=timezone.utc)      # 11:45 по Москве — вне окна 08:30–11:30
    assert run(utc_0830) == 0
    assert run(datetime(TODAY.year, TODAY.month, TODAY.day, 5, 45, tzinfo=timezone.utc)) == 1   # 08:45 по Москве


def test_weekday_filter_disabled_and_window_over_midnight(full_org, tg):
    o = full_org
    a = make(o, name="Пн", weekdays=[0], metrics=["order_statuses"])
    assert run(at_msk("08:45")) == 0                                           # пятница — не в списке дней
    o.patch(f"/report-schedules/{a['id']}", json={"weekdays": [4], "enabled": False})
    assert run(at_msk("08:45")) == 0                                           # выключен
    o.patch(f"/report-schedules/{a['id']}", json={"enabled": True, "send_time": "23:30"})
    assert run(at_msk("00:30", TODAY + timedelta(days=1))) == 1                # 00:30 субботы — это ещё окно пятничной отправки


def test_two_reports_to_two_groups(full_org, tg):
    o = full_org
    make(o, name="А", metrics=["order_statuses"]); make(o, name="Б", chat_ref="-1009999999999", metrics=["workers"], section_ids=[o.wcs[0]["id"]])
    assert run(at_msk("08:45")) == 2
    assert {c for c, _ in tg.sent} == {int(GROUP), -1009999999999}


def test_temporary_failure_is_retried_permanent_is_not(full_org, tg):
    o = full_org
    r = make(o, metrics=["order_statuses"])
    tg.fail = httpx.ConnectError("boom")
    assert run(at_msk("08:45")) == 0
    item = o.get("/report-schedules").json()["items"][0]
    assert item["last_status"] == "error" and "повторена" in item["last_error"]
    assert run(at_msk("08:46")) == 0                                           # сразу повторять не будем (раз в 5 минут)
    s = dbm.admin_session(); s.info["write"] = True
    s.get(ReportSchedule, r["id"]).last_attempt_at = utcnow() - timedelta(minutes=10); s.commit(); s.close()
    tg.fail = None
    assert run(at_msk("08:55")) == 1 and len(tg.sent) == 1                     # сеть вернулась — отчёт ушёл, один раз
    assert run(at_msk("09:05")) == 0
    # постоянная ошибка: бота нет в группе — не повторяем
    o.patch(f"/report-schedules/{r['id']}", json={"send_time": "09:00"})
    tg.fail = RuntimeError("telegram sendMessage: 403 Forbidden: bot was kicked")
    n = len(tg.sent)
    assert run(at_msk("09:10", TODAY)) == 0
    tg.fail = None
    s = dbm.admin_session(); s.info["write"] = True
    s.get(ReportSchedule, r["id"]).last_attempt_at = utcnow() - timedelta(minutes=10); s.commit(); s.close()
    assert run(at_msk("09:20")) == 0 and len(tg.sent) == n


def test_deleted_sections_do_not_send(full_org, tg):
    o = full_org
    extra = o.post("/work-centers", json={"name": "Временный"}).json()
    make(o, section_ids=[extra["id"]], metrics=["workers"])
    assert o.delete(f"/work-centers/{extra['id']}").status_code in (200, 204)
    assert run(at_msk("08:45")) == 0 and not tg.sent
    assert "удалены" in o.get("/report-schedules").json()["items"][0]["last_error"]


# ---------------------------------------------------------------- бот в группе
def group_update(n, chat_type, text):
    out = []
    s = dbm.admin_session(); s.info["write"] = True
    try:
        bot.handle_update(s, {"update_id": n, "message": {"chat": {"id": -100555, "type": chat_type}, "text": text}}, out)
        s.commit()
    finally:
        s.close()
    return out


def test_chatid_command_in_group(full_org, tg):
    for n, (t, txt) in enumerate([("supergroup", "/chatid"), ("group", "/chatid@plan24_test_bot")], start=900001):
        out = group_update(n, t, txt)
        assert out and out[0]["chat_id"] == -100555 and "-100555" in out[0]["text"]
    assert not group_update(900010, "supergroup", "привет")                    # прочее в группах игнорируется
    assert not any("Номер этой группы" in m.get("text", "") for m in group_update(900011, "private", "/chatid"))
