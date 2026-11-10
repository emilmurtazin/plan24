"""Проблемы работников дублируются в группы Telegram: настройка администратором и отправка в момент создания проблемы."""
import pytest

from conftest import Org
from test_telegram import Chat, text, tg  # noqa: F401
from test_telegram_ux import sent_to, team

G1, G2 = "-1001111111111", "-1002222222222"


def add(o, **kw):
    r = o.post("/problem-routes", json={"name": "Диспетчер", "chat_ref": G1, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def report_problem(wchat, comment="ленты нет на складе"):
    wchat.say("⚠ Проблема"); wchat.press("pb:mat")
    return wchat.send(photo=[{"file_id": "p1"}], caption=comment)


def test_crud_validation_and_isolation(full_org, env):
    o = full_org
    r = add(o, section_ids=[o.wcs[0]["id"]])
    assert o.get("/problem-routes").json()["items"][0]["id"] == r["id"]
    assert o.patch(f"/problem-routes/{r['id']}", json={"enabled": False, "chat_ref": "@plan24_group"}).json()["chat_ref"] == "@plan24_group"
    for bad in ({"chat_ref": "abc"}, {"section_ids": [999999]}, {"name": " "}):
        assert o.post("/problem-routes", json={"name": "X", "chat_ref": G1, **bad}).status_code == 422
    other = Org(env, "o2@b.ru", "Цех Б")
    assert other.get("/problem-routes").json()["items"] == []
    assert other.patch(f"/problem-routes/{r['id']}", json={"name": "z"}).status_code == 404
    assert other.post(f"/problem-routes/{r['id']}/test").status_code == 404
    assert o.delete(f"/problem-routes/{r['id']}").status_code == 204


def test_problem_goes_to_group_at_creation_and_to_foreman(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    add(o)                                                                  # все участки
    n = len(tg.sent)
    done = report_problem(wchat)
    assert "и в группу участка" in text(done)
    g = sent_to(tg, int(G1), n)
    assert len(g) == 1 and "Нет материала" in g[0]["text"] and w["worker"] in g[0]["text"] and "ленты нет" in g[0]["text"]
    assert "на участке «" in g[0]["text"] and g[0]["markup"] is None
    assert (int(G1), wchat.chat_id) == tg.copies[-1][:2]                    # фото переслано и в группу
    assert sent_to(tg, fchat.chat_id, n)                                    # бригадир тоже получил


def test_only_matching_sections_and_several_groups(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    mine, other = me["work_center_id"], next(c["id"] for c in o.wcs if c["id"] != me["work_center_id"])
    add(o, name="Чужой", chat_ref=G2, section_ids=[other])
    add(o, name="Свой", chat_ref=G1, section_ids=[mine])
    add(o, name="Все", chat_ref=G1)                                          # та же группа — без повтора
    off = add(o, name="Выкл", chat_ref="-1003333333333"); o.patch(f"/problem-routes/{off['id']}", json={"enabled": False})
    n = len(tg.sent)
    report_problem(wchat)
    assert len(sent_to(tg, int(G1), n)) == 1 and not sent_to(tg, int(G2), n) and not sent_to(tg, -1003333333333, n)


def test_group_only_when_foreman_not_linked(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    link = next(l for l in o.get("/telegram/status", "manager").json()["links"] if l["kind"] == "user")
    assert o.delete(f"/telegram/links/{link['id']}", "manager").status_code == 204
    add(o)
    n = len(tg.sent)
    assert "Передал в группу участка" in text(report_problem(wchat))
    assert len(sent_to(tg, int(G1), n)) == 1


def test_no_groups_behaves_as_before(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    assert "Передал бригадиру" in text(report_problem(wchat)) and "группу" not in text(report_problem(wchat))


def test_test_button(full_org, tg):
    o = full_org
    r = add(o)
    ok = o.post(f"/problem-routes/{r['id']}/test")
    assert ok.status_code == 200 and ok.json()["last_status"] == "ok" and tg.sent[-1]["chat_id"] == int(G1)
    tg.send_message = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("telegram sendMessage: 403 Forbidden: bot was kicked"))
    bad = o.post(f"/problem-routes/{r['id']}/test")
    assert bad.status_code == 502 and o.get("/problem-routes").json()["items"][0]["last_status"] == "error"
