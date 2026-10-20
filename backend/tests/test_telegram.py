"""Telegram-бот: безопасность вебхука, привязка по коду, задания, ввод факта, права, напоминания."""
import itertools
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import Org, TODAY
from plan24 import config, db as dbm
from plan24.models import Fact, Plan, TelegramLink, Tenant
from plan24.telegram import bot, client as tgclient, notifications
from test_planning import first_task

_ids = itertools.count(1000)


class FakeTG:
    def __init__(self):
        self.sent, self.answers, self.answer_texts, self.copies, self.commands = [], [], [], [], {}

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "markup": reply_markup, "kind": "message"})

    def edit_message(self, chat_id, message_id, text, reply_markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "markup": reply_markup, "kind": "edit", "message_id": message_id})

    def copy_message(self, chat_id, from_chat_id, message_id):
        self.copies.append((chat_id, from_chat_id, message_id))

    def set_commands(self, chat_id, commands):
        self.commands[chat_id] = commands

    def answer_callback(self, cid, text=None):
        self.answers.append(cid)
        self.answer_texts.append(text)


@pytest.fixture()
def tg(full_org):
    fake = FakeTG()
    tgclient.set_client(fake)
    full_org.fake = fake
    yield fake
    tgclient.set_client(None)


class Chat:
    """Один чат с ботом. Каждый вызов возвращает сообщения, которые бот прислал в ответ."""
    def __init__(self, org, chat_id):
        self.org, self.chat_id = org, chat_id

    def _post(self, upd, headers=None, secret=None):
        cfg = config.telegram_settings()
        h = {"X-Telegram-Bot-Api-Secret-Token": cfg["header_secret"]} if headers is None else headers
        n = len(self.org.fake.sent)
        r = self.org.c.post(f"/api/v1/integrations/telegram/webhook/{secret or cfg['secret']}", json=upd, headers=h)
        assert r.status_code == 200, (r.status_code, r.text)
        return [m for m in self.org.fake.sent[n:] if m["chat_id"] == self.chat_id]

    def say(self, text, **kw):
        return self._post({"update_id": kw.pop("update_id", next(_ids)),
                           "message": {"chat": {"id": self.chat_id, "type": "private"}, "text": text}}, **kw)

    def press(self, data, **kw):
        return self._post({"update_id": kw.pop("update_id", next(_ids)),
                           "callback_query": {"id": f"cb{next(_ids)}", "data": data,
                                              "message": {"message_id": kw.pop("mid", 50000 + next(_ids)),
                                                          "chat": {"id": self.chat_id, "type": "private"}}}}, **kw)

    def send(self, **fields):
        """Сообщение с произвольными полями (фото, подпись…)."""
        return self._post({"update_id": next(_ids), "message": {"message_id": next(_ids), "chat": {"id": self.chat_id, "type": "private"}, **fields}})


def buttons(msgs):
    return [b for m in msgs for row in (m["markup"] or {}).get("inline_keyboard", []) for b in row]


def find(msgs, prefix):
    return next(b["callback_data"] for b in buttons(msgs) if b["text"].startswith(prefix))


def toasts(tg):
    return " | ".join(t for t in tg.answer_texts if t)


def number_key(chat):
    """Кнопка «✏ Ввести число» из карточки первого задания."""
    card = chat.press(find(chat.say("/tasks"), "🔢 №1"))
    return find(card, "✏ Ввести число")


def text(msgs):
    return "\n".join(m["text"] for m in msgs)


def issue(org, **who):
    r = org.post("/telegram/codes", "manager", json=who)
    assert r.status_code == 201, r.text
    return r.json()["code"]


def worker_with_tasks(org):
    org.post("/plans/replan")
    _, w, t = first_task(org)
    return w["worker_id"], w["worker"]


def link_worker(org, chat_id=5001):
    wid, name = worker_with_tasks(org)
    chat = Chat(org, chat_id)
    reply = chat.say("/start " + issue(org, worker_id=wid))
    assert name in text(reply)
    return chat, wid, name


def facts_of(org, wid):
    return [f for f in org.get("/facts").json()["items"] if f["worker_id"] == wid]


# ---------------------------------------------------------------- безопасность вебхука
def test_webhook_secret_in_url_and_signature_header(full_org, tg):
    c = full_org.c
    upd = {"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "text": "/start"}}
    good = config.telegram_settings()
    assert c.post("/api/v1/integrations/telegram/webhook/wrong-secret", json=upd,
                  headers={"X-Telegram-Bot-Api-Secret-Token": good["header_secret"]}).status_code == 404
    url = f"/api/v1/integrations/telegram/webhook/{good['secret']}"
    assert c.post(url, json=upd).status_code == 403                                           # нет подписи
    assert c.post(url, json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "x" * 48}).status_code == 403
    assert c.post(url, json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": good["header_secret"]}).status_code == 200


def test_bot_disabled_without_token(full_org, monkeypatch):
    monkeypatch.delenv("PLAN24_TG_TOKEN")
    assert full_org.c.post("/api/v1/integrations/telegram/webhook/path-secret-xyz", json={}).status_code == 404
    assert full_org.post("/telegram/codes", "manager", json={"worker_id": 1}).status_code == 409
    assert full_org.get("/telegram/status").json()["configured"] is False


# ---------------------------------------------------------------- коды и привязка
def test_only_managers_issue_codes_for_workers_and_foremen(full_org, tg):
    o = full_org
    wid = o.workers[0]["id"]
    for role in ("worker", "viewer", "director"):
        assert o.post("/telegram/codes", role, json={"worker_id": wid}).status_code == 403
    # бригадир — только работникам своего участка (и не бригадирам/пользователям)
    own = next(w["id"] for w in o.workers if w["work_center_id"] == o.wcs[0]["id"])
    other = next(w["id"] for w in o.workers if w["work_center_id"] != o.wcs[0]["id"])
    assert o.post("/telegram/codes", "foreman", json={"worker_id": other}).status_code == 403
    assert o.post("/telegram/codes", "foreman", json={"worker_id": own}).status_code == 201
    assert o.post("/telegram/codes", "foreman", json={"user_id": 1}).status_code == 403
    r = o.post("/telegram/codes", "manager", json={"worker_id": wid}).json()
    assert len(r["code"]) == 9 and r["deep_link"].startswith("https://t.me/plan24_test_bot?start=") and "/start" in r["instruction"]
    assert o.post("/telegram/codes", "manager", json={}).status_code == 422
    assert o.post("/telegram/codes", "manager", json={"worker_id": wid, "user_id": 1}).status_code == 422
    viewer = next(u for u in o.get("/users").json() if u["role"] == "viewer")
    assert o.post("/telegram/codes", "owner", json={"user_id": viewer["id"]}).status_code == 422   # только исполнители и бригадиры
    assert o.post("/telegram/codes", "manager", json={"worker_id": 999999}).status_code == 404


def test_link_flow_code_is_one_time_and_stored_hashed(full_org, tg):
    o = full_org
    wid, name = worker_with_tasks(o)
    code = issue(o, worker_id=wid)
    chat = Chat(o, 5001)
    assert "Этот чат пока не привязан" in text(chat.say("привет"))
    reply = chat.say(f"/start {code}")
    assert name in text(reply) and "Пароль не нужен" in text(reply)
    s = dbm.admin_session()
    try:
        from plan24.models import TelegramCode
        assert code.replace("-", "") not in [c.code_hash for c in s.scalars(select(TelegramCode))]
    finally:
        s.close()
    other = Chat(o, 5002)
    assert "не подошёл" in text(other.say(f"/start {code}"))                    # код одноразовый
    st = o.get("/telegram/status", "manager").json()
    assert [l["name"] for l in st["links"]] == [name]


def test_wrong_codes_are_rate_limited(full_org, tg):
    chat = Chat(full_org, 7001)
    for i in range(5):
        assert "не подошёл" in text(chat.say(f"/start ABCD-EF{i}2"))
    assert "Слишком много" in text(chat.say("/start ABCD-EFGH"))


def test_expired_code_is_rejected(full_org, tg):
    o = full_org
    wid, _ = worker_with_tasks(o)
    code = issue(o, worker_id=wid)
    s = dbm.admin_session()
    try:
        from plan24.models import TelegramCode, utcnow
        s.scalars(select(TelegramCode)).first().expires_at = utcnow() - timedelta(seconds=1); s.commit()
    finally:
        s.close()
    assert "не подошёл" in text(Chat(o, 5001).say(f"/start {code}"))


def test_new_code_invalidates_previous_and_relink_replaces_old_chat(full_org, tg):
    o = full_org
    wid, name = worker_with_tasks(o)
    c1, c2 = issue(o, worker_id=wid), issue(o, worker_id=wid)
    assert "не подошёл" in text(Chat(o, 5001).say(f"/start {c1}"))             # первый код аннулирован вторым
    old_phone, new_phone = Chat(o, 5002), Chat(o, 5003)
    assert name in text(old_phone.say(f"/start {c2}"))
    assert name in text(new_phone.say(f"/start {issue(o, worker_id=wid)}"))   # новый телефон
    assert "не привязан" in text(old_phone.say("/tasks"))                      # старый чат потерял доступ
    assert "Задания на" in text(new_phone.say("/tasks"))


def test_linked_chat_cannot_rebind_to_another_person(full_org, tg):
    o = full_org
    chat, wid, name = link_worker(o)
    other = next(w for w in o.workers if w["id"] != wid)
    reply = chat.say("/start " + issue(o, worker_id=other["id"]))
    assert "уже привязан" in text(reply) and name in text(reply)
    assert [l["name"] for l in o.get("/telegram/status", "manager").json()["links"]] == [name]


def test_group_chats_are_ignored_and_unlink_revokes_access(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    n = len(tg.sent)
    o.c.post(f"/api/v1/integrations/telegram/webhook/{config.telegram_settings()['secret']}", headers={"X-Telegram-Bot-Api-Secret-Token": config.telegram_settings()["header_secret"]},
             json={"update_id": 9, "message": {"chat": {"id": -100, "type": "group"}, "text": "/tasks"}})
    assert len(tg.sent) == n
    lid = o.get("/telegram/status", "manager").json()["links"][0]["id"]
    assert o.delete(f"/telegram/links/{lid}", "worker").status_code == 403
    if next(w for w in o.workers if w["id"] == wid)["work_center_id"] != o.wcs[0]["id"]:
        assert o.delete(f"/telegram/links/{lid}", "foreman").status_code == 404       # привязка работника чужого участка бригадиру не видна
    assert o.delete(f"/telegram/links/{lid}", "manager").status_code == 204
    assert "не привязан" in text(chat.say("/tasks"))


# ---------------------------------------------------------------- задания и ввод факта
def test_tasks_show_only_own_work_with_buttons(full_org, tg):
    o = full_org
    chat, wid, name = link_worker(o)
    msgs = chat.say("/tasks")
    t = text(msgs)
    assert "Задания на 02.10 (пт)" in t and name in t and "план" in t
    mine = [x for x in o.get("/reports/day").json()["workers"] if x["worker_id"] == wid][0]["tasks"]
    assert len([l for l in t.splitlines() if l[:2].strip(". ").isdigit()]) == min(len(mine), bot.PAGE)
    assert find(msgs, "✔ №1 всё").startswith("d:") and find(msgs, "🔢 №1").startswith("k:")
    others = {w["worker"] for w in o.get("/reports/day").json()["workers"]} - {name}
    assert not any(n in t for n in others)                                         # чужих заданий нет


def test_done_button_saves_plan_qty_as_fact_via_telegram(full_org, tg):
    o = full_org
    chat, wid, name = link_worker(o)
    msgs = chat.say("/tasks")
    key = find(msgs, "✔ №1 всё")
    reply = chat.press(key)
    assert reply[0]["kind"] == "edit"                                               # список переписан на месте, а не новое сообщение
    assert "Записано" in toasts(tg) and find(reply, "↩ Отменить последнее").startswith("u:")
    f = facts_of(o, wid)
    assert len(f) == 1 and f[0]["source"] == "telegram" and f[0]["status"] == "confirmed"
    assert tg.answers                                                                # спиннер кнопки погашен
    day = [x for x in o.get("/reports/day").json()["workers"] if x["worker_id"] == wid][0]["tasks"]
    assert any(t["state"] == "done" for t in day)
    chat.press(key)                                                                  # повтор не суммирует
    assert len(facts_of(o, wid)) == 1 and facts_of(o, wid)[0]["qty"] == f[0]["qty"]


def test_custom_number_needs_confirmation_and_accepts_comma(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    key = number_key(chat)
    assert "Сколько сделано" in text(chat.press(key))
    assert "Нужно число" in text(chat.say("много"))
    assert "Нужно число" in text(chat.say("-3"))
    ask = chat.say("2,5")
    assert "Записать 2,5" in text(ask) and facts_of(o, wid) == []                     # пока ничего не записано
    done = chat.press("y")
    assert "Записано" in toasts(tg) and "Сделано: 2,5" in text(done) and facts_of(o, wid)[0]["qty"] == 2.5


def test_decline_confirmation_writes_nothing_and_cancel_works(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    key = number_key(chat)
    chat.press(key); chat.say("4")
    assert "ничего не записано" in text(chat.press("x")) and facts_of(o, wid) == []
    chat.press(key)
    assert text(chat.say("/cancel")) == "Отменено."
    assert "Не понял" in text(chat.say("5"))                                          # число без ожидания не принимается


def test_partial_entry_asks_for_reason_and_stores_it(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    msgs = chat.say("/tasks")
    plan = [t for t in [x for x in o.get("/reports/day").json()["workers"] if x["worker_id"] == wid][0]["tasks"] if t["plan_qty"]][0]["plan_qty"]
    chat.press(find(chat.press(find(msgs, "🔢 №1")), "✏ Ввести число")); chat.say("1")
    reply = chat.press("y")
    assert "Что помешало" in text(reply)
    rid = find(reply, "Нет материала")
    assert rid.startswith("r:")
    chat.press(rid)
    assert "Причина сохранена" in toasts(tg)
    reasons = {r["id"]: r["name"] for r in o.get("/fact-reasons").json()["items"]}
    assert reasons[facts_of(o, wid)[0]["reason_id"]] == "Нет материала"
    chat.press(find(reply, "Без причины"))
    assert "Хорошо, без причины" in toasts(tg)


def test_undo_restores_previous_value_or_removes_new_entry(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    key = find(chat.say("/tasks"), "✔ №1 всё")
    reply = chat.press(key)                                                           # новая запись
    chat.press(find(reply, "↩ Отменить"))
    assert "Ввод отменён" in toasts(tg)
    assert facts_of(o, wid) == []
    chat.press(key)                                                                   # снова записали план
    first = facts_of(o, wid)[0]["qty"]
    chat.press(number_key(chat)); chat.say("1"); chat.press("y")                      # изменили на 1
    assert facts_of(o, wid)[0]["qty"] == 1
    assert "Возвращено прежнее" in text(chat.say("/undo"))
    assert facts_of(o, wid)[0]["qty"] == first
    assert "Отменять нечего" in text(chat.say("/undo"))                               # возвращается только последний ввод


def test_crafted_callbacks_cannot_touch_foreign_or_nonexistent_tasks(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    day = o.get("/reports/day").json()["workers"]
    other = next(w for w in day if w["worker_id"] != wid)
    t = other["tasks"][0]
    forged = f"d:{t['order_line_id']}:{t['operation_id']}:{other['worker_id']}"
    assert "недоступно" in text(chat.press(forged))                                    # задача не из отчёта этого человека
    assert "недоступно" in text(chat.press("d:99999:1:1"))
    assert "недоступно" in text(chat.press("d:abc"))
    assert "Не понял" in text(chat.press("p:xyz"))
    assert facts_of(o, other["worker_id"]) == [] and facts_of(o, wid) == []


def test_duplicate_update_is_processed_once(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    key = find(chat.say("/tasks"), "✔ №1 всё")
    r1 = chat.press(key, update_id=777001)
    r2 = chat.press(key, update_id=777001)                                             # Telegram повторил доставку
    assert r1 and r2 == []
    assert sum(1 for a in o.get("/audit?limit=100").json()["items"] if a["entity"] == "fact") == 1


def test_unavailable_worker_keeps_his_tasks_but_deactivated_foreman_loses_access(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    o.patch(f"/workers/{wid}", json={"status": "unavailable"})
    assert "Задания на" in text(chat.say("/tasks"))                       # исполнитель вне плана по-прежнему может вносить факт
    me = next(u for u in o.get("/users").json() if u["role"] == "foreman")
    fchat = Chat(o, 6101)
    fchat.say("/start " + issue(o, user_id=me["id"]))
    assert "Участок" in text(fchat.say("/tasks"))
    o.patch(f"/users/{me['id']}", json={"is_active": False})
    assert "Доступ закрыт" in text(fchat.say("/tasks"))
    assert "Доступ закрыт" in text(fchat.press("l"))


def test_weekend_has_no_tasks(full_org, tg, monkeypatch):
    chat, wid, _ = link_worker(full_org)
    monkeypatch.setenv("PLAN24_TODAY", "2026-10-03")                                   # суббота
    assert "заданий нет" in text(chat.say("/tasks"))


# ---------------------------------------------------------------- бригадир
@pytest.fixture()
def foreman_chat(full_org, tg):
    o = full_org
    o.post("/plans/replan")
    me = next(u for u in o.get("/users").json() if u["role"] == "foreman")
    chat = Chat(o, 6001)
    assert me["name"] in text(chat.say("/start " + issue(o, user_id=me["id"])))
    return o, chat, me


def test_foreman_sees_center_tasks_and_enters_for_workers(foreman_chat):
    o, chat, me = foreman_chat
    msgs = chat.say("/tasks")
    t = text(msgs)
    center_names = {w["name"] for w in o.workers if w["work_center_id"] == me["work_center_id"]}
    other_names = {w["name"] for w in o.workers if w["work_center_id"] != me["work_center_id"]}
    assert "Участок" in t and any(n in t for n in center_names) and not any(n in t for n in other_names)      # экран «по людям»
    person = chat.press(find(msgs, "•") if any(b["text"].startswith("•") for b in buttons(msgs)) else buttons(msgs)[0]["callback_data"])
    reply = chat.press(find(person, "✔ №1 всё"))
    assert "Записано" in toasts(o.fake)
    fs = o.get("/facts").json()["items"]
    assert fs and fs[0]["source"] == "telegram" and fs[0]["status"] == "confirmed"


def test_foreman_cannot_enter_for_other_center_via_forged_callback(foreman_chat):
    o, chat, me = foreman_chat
    other = next(w for w in o.get("/reports/day").json()["workers"] if w["work_center_id"] != me["work_center_id"])
    t = other["tasks"][0]
    assert "недоступно" in text(chat.press(f"d:{t['order_line_id']}:{t['operation_id']}:{other['worker_id']}"))
    assert o.get("/facts").json()["items"] == []


def test_confirmation_queue_for_foreman(foreman_chat):
    o, chat, me = foreman_chat
    o.patch("/settings", json={"fact_confirmation": True})
    wid = next(w["id"] for w in o.workers if w["work_center_id"] == me["work_center_id"])
    worker_chat = Chat(o, 5100)
    tasks = [x for x in o.get("/reports/day").json()["workers"] if x["worker_id"] == wid]
    assert tasks, "у исполнителя участка бригадира нет заданий в демо-данных"
    worker_chat.say("/start " + issue(o, worker_id=wid))
    worker_chat.press(find(worker_chat.say("/tasks"), "✔ №1 всё"))
    assert "Бригадир подтвердит" in toasts(o.fake)
    assert o.get("/facts").json()["items"][0]["status"] == "submitted"
    q = chat.say("/queue")
    assert "Ждёт подтверждения" in text(q)
    assert "Подтверждено" in text(chat.press(find(q, "✔ Подтвердить все")))
    assert o.get("/facts").json()["items"][0]["status"] == "confirmed"
    assert "нет" in text(chat.say("/queue"))
    assert "Подтверждать факт может бригадир" in text(worker_chat.press("c:all"))


# ---------------------------------------------------------------- напоминания
def at_local(hhmm, tz="Europe/Moscow", day=TODAY):
    h, m = map(int, hhmm.split(":"))
    from zoneinfo import ZoneInfo
    return datetime(day.year, day.month, day.day, h, m, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def run_notifications(when):
    s = dbm.admin_session(); s.info["write"] = True
    try:
        out = notifications.run_due(s, when); s.commit()
    finally:
        s.close()
    tgclient.deliver(out)
    return out


def test_morning_digest_once_per_day_only_after_set_time(full_org, tg):
    o = full_org
    chat, wid, name = link_worker(o)
    assert run_notifications(at_local("07:00")) == []                                  # рано
    assert run_notifications(at_local("11:00")) == []                                  # окно утра (3 ч) закрыто
    out = run_notifications(at_local("07:45"))
    assert len(out) == 1 and "Доброе утро" in out[0]["text"] and name in out[0]["text"] and out[0]["chat_id"] == chat.chat_id
    assert run_notifications(at_local("08:00")) == []                                  # второй раз за день — нет
    assert tg.sent[-1]["text"].startswith("Доброе утро")


def test_evening_reminder_only_without_fact(full_org, tg):
    o = full_org
    chat, wid, name = link_worker(o)
    out = run_notifications(at_local("17:40"))
    assert len(out) == 1 and "не забудьте внести факт" in out[0]["text"]
    # все задания закрыты — напоминать не нужно
    tasks = [t for x in o.get("/reports/day").json()["workers"] if x["worker_id"] == wid for t in x["tasks"] if t["plan_qty"]]
    for t in tasks:
        o.post("/facts", json={"fact_date": TODAY.isoformat(), "order_line_id": t["order_line_id"],
                               "operation_id": t["operation_id"], "worker_id": wid, "qty": t["plan_qty"]})
    s = dbm.admin_session()
    try:
        from plan24.models import NotificationLog
        for n in s.scalars(select(NotificationLog)): s.delete(n)
        s.commit()
    finally:
        s.close()
    assert run_notifications(at_local("17:50")) == []


def test_foreman_evening_reminder_lists_workers_without_fact(foreman_chat):
    o, chat, me = foreman_chat
    out = run_notifications(at_local("17:35"))
    mine = [m for m in out if m["chat_id"] == chat.chat_id]
    assert len(mine) == 1 and "Факт ещё не внесён" in mine[0]["text"]


def test_notifications_respect_each_tenants_timezone(full_org, tg):
    o = full_org
    link_worker(o)
    s = dbm.admin_session()
    try:
        s.scalars(select(Tenant)).first().timezone = "Asia/Vladivostok"; s.commit()          # UTC+10
    finally:
        s.close()
    assert run_notifications(at_local("07:45", "Europe/Moscow")) == []                       # у них уже 14:45 — окно утра закрыто
    assert len(run_notifications(at_local("07:45", "Asia/Vladivostok"))) == 1


def test_no_notification_without_plan_for_today(full_org, tg):
    o = full_org
    link_worker(o)
    assert run_notifications(at_local("07:45", day=TODAY + timedelta(days=1))) == []         # суббота: заданий нет


# ---------------------------------------------------------------- устойчивость и приватность
def test_delivery_failure_does_not_break_webhook_and_is_logged_without_content(full_org, tg, caplog):
    o = full_org

    class Down(FakeTG):
        def send_message(self, *a, **k):
            raise ConnectionError("telegram unreachable")
    tgclient.set_client(Down())
    with caplog.at_level(logging.ERROR, logger="plan24.telegram"):
        r = o.c.post(f"/api/v1/integrations/telegram/webhook/{config.telegram_settings()['secret']}",
                     headers={"X-Telegram-Bot-Api-Secret-Token": config.telegram_settings()["header_secret"]},
                     json={"update_id": 1, "message": {"chat": {"id": 4242, "type": "private"}, "text": "/start SECRET-CODE"}})
    assert r.status_code == 200
    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert "ConnectionError" in logged and "SECRET-CODE" not in logged and "4242" not in logged


def test_handler_exception_is_contained_and_user_gets_apology(full_org, tg, monkeypatch):
    chat, wid, _ = link_worker(full_org)
    monkeypatch.setattr(bot, "_open", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert "Что-то пошло не так" in text(chat.say("/tasks"))
    monkeypatch.undo()


def test_cross_tenant_chat_cannot_reach_other_organizations_data(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    other = Org(o.c, "z@z.ru", "Чужой цех"); other.seed("windows"); other.post("/plans/replan")
    other.fake = tg
    foreign = other.get("/reports/day").json()["workers"][0]
    t = foreign["tasks"][0]
    assert "недоступно" in text(chat.press(f"d:{t['order_line_id']}:{t['operation_id']}:{foreign['worker_id']}"))
    assert other.get("/facts").json()["items"] == []


# ---------------------------------------------------------------- HTTP-клиент Bot API (без сети)
def test_http_client_builds_correct_bot_api_requests():
    import httpx, json
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"ok": True, "result": True})
    c = tgclient.TelegramClient("123:ABC", transport=httpx.MockTransport(handler))
    c.send_message(5, "привет", {"inline_keyboard": []})
    c.answer_callback("cb1", "ок")
    c.set_webhook("https://x.test/hook", "sig")
    assert calls[0] == ("/bot123:ABC/sendMessage", {"chat_id": 5, "text": "привет", "reply_markup": {"inline_keyboard": []}})
    assert calls[1][0].endswith("/answerCallbackQuery") and calls[1][1]["callback_query_id"] == "cb1"
    assert calls[2][1]["secret_token"] == "sig" and calls[2][1]["allowed_updates"] == ["message", "callback_query"]
    bad = tgclient.TelegramClient("t", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": False, "error_code": 403})))
    with pytest.raises(RuntimeError):
        bad.send_message(1, "x")


# ---------------------------------------------------------------- доступ к Bot API с российских серверов
def test_api_base_and_proxy_are_configurable(monkeypatch):
    import httpx
    seen = []
    c = tgclient.TelegramClient("1:T", api_base="https://relay.example.com/", transport=httpx.MockTransport(
        lambda r: (seen.append(str(r.url)), httpx.Response(200, json={"ok": True, "result": True}))[1]))
    c.send_message(1, "x")
    assert seen == ["https://relay.example.com/bot1:T/sendMessage"]
    monkeypatch.setenv("PLAN24_TG_API_BASE", "https://tg-relay.test"); monkeypatch.setenv("PLAN24_TG_PROXY", "http://127.0.0.1:3128")
    cfg = config.telegram_settings()
    assert cfg["api_base"] == "https://tg-relay.test" and cfg["proxy"] == "http://127.0.0.1:3128"
    tgclient.TelegramClient("1:T", proxy=cfg["proxy"])                      # клиент создаётся с прокси без ошибок
    monkeypatch.setenv("PLAN24_TG_PROXY", "socks5://127.0.0.1:1080")
    tgclient.TelegramClient("1:T", proxy=config.telegram_settings()["proxy"])   # и с socks5 (зависимость httpx[socks])
    monkeypatch.delenv("PLAN24_TG_API_BASE"); monkeypatch.delenv("PLAN24_TG_PROXY")
    assert config.telegram_settings()["api_base"] == "https://api.telegram.org"


def test_long_polling_request_timeout_exceeds_server_wait():
    """Регрессия: getUpdates держится 25 с, клиентский таймаут 10 с обрывал бы каждый опрос."""
    import httpx
    t = {}

    def handler(req):
        t["timeout"] = req.extensions["timeout"]
        return httpx.Response(200, json={"ok": True, "result": []})
    c = tgclient.TelegramClient("1:T", transport=httpx.MockTransport(handler))
    c.get_updates(0, timeout=25)
    assert t["timeout"]["read"] >= 35
    c.send_message(1, "x")
    assert t["timeout"]["read"] == 10                                         # обычные вызовы по-прежнему короткие


def test_polling_loop_survives_network_failure_and_bad_update(full_org, tg, monkeypatch):
    from plan24.telegram import __main__ as cli
    calls = {"n": 0}

    class Flaky(FakeTG):
        deleted = 0

        def delete_webhook(self):
            Flaky.deleted += 1

        def get_updates(self, offset, timeout=25):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("telegram unreachable")                 # сеть до Telegram недоступна
            if calls["n"] == 2:
                return [{"update_id": 1, "message": {"chat": {"id": 7, "type": "private"}, "text": "/start"}}]
            raise KeyboardInterrupt                                           # остановить цикл в тесте
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    engine_before = dbm.get_engine()
    tgclient.set_client(Flaky())
    with pytest.raises(KeyboardInterrupt):
        cli.main(["x", "poll"])
    assert calls["n"] == 3                                                    # упал, повторил, обработал, пошёл дальше
    assert dbm.get_engine() is engine_before                                  # движок БД не пересоздавался на каждом обновлении
    assert Flaky.deleted == 1                                                 # перед опросом снят вебхук
