"""Telegram для бригадира и сборщика: меню, карточка задания с быстрым вводом, «закрыть день», «по людям», напоминания,
оповещения об отклонениях и изменениях плана, проблема (с фото), отсутствие и перепланирование из бота, настройки."""
from datetime import timedelta

import pytest

from conftest import TODAY
from plan24 import db as dbm
from test_telegram import (Chat, at_local, buttons, facts_of, find, issue, link_worker, run_notifications, text, tg, toasts,  # noqa: F401
                           worker_with_tasks)


def sent_to(tg, chat_id, since=0):
    return [m for m in tg.sent[since:] if m["chat_id"] == chat_id]


def task_key(w, min_plan=1):
    t = next(t for t in w["tasks"] if t["plan_qty"] >= min_plan)
    return f"{t['order_line_id']}:{t['operation_id']}:{w['worker_id']}", t


def team(o):
    """Бригадир и работник его участка с заданиями на сегодня, оба привязаны к боту."""
    o.post("/plans/replan")
    me = next(u for u in o.get("/users").json() if u["role"] == "foreman")
    fchat = Chat(o, 6001)
    fchat.say("/start " + issue(o, user_id=me["id"]))
    day = o.get("/reports/day").json()["workers"]
    w = next(x for x in day if x["work_center_id"] == me["work_center_id"] and any(t["plan_qty"] >= 2 for t in x["tasks"]))
    wchat = Chat(o, 5100)
    wchat.say("/start " + issue(o, worker_id=w["worker_id"]))
    return fchat, wchat, w, me


# ------------------------------------------------------------------ меню
def test_link_sends_persistent_menu_and_role_commands(full_org, tg):
    o = full_org
    wid, name = worker_with_tasks(o)
    chat = Chat(o, 5001)
    reply = chat.say("/start " + issue(o, worker_id=wid))
    kb = reply[0]["markup"]
    assert kb["is_persistent"] and kb["resize_keyboard"]
    labels = [b["text"] for row in kb["keyboard"] for b in row]
    assert {"📋 Задания", "✅ Закрыть день", "⚠ Проблема", "🤒 Не выйду", "📅 Завтра", "⚙ Настройки"} <= set(labels)
    assert {c for c, _ in tg.commands[5001]} >= {"tasks", "closeday", "problem", "absent", "tomorrow", "undo", "settings", "help"}
    me = next(u for u in o.get("/users").json() if u["role"] == "foreman")
    f = Chat(o, 6001)
    fkb = f.say("/start " + issue(o, user_id=me["id"]))[0]["markup"]["keyboard"]
    assert {"👥 По людям", "📥 Очередь", "🤒 Отсутствие"} <= {b["text"] for row in fkb for b in row}
    assert "queue" in {c for c, _ in tg.commands[6001]}


def test_menu_buttons_work_as_commands_and_unknown_text_shows_menu(full_org, tg):
    chat, wid, name = link_worker(full_org)
    assert "Задания на" in text(chat.say("📋 Задания"))
    assert "Внести по плану" in text(chat.say("✅ Закрыть день"))
    assert "Что случилось" in text(chat.say("⚠ Проблема"))
    assert "Когда вы не сможете" in text(chat.say("🤒 Не выйду"))
    assert "Настройки уведомлений" in text(chat.say("⚙ Настройки"))
    r = chat.say("абракадабра")
    assert "Не понял" in text(r) and "keyboard" in r[0]["markup"]                       # меню возвращается
    assert "Фото и файлы" in text(chat.send(photo=[{"file_id": "x"}]))


# ------------------------------------------------------------------ карточка и быстрый ввод
def test_card_quick_buttons_accumulate_and_screen_is_edited_in_place(full_org, tg):
    o = full_org
    chat, wid, name = link_worker(full_org)
    w = next(x for x in o.get("/reports/day").json()["workers"] if x["worker_id"] == wid)
    key, t = task_key(w, 2)
    card = chat.press(f"k:{key}", mid=900)
    assert card[0]["kind"] == "edit" and card[0]["message_id"] == 900
    assert {"＋1", "＋5", "＋10"} <= {b["text"] for b in buttons(card)}
    chat.press(f"a:{key}:1", mid=900)
    r = chat.press(f"a:{key}:1", mid=900)
    assert facts_of(o, wid)[0]["qty"] == 2 and "Сделано: 2" in text(r) and r[0]["message_id"] == 900
    assert "Что помешало" in text(r) or t["plan_qty"] <= 2                              # меньше плана — карточка спрашивает причину
    r = chat.press(f"z:{key}", mid=900)
    assert facts_of(o, wid)[0]["qty"] == 0
    chat.press(f"d:{key}", mid=900)
    assert facts_of(o, wid)[0]["qty"] == t["plan_qty"]


def test_increment_is_capped_and_undo_from_card(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    key, t = task_key(next(x for x in o.get("/reports/day").json()["workers"] if x["worker_id"] == wid))
    chat.press(f"a:{key}:5")
    card = chat.press(f"a:{key}:10")
    assert facts_of(o, wid)[0]["qty"] == 15
    chat.press(find(card, "↩ Отменить последнее"))
    assert facts_of(o, wid)[0]["qty"] == 5 and "Возвращено прежнее" in toasts(tg)


# ------------------------------------------------------------------ закрыть день по плану
def test_close_day_enters_plan_only_for_tasks_without_fact(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    w = next(x for x in o.get("/reports/day").json()["workers"] if x["worker_id"] == wid)
    planned = [t for t in w["tasks"] if t["plan_qty"]]
    key, first = f"{planned[0]['order_line_id']}:{planned[0]['operation_id']}:{wid}", planned[0]
    chat.press(f"a:{key}:1")                                                          # по первому факт уже есть (1)
    ask = chat.say("✅ Закрыть день")
    assert f"{len(planned) - 1}" in text(ask) or len(planned) == 1
    if len(planned) == 1:
        assert "Заданий без факта нет" in text(ask)
        return
    chat.press(find(ask, "✔ Да, внести"))
    fs = {(f["order_line_id"], f["operation_id"]): f["qty"] for f in facts_of(o, wid)}
    assert fs[(first["order_line_id"], first["operation_id"])] == 1                      # уже внесённое не перезаписано
    for t in planned[1:]:
        assert fs[(t["order_line_id"], t["operation_id"])] == t["plan_qty"]
    assert "Заданий без факта нет" in text(chat.say("✅ Закрыть день"))


def test_foreman_people_screen_and_close_day_for_one_worker_or_all(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    people = fchat.say("👥 По людям")
    assert "Участок" in text(people) and w["worker"] in text(people)
    wcb = next(b["callback_data"] for b in buttons(people) if b["callback_data"] == f"w:{w['worker_id']}")
    lst = fchat.press(wcb)
    assert w["worker"] in text(lst) and any(b["callback_data"] == f"cd:{w['worker_id']}" for b in buttons(lst))
    ask = fchat.press(f"cd:{w['worker_id']}")
    assert w["worker"] in text(ask)
    fchat.press(f"cdy:{w['worker_id']}")
    assert all(f["source"] == "telegram" for f in facts_of(o, w["worker_id"])) and facts_of(o, w["worker_id"])
    # «за всех» — только свой участок
    fchat.press("cdy:0")
    mine = {x["worker_id"] for x in o.get("/reports/day").json()["workers"] if x["work_center_id"] == me["work_center_id"]}
    foreign = {f["worker_id"] for f in o.get("/facts").json()["items"]} - mine
    assert not foreign


def test_foreman_remind_sends_to_workers_and_is_throttled(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    n = len(tg.sent)
    people = fchat.say("/tasks")
    r = fchat.press("rem")
    assert "Напоминание отправлено" in text(r)
    got = sent_to(tg, wchat.chat_id, n)
    assert got and "внесите, пожалуйста, факт" in got[-1]["text"] and any(b["callback_data"] == "cd:0" for b in buttons(got))
    fchat.press("rem")
    assert "подождите" in toasts(tg)                                                    # не чаще раза в час


def test_evening_messages_have_action_buttons(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    out = run_notifications(at_local("17:40"))
    for_f = [m for m in out if m["chat_id"] == fchat.chat_id]
    for_w = [m for m in out if m["chat_id"] == wchat.chat_id]
    assert for_f and {"rem", "cd:0"} <= {b.get("callback_data") for m in for_f for row in m["markup"]["inline_keyboard"] for b in row}
    assert for_w and "cd:0" in {b.get("callback_data") for m in for_w for row in m["markup"]["inline_keyboard"] for b in row}


# ------------------------------------------------------------------ отклонения → бригадиру
def test_shortfall_with_reason_alerts_foreman_once(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    key, t = task_key(w, 2)
    n = len(tg.sent)
    wchat.press(f"k:{key}")
    card = wchat.press(f"a:{key}:1")
    assert not sent_to(tg, fchat.chat_id, n)                                            # пока причина не выбрана — тишина
    rid = find(card, "Нет материала")
    wchat.press(rid)
    alert = sent_to(tg, fchat.chat_id, n)
    assert len(alert) == 1 and "Отклонение" in alert[0]["text"] and w["worker"] in alert[0]["text"] and "Нет материала" in alert[0]["text"]
    assert f"из {int(t['plan_qty'])}" in alert[0]["text"] or "из" in alert[0]["text"]
    wchat.press(rid)
    assert len(sent_to(tg, fchat.chat_id, n)) == 1                                      # повтор не дублирует
    # «без причины» тоже сообщает — с пометкой
    key2, _ = task_key(w, 1)
    other = [x for x in w["tasks"] if x["plan_qty"] >= 2 and f"{x['order_line_id']}:{x['operation_id']}:{w['worker_id']}" != key]
    if other:
        k2 = f"{other[0]['order_line_id']}:{other[0]['operation_id']}:{w['worker_id']}"
        wchat.press(f"k:{k2}")
        c2 = wchat.press(f"a:{k2}:1")
        wchat.press(find(c2, "Без причины"))
        assert "не указана" in sent_to(tg, fchat.chat_id, n)[-1]["text"]


def test_web_shortfall_alerts_foreman_and_foreman_own_entry_does_not(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    key, t = task_key(w, 2)
    n = len(tg.sent)
    body = {"fact_date": TODAY.isoformat(), "order_line_id": t["order_line_id"], "operation_id": t["operation_id"],
            "worker_id": w["worker_id"], "qty": 1, "note": "не хватило деталей"}
    assert o.post("/facts", json=body).status_code == 200
    alert = sent_to(tg, fchat.chat_id, n)
    assert len(alert) == 1 and "не хватило деталей" in alert[0]["text"]
    n = len(tg.sent)
    assert o.post("/facts", "foreman", json={**body, "qty": 0, "note": "сам внёс"}).status_code == 200
    assert not sent_to(tg, fchat.chat_id, n)                                            # бригадир о своём вводе не оповещается


def test_alerts_can_be_switched_off_in_settings(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    fchat.press("s:al")
    assert "Оповещения: выкл" in text(fchat.say("⚙ Настройки"))
    key, t = task_key(w, 2)
    n = len(tg.sent)
    o.post("/facts", json={"fact_date": TODAY.isoformat(), "order_line_id": t["order_line_id"], "operation_id": t["operation_id"],
                           "worker_id": w["worker_id"], "qty": 1, "note": "x"})
    assert not sent_to(tg, fchat.chat_id, n)


# ------------------------------------------------------------------ изменение плана → работнику
def test_plan_edit_in_web_notifies_the_worker(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    key, t = task_key(w, 1)
    n = len(tg.sent)
    r = o.post("/plan-day/items", json={"plan_date": TODAY.isoformat(), "worker_id": w["worker_id"], "order_line_id": t["order_line_id"],
                                        "operation_id": t["operation_id"], "qty": 1})
    assert r.status_code == 201, r.text
    got = sent_to(tg, wchat.chat_id, n)
    assert len(got) == 1 and "изменил ваш план" in got[0]["text"] and "→" in got[0]["text"]
    assert any(b["callback_data"] == "l" for b in buttons(got))
    n = len(tg.sent)
    assert o.delete(f"/plan-day/items/{r.json()['item']['id']}").status_code == 204
    assert sent_to(tg, wchat.chat_id, n)                                                  # возврат к прежнему тоже сообщается
    # через несколько дней — не беспокоим
    n = len(tg.sent)
    o.post("/plan-day/items", json={"plan_date": (TODAY + timedelta(days=6)).isoformat(), "worker_id": w["worker_id"],
                                    "order_line_id": t["order_line_id"], "operation_id": t["operation_id"], "qty": 1})
    assert not sent_to(tg, wchat.chat_id, n)


def test_worker_with_alerts_off_gets_no_plan_changes(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    wchat.press("s:al")
    key, t = task_key(w, 1)
    n = len(tg.sent)
    o.post("/plan-day/items", json={"plan_date": TODAY.isoformat(), "worker_id": w["worker_id"], "order_line_id": t["order_line_id"],
                                    "operation_id": t["operation_id"], "qty": 1})
    assert not sent_to(tg, wchat.chat_id, n)


# ------------------------------------------------------------------ проблема
def test_problem_with_text_and_photo_goes_to_foreman(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    n = len(tg.sent)
    assert "Что случилось" in text(wchat.say("⚠ Проблема"))
    assert "Опишите коротко" in text(wchat.press("pb:mat"))
    done = wchat.send(photo=[{"file_id": "p1"}], caption="ленты нет на складе")
    assert "Передал бригадиру" in text(done)
    got = sent_to(tg, fchat.chat_id, n)
    assert len(got) == 1 and "Нет материала" in got[0]["text"] and w["worker"] in got[0]["text"] and "ленты нет" in got[0]["text"]
    assert {"ak:%d" % w["worker_id"], "w:%d" % w["worker_id"]} <= {b["callback_data"] for b in buttons(got)}
    assert tg.copies and tg.copies[-1][0] == fchat.chat_id and tg.copies[-1][1] == wchat.chat_id          # фото переслано
    assert any(a["entity"] == "problem" for a in o.get("/audit?limit=50").json()["items"])
    # бригадир нажимает «Принял» — работник узнаёт
    n = len(tg.sent)
    fchat.press("ak:%d" % w["worker_id"])
    assert "принял" in text(sent_to(tg, wchat.chat_id, n))


def test_problem_without_details_and_cancel_and_no_foreman_linked(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)                                                      # бригадир к боту не привязан
    chat.say("⚠ Проблема"); chat.press("pb:bad")
    assert "не подключён к Telegram" in text(chat.press("pn"))
    chat.say("⚠ Проблема"); chat.press("pb:tool")
    assert "Отменено" in text(chat.press("pc"))
    assert "Не понял" in text(chat.say("просто текст"))                                 # после отмены текст уже не уходит бригадиру


def test_problem_flow_is_cancelled_by_menu_button(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    wchat.say("⚠ Проблема"); wchat.press("pb:oth")
    n = len(tg.sent)
    assert "Задания на" in text(wchat.say("📋 Задания"))                                 # меню прерывает ввод
    wchat.say("это уже не проблема")
    assert not sent_to(tg, fchat.chat_id, n)


# ------------------------------------------------------------------ отсутствие и перепланирование
def test_worker_reports_absence_foreman_replans_from_bot(full_org, tg):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    fchat, wchat, w, me = team(o)
    n = len(tg.sent)
    assert "Когда вы не сможете" in text(wchat.say("🤒 Не выйду"))
    assert "Причина" in text(wchat.press("ab:t"))
    done = wchat.press("abr:t:ill")
    assert "Бригадир предупреждён" in text(done)
    ab = [a for a in o.get("/absences").json()["items"] if a["worker_id"] == w["worker_id"]]
    assert ab and ab[0]["date_from"] == TODAY.isoformat() and ab[0]["reason"] == "Болезнь"
    note = sent_to(tg, fchat.chat_id, n)
    assert len(note) == 1 and w["worker"] in note[0]["text"] and "rp" in {b["callback_data"] for b in buttons(note)}
    n = len(tg.sent)
    res = fchat.press("rp")
    assert "План участка пересчитан" in text(res)
    day = [x for x in o.get("/reports/day").json()["workers"] if x["worker_id"] == w["worker_id"]]
    assert not [t for x in day for t in x["tasks"] if t["plan_qty"]]                    # заболевшему задания на сегодня больше не ставятся
    assert "изменил ваш план" in text(sent_to(tg, wchat.chat_id, n)) or "−" in text(sent_to(tg, wchat.chat_id, n))


def test_foreman_marks_absence_of_own_worker_only(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    n = len(tg.sent)
    pick = fchat.say("🤒 Отсутствие")
    assert f"fa:{w['worker_id']}" in {b["callback_data"] for b in buttons(pick)}
    foreign = next(x for x in o.workers if x["work_center_id"] != me["work_center_id"])
    assert f"fa:{foreign['id']}" not in {b["callback_data"] for b in buttons(pick)}
    fchat.press(f"fa:{w['worker_id']}"); fchat.press(f"fp:{w['worker_id']}:m")
    r = fchat.press(f"fr:{w['worker_id']}:m:vac")
    assert "Отмечено" in text(r) and "rp" in {b["callback_data"] for b in buttons(r)}
    ab = [a for a in o.get("/absences").json()["items"] if a["worker_id"] == w["worker_id"]]
    assert ab and ab[0]["date_from"] == (TODAY + timedelta(days=1)).isoformat() and ab[0]["reason"] == "Отпуск"
    assert "отметил ваше отсутствие" in text(sent_to(tg, wchat.chat_id, n))
    # чужой работник по подделанной кнопке — отказ, записи нет
    before = len(o.get("/absences").json()["items"])
    fchat.press(f"fa:{foreign['id']}")
    assert "другого участка" in toasts(tg)
    fchat.press(f"fr:{foreign['id']}:t:ill")
    assert "Нет доступа" in toasts(tg)
    assert len(o.get("/absences").json()["items"]) == before


# ------------------------------------------------------------------ настройки и расписание
def test_settings_shift_time_and_scheduler_uses_personal_times(full_org, tg):
    o = full_org
    chat, wid, name = link_worker(o)
    assert "07:30" in text(chat.say("⚙ Настройки"))
    assert "08:00" in text(chat.press("s:m+"))                                           # утро сдвинулось на полчаса
    out = run_notifications(at_local("07:45"))
    assert out == []                                                                    # личное время 08:00 ещё не наступило
    out = run_notifications(at_local("08:10"))
    assert len(out) == 1 and "Доброе утро" in out[0]["text"]
    for _ in range(40):                                                                 # границы: не раньше 05:00
        chat.press("s:m-")
    assert "05:00" in text(chat.say("⚙ Настройки"))


def test_settings_toggle_morning_off(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    assert "Утро: выкл" in text(chat.press("s:mt"))
    assert run_notifications(at_local("07:45")) == []
    chat.press("s:et")
    assert run_notifications(at_local("17:40")) == []


# ------------------------------------------------------------------ завтра
def test_tomorrow_skips_weekend_to_next_planned_day(full_org, tg):
    o = full_org
    chat, wid, _ = link_worker(o)
    t = text(chat.say("📅 Завтра"))
    assert "заданий нет" in t and "Ближайший день с заданиями" in t and "05.10 (пн)" in t


# ------------------------------------------------------------------ безопасность кнопок
def test_forged_foreman_buttons_do_nothing_for_workers_and_other_centers(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    n_abs = len(o.get("/absences").json()["items"])
    for data in ("rp", "rem", "w:1", "fa:1", "fp:1:t", "fr:1:t:ill", "ak:1", "cdy:99999", "abr:zz:ill", "ab:zz", "pb:zz", "s:zz", "k:1:2:3"):
        wchat.press(data)                                                               # ни одна не должна падать или менять данные
    assert len(o.get("/absences").json()["items"]) == n_abs
    foreign = next(x for x in o.get("/reports/day").json()["workers"] if x["work_center_id"] != me["work_center_id"])
    before = len(o.get("/absences").json()["items"])
    fchat.press(f"fa:{foreign['worker_id']}")
    assert "другого участка" in toasts(tg)
    fchat.press(f"fr:{foreign['worker_id']}:t:ill")
    assert len(o.get("/absences").json()["items"]) == before
    assert not any(f["worker_id"] == foreign["worker_id"] for f in o.get("/facts").json()["items"])
