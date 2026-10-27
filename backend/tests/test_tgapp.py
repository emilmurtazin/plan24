"""Мини-приложение Telegram: проверка подписи данных запуска, вход без пароля, права сборщика и бригадира, отзыв доступа при отвязке."""
import hashlib
import hmac
import json
import time
from datetime import timedelta
from urllib.parse import urlencode

import pytest

from conftest import TODAY
from plan24 import config, security
from plan24.telegram import webapp
from test_telegram import Chat, buttons, issue, tg  # noqa: F401
from test_telegram_ux import sent_to, task_key, team

TOKEN = "123456:TEST-TOKEN"


def init_data(user_id, token=TOKEN, auth_date=None, **extra):
    """Данные запуска, подписанные так же, как это делает Telegram."""
    d = {"auth_date": str(int(time.time()) if auth_date is None else auth_date), "query_id": "AAH123", "user": json.dumps({"id": user_id, "first_name": "Тест"}), **extra}
    check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    d["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(d)


class App:
    """Клиент мини-приложения: вход по initData и вызовы API с токеном сессии."""
    def __init__(self, org, chat_id):
        self.org, self.chat_id, self.token = org, chat_id, None

    def login(self, **kw):
        r = self.org.c.post("/api/v1/tgapp/auth", json={"init_data": init_data(self.chat_id, **kw)})
        if r.status_code == 200:
            self.token = r.json()["token"]
        return r

    def call(self, method, path, body=None, token=None):
        h = {"Authorization": "Bearer " + (token or self.token)} if (token or self.token) else {}
        return self.org.c.request(method, "/api/v1/tgapp" + path, json=body, headers=h)

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, body if body is not None else {}, **kw)

    def put(self, path, body=None, **kw):
        return self.call("PUT", path, body if body is not None else {}, **kw)

    def delete(self, path, **kw):
        return self.call("DELETE", path, **kw)


def open_app(org, chat_id):
    a = App(org, chat_id)
    assert a.login().status_code == 200
    return a


# ------------------------------------------------------------------ проверка подписи
def test_validate_accepts_signed_data_and_rejects_everything_else():
    ok = webapp.validate(init_data(42), TOKEN)
    assert ok["user"]["id"] == 42
    for bad in (init_data(42, token="999:OTHER"),                                 # подпись чужим токеном
                init_data(42, auth_date=int(time.time()) - 90000),                 # устарело
                init_data(42, auth_date=int(time.time()) + 3600),                  # из будущего
                "", "hash=abc", "auth_date=1&user=%7B%7D"):
        with pytest.raises(webapp.InitDataError):
            webapp.validate(bad, TOKEN)
    tampered = init_data(42).replace("%22id%22%3A+42", "%22id%22%3A+43")
    with pytest.raises(webapp.InitDataError):
        webapp.validate(tampered, TOKEN)
    with pytest.raises(webapp.InitDataError):                                      # повторяющееся поле
        webapp.validate(init_data(42) + "&auth_date=1", TOKEN)
    with pytest.raises(webapp.InitDataError):
        webapp.validate("x" * 9000, TOKEN)


# ------------------------------------------------------------------ вход
def test_login_requires_linked_chat_and_valid_signature(full_org, tg):
    o = full_org
    o.replan_all()
    assert App(o, 777).login().status_code == 403                                  # подпись верна, но чат не привязан
    r = o.c.post("/api/v1/tgapp/auth", json={"init_data": init_data(777, token="999:OTHER")})
    assert r.status_code == 401
    r = o.c.post("/api/v1/tgapp/auth", json={"init_data": ""})
    assert r.status_code == 401 or r.status_code == 422


def test_login_returns_role_and_worker_for_linked_chats(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    wa = App(o, wchat.chat_id)
    r = wa.login()
    assert r.status_code == 200 and r.json()["role"] == "worker" and r.json()["worker_id"] == w["worker_id"] and r.json()["name"] == w["worker"]
    fa = App(o, fchat.chat_id)
    r = fa.login()
    assert r.json()["role"] == "foreman" and r.json()["worker_id"] is None


def test_login_is_rate_limited_after_failures(full_org, tg):
    o = full_org
    security.tgapp_limiter.clear_memory()
    for _ in range(20):
        assert o.c.post("/api/v1/tgapp/auth", json={"init_data": "hash=00"}).status_code == 401
    assert o.c.post("/api/v1/tgapp/auth", json={"init_data": "hash=00"}).status_code == 429
    security.tgapp_limiter.clear_memory()


def test_app_is_off_when_bot_is_not_configured(full_org, monkeypatch):
    monkeypatch.delenv("PLAN24_TG_TOKEN")
    assert full_org.c.post("/api/v1/tgapp/auth", json={"init_data": init_data(1)}).status_code == 404


# ------------------------------------------------------------------ токены разных видов не смешиваются
def test_tokens_are_not_interchangeable(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    a = open_app(o, wchat.chat_id)
    assert o.c.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + a.token}).status_code == 401      # токен приложения — не веб-вход
    web = o.c.headers.get("Authorization") or ""
    web_token = web.replace("Bearer ", "") if web else security.make_access(1, 1)
    assert a.get("/day", token=web_token).status_code == 401                                              # веб-токен — не токен приложения
    assert a.get("/day", token="garbage.token.value").status_code == 401
    assert o.c.get("/api/v1/tgapp/day").status_code == 401


# ------------------------------------------------------------------ сборщик
def test_worker_sees_only_own_tasks_and_enters_fact(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    a = open_app(o, wchat.chat_id)
    d = a.get("/day").json()
    assert d["role"] == "worker" and [x["worker_id"] for x in d["workers"]] == [w["worker_id"]] and d["can_enter"]
    assert d["reasons"][str(w["worker_id"])]
    key, t = task_key(w, 2)
    ol, op = t["order_line_id"], t["operation_id"]
    body = {"order_line_id": ol, "operation_id": op, "worker_id": w["worker_id"], "qty": 2}
    r = a.post("/fact", body)
    assert r.status_code == 200 and r.json()["qty"] == 2 and r.json()["previous"] is None
    fs = [f for f in o.get("/facts").json()["items"] if f["worker_id"] == w["worker_id"]]
    assert fs and fs[0]["source"] == "telegram"
    r2 = a.post("/fact", {**body, "qty": 3})
    assert r2.json()["previous"] == 2
    # отмена возвращает прежнее значение, а при первой записи — убирает её
    assert a.post(f"/fact/{r2.json()['fact_id']}/undo", {"previous": 2}).json()["qty"] == 2
    assert a.post(f"/fact/{r.json()['fact_id']}/undo", {"previous": None}).json()["removed"] is True
    assert not [f for f in o.get("/facts").json()["items"] if f["worker_id"] == w["worker_id"]]


def test_worker_cannot_touch_others_or_send_garbage(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    a = open_app(o, wchat.chat_id)
    key, t = task_key(w, 1)
    other = next(x for x in o.get("/reports/day").json()["workers"] if x["worker_id"] != w["worker_id"] and x["tasks"])
    ot = other["tasks"][0]
    forged = {"order_line_id": ot["order_line_id"], "operation_id": ot["operation_id"], "worker_id": other["worker_id"], "qty": 1}
    assert a.post("/fact", forged).status_code == 404                                  # чужое задание
    base = {"order_line_id": t["order_line_id"], "operation_id": t["operation_id"], "worker_id": w["worker_id"]}
    assert a.post("/fact", {**base, "qty": -1}).status_code == 422
    assert a.post("/fact", {**base, "qty": 10 ** 9}).status_code == 422
    assert a.post("/fact", {**base, "qty": "много"}).status_code == 422
    assert not [f for f in o.get("/facts").json()["items"] if f["worker_id"] != w["worker_id"]]
    # чужой факт отменить/причину поставить нельзя
    o.post("/facts", json={"fact_date": TODAY.isoformat(), "order_line_id": ot["order_line_id"], "operation_id": ot["operation_id"],
                           "worker_id": other["worker_id"], "qty": 1, "note": "x"})
    fid = next(f["id"] for f in o.get("/facts").json()["items"] if f["worker_id"] == other["worker_id"])
    assert a.post(f"/fact/{fid}/undo", {"previous": None}).status_code in (403, 404)
    assert a.post(f"/fact/{fid}/reason", {"reason_id": None}).status_code in (403, 404)
    # кнопки бригадира сборщику недоступны
    for path in ("/replan", "/remind", "/confirm"):
        assert a.post(path).status_code == 403
    assert a.get("/queue").status_code == 403


def test_reason_alerts_foreman_and_close_day_and_tomorrow(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    a = open_app(o, wchat.chat_id)
    key, t = task_key(w, 2)
    r = a.post("/fact", {"order_line_id": t["order_line_id"], "operation_id": t["operation_id"], "worker_id": w["worker_id"], "qty": 1}).json()
    n = len(tg.sent)
    rid = a.get("/day").json()["reasons"][str(w["worker_id"])][0]["id"]
    assert a.post(f"/fact/{r['fact_id']}/reason", {"reason_id": rid}).status_code == 200
    got = sent_to(tg, fchat.chat_id, n)
    assert len(got) == 1 and "Отклонение" in got[0]["text"] and w["worker"] in got[0]["text"]
    # причина чужого участка не принимается
    foreign = o.post("/fact-reasons", json={"name": "Чужая", "work_center_id": next(c["id"] for c in o.wcs if c["id"] != me["work_center_id"])}).json()["id"]
    assert a.post(f"/fact/{r['fact_id']}/reason", {"reason_id": foreign}).status_code == 422
    # закрыть день: заполняет только задания без факта
    ok = a.post("/closeday", {}).json()
    planned = [x for x in w["tasks"] if x["plan_qty"]]
    assert ok["ok"] == len(planned) - 1 and not ok["fails"]
    assert a.post("/closeday", {}).json()["ok"] == 0
    tm = a.get("/tomorrow").json()
    assert tm["role"] == "worker" and tm["workers"] and all(x["plan_qty"] for ww in tm["workers"] for x in ww["tasks"])


def test_problem_and_absence_from_app(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    a = open_app(o, wchat.chat_id)
    n = len(tg.sent)
    assert a.post("/problem", {"type": "mat", "comment": "нет ленты"}).json()["delivered"] == 1
    got = sent_to(tg, fchat.chat_id, n)
    assert len(got) == 1 and "Нет материала" in got[0]["text"] and "нет ленты" in got[0]["text"] and w["worker"] in got[0]["text"]
    assert a.post("/problem", {"type": "xxx"}).status_code == 422
    assert a.post("/problem", {"type": "bad", "comment": "я" * 501}).status_code == 422
    n = len(tg.sent)
    r = a.post("/absence", {"period": "m", "reason": "ill", "worker_id": 999999})        # сборщик отмечает только себя, чужой id игнорируется
    assert r.status_code == 200 and r.json()["worker"] == w["worker"]
    ab = [x for x in o.get("/absences").json()["items"] if x["worker_id"] == w["worker_id"]]
    assert ab and ab[0]["date_from"] == (TODAY + timedelta(days=1)).isoformat()
    assert "rp" in {b["callback_data"] for b in buttons(sent_to(tg, fchat.chat_id, n))}
    assert a.post("/absence", {"period": "zz", "reason": "ill"}).status_code == 422


# ------------------------------------------------------------------ бригадир
def test_foreman_sees_team_and_enters_for_own_workers_only(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    f = open_app(o, fchat.chat_id)
    d = f.get("/day").json()
    ids = {x["worker_id"] for x in d["workers"]}
    mine = {x["id"] for x in o.workers if x["work_center_id"] == me["work_center_id"]}
    assert ids and ids <= mine and {t["worker_id"] for t in d["team"]} == mine
    key, t = task_key(w, 1)
    assert f.post("/fact", {"order_line_id": t["order_line_id"], "operation_id": t["operation_id"], "worker_id": w["worker_id"], "qty": 1}).status_code == 200
    foreign = next(x for x in o.get("/reports/day").json()["workers"] if x["work_center_id"] != me["work_center_id"] and x["tasks"])
    ft = foreign["tasks"][0]
    assert f.post("/fact", {"order_line_id": ft["order_line_id"], "operation_id": ft["operation_id"], "worker_id": foreign["worker_id"], "qty": 1}).status_code == 404
    assert f.post("/closeday", {"worker_id": foreign["worker_id"]}).json()["ok"] == 0
    assert not [x for x in o.get("/facts").json()["items"] if x["worker_id"] == foreign["worker_id"]]


def test_foreman_absence_remind_replan_queue(full_org, tg):
    o = full_org
    o.patch("/settings", json={"freeze_days": 0})
    fchat, wchat, w, me = team(o)
    f = open_app(o, fchat.chat_id)
    foreign = next(x for x in o.workers if x["work_center_id"] != me["work_center_id"])
    assert f.post("/absence", {"worker_id": foreign["id"], "period": "t", "reason": "ill"}).status_code == 403
    assert f.post("/absence", {"period": "t", "reason": "ill"}).status_code == 422                     # не выбран работник
    n = len(tg.sent)
    assert f.post("/absence", {"worker_id": w["worker_id"], "period": "t", "reason": "vac"}).json()["when"] == "сегодня"
    assert "отметил ваше отсутствие" in " ".join(m["text"] for m in sent_to(tg, wchat.chat_id, n))
    r = f.post("/replan").json()
    assert r["workers"] >= 0 and "tasks" in r
    n = len(tg.sent)
    rem = f.post("/remind").json()
    assert rem["status"] in ("sent", "nobody", "throttled")
    assert f.post("/remind").json()["status"] in ("throttled", "nobody")
    assert f.get("/queue").json() == {"items": []}


def test_queue_confirm_flow(full_org, tg):
    o = full_org
    o.patch("/settings", json={"fact_confirmation": True})
    fchat, wchat, w, me = team(o)
    a, f = open_app(o, wchat.chat_id), open_app(o, fchat.chat_id)
    key, t = task_key(w, 1)
    wr = a.post("/fact", {"order_line_id": t["order_line_id"], "operation_id": t["operation_id"], "worker_id": w["worker_id"], "qty": 1}).json()
    assert wr["status"] == "submitted"
    q = f.get("/queue").json()["items"]
    assert [x["fact_id"] for x in q] == [wr["fact_id"]] and f.get("/day").json()["queue"] == 1
    assert f.post("/confirm", {"fact_id": wr["fact_id"]}).json()["confirmed"] == 1
    assert f.get("/queue").json()["items"] == []


# ------------------------------------------------------------------ отзыв доступа
def test_unlinking_chat_or_deactivating_foreman_revokes_the_app(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    a, f = open_app(o, wchat.chat_id), open_app(o, fchat.chat_id)
    assert a.get("/day").status_code == 200 and f.get("/day").status_code == 200
    link = next(l for l in o.get("/telegram/status").json()["links"] if l["name"] == w["worker"])
    assert o.delete(f"/telegram/links/{link['id']}").status_code == 204
    assert a.get("/day").status_code == 401                                          # токен жив, но привязки уже нет
    assert a.login().status_code == 403
    o.patch(f"/users/{me['id']}", json={"is_active": False})
    assert f.get("/day").status_code == 401


# ------------------------------------------------------------------ кнопки бота и заголовки
def test_bot_offers_app_only_over_https(full_org, tg, monkeypatch):
    o = full_org
    monkeypatch.setenv("PLAN24_BASE_URL", "http://localhost:8000")
    me = next(u for u in o.get("/users").json() if u["role"] == "foreman")
    chat = Chat(o, 6101)
    msgs = chat.say("/start " + issue(o, user_id=me["id"]))
    assert config.tgapp_url() is None and not [b for b in buttons(msgs) if b.get("web_app")] and not getattr(tg, "menu_buttons", {})
    monkeypatch.setenv("PLAN24_BASE_URL", "https://plan.example.com")
    assert config.tgapp_url() == "https://plan.example.com/tg/"
    wid = o.workers[0]["id"]
    chat2 = Chat(o, 6102)
    msgs = chat2.say("/start " + issue(o, worker_id=wid))
    app_btn = [b for b in buttons(msgs) if b.get("web_app")]
    assert app_btn and app_btn[0]["web_app"]["url"] == "https://plan.example.com/tg/"
    assert tg.menu_buttons[6102] == "https://plan.example.com/tg/"
    monkeypatch.setenv("PLAN24_TGAPP", "off")
    assert config.tgapp_url() is None


def test_app_page_headers_allow_telegram_but_site_stays_unframable(full_org):
    o = full_org
    page = o.c.get("/tg/")
    assert page.status_code == 200 and "tgapp/app.js" in page.text
    assert "X-Frame-Options" not in page.headers and "https://web.telegram.org" in page.headers["Content-Security-Policy"]
    assert "'self'" in page.headers["Content-Security-Policy"] and page.headers["Cache-Control"] == "no-store"
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]                 # внешних скриптов нет
    site = o.c.get("/")
    assert site.headers["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in site.headers["Content-Security-Policy"]
    assert o.c.get("/static/tgapp/app.js").status_code == 200 and o.c.get("/static/tgapp/app.css").status_code == 200


def test_every_app_route_is_closed_without_a_session(full_org, tg):
    """Все маршруты мини-приложения, кроме входа, без токена и с чужим токеном отвечают 401 — нового открытого маршрута не появится."""
    o = full_org
    web = security.make_access(1, 1)
    routes = []
    for path, ops in o.c.app.openapi()["paths"].items():
        if path.startswith("/api/v1/tgapp/") and path != "/api/v1/tgapp/auth":
            routes += [(m.upper(), path.replace("{fid}", "1")) for m in ops]
    assert len(routes) >= 12
    for m, path in routes:
        for hdr in ({}, {"Authorization": "Bearer " + web}):
            r = o.c.request(m, path, json={}, headers=hdr)
            assert r.status_code == 401, (m, path, r.status_code)


# ------------------------------------------------------------------ другая работа и работа вне перечня
def test_worker_enters_fact_for_any_project_order_operation(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    a = open_app(o, wchat.chat_id)
    cat = a.get("/catalog").json()["orders"]
    assert len(cat) >= 3 and {"order_line_id", "project", "order", "product", "qty"} <= set(cat[0])
    planned_ols = {t["order_line_id"] for t in w["tasks"]}
    other = next(x for x in cat if x["order_line_id"] not in planned_ols)                  # заказ, которого в плане работника нет
    ops = a.get(f"/catalog/ops?order_line_id={other['order_line_id']}").json()["operations"]
    assert len(ops) >= 2 and {"operation_id", "name", "required", "done"} <= set(ops[0])
    # несколько операций подряд + другой заказ
    r1 = a.post("/fact/other", {"order_line_id": other["order_line_id"], "operation_id": ops[0]["operation_id"], "qty": 3})
    r2 = a.post("/fact/other", {"order_line_id": other["order_line_id"], "operation_id": ops[1]["operation_id"], "qty": 2})
    assert r1.status_code == r2.status_code == 200 and r1.json()["worker_id"] == w["worker_id"]
    second = next(x for x in cat if x["order_line_id"] not in planned_ols and x["order_line_id"] != other["order_line_id"])
    ops2 = a.get(f"/catalog/ops?order_line_id={second['order_line_id']}").json()["operations"]
    assert a.post("/fact/other", {"order_line_id": second["order_line_id"], "operation_id": ops2[0]["operation_id"], "qty": 1}).status_code == 200
    fs = [f for f in o.get("/facts").json()["items"] if f["worker_id"] == w["worker_id"]]
    assert len(fs) == 3 and all(f["source"] == "telegram" for f in fs)
    # на экране дня они — карточки «вне плана», редактируются обычным «＋/−»
    day = a.get("/day").json()["workers"][0]["tasks"]
    extra_cards = [t for t in day if t["order_line_id"] in (other["order_line_id"], second["order_line_id"])]
    assert len(extra_cards) == 3 and all(not t["plan_qty"] and t["fact_qty"] is not None for t in extra_cards)
    # повторный ввод по той же операции заменяет значение
    again = a.post("/fact/other", {"order_line_id": other["order_line_id"], "operation_id": ops[0]["operation_id"], "qty": 5}).json()
    assert again["previous"] == 3 and again["qty"] == 5
    # чужой worker_id игнорируется: факт пишется только себе
    a.post("/fact/other", {"order_line_id": second["order_line_id"], "operation_id": ops2[1]["operation_id"], "qty": 1, "worker_id": 999})
    assert {f["worker_id"] for f in o.get("/facts").json()["items"]} == {w["worker_id"]}


def test_other_fact_validation(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    a = open_app(o, wchat.chat_id)
    cat = a.get("/catalog").json()["orders"]
    ops = a.get(f"/catalog/ops?order_line_id={cat[0]['order_line_id']}").json()["operations"]
    base = {"order_line_id": cat[0]["order_line_id"], "operation_id": ops[0]["operation_id"]}
    assert a.post("/fact/other", {**base, "qty": -1}).status_code == 422
    assert a.post("/fact/other", {**base, "qty": 10 ** 9}).status_code == 422
    diff = next((x for x in cat if x["product"] != cat[0]["product"]), None)                       # другое изделие — другой маршрут операций
    if diff:
        other_ops = a.get(f"/catalog/ops?order_line_id={diff['order_line_id']}").json()["operations"]
        assert a.post("/fact/other", {"order_line_id": cat[0]["order_line_id"], "operation_id": other_ops[0]["operation_id"], "qty": 1}).status_code == 422   # операция чужого маршрута
    assert a.post("/fact/other", {"order_line_id": 999999, "operation_id": 1, "qty": 1}).status_code == 404
    assert a.get("/catalog/ops?order_line_id=999999").status_code == 404
    # отменённая позиция — факт вносить нельзя
    assert o.patch(f"/order-lines/{cat[0]['order_line_id']}", json={"status": "cancelled"}).status_code == 200
    assert a.post("/fact/other", {**base, "qty": 1}).status_code == 422
    assert cat[0]["order_line_id"] not in [x["order_line_id"] for x in a.get("/catalog").json()["orders"]]


def test_foreman_enters_other_fact_for_own_workers_only(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    f = open_app(o, fchat.chat_id)
    cat = f.get("/catalog").json()["orders"]
    ops = f.get(f"/catalog/ops?order_line_id={cat[0]['order_line_id']}").json()["operations"]
    body = {"order_line_id": cat[0]["order_line_id"], "operation_id": ops[0]["operation_id"], "qty": 2}
    assert f.post("/fact/other", body).status_code == 422                                  # не выбран работник
    assert f.post("/fact/other", {**body, "worker_id": w["worker_id"]}).status_code == 200
    foreign = next(x for x in o.workers if x["work_center_id"] != me["work_center_id"])
    assert f.post("/fact/other", {**body, "worker_id": foreign["id"]}).status_code == 403
    assert not [x for x in o.get("/facts").json()["items"] if x["worker_id"] == foreign["id"]]


def test_extra_work_outside_operations(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    a = open_app(o, wchat.chat_id)
    r = a.post("/extra", {"title": "Уборка участка", "hours": 1.5})
    assert r.status_code == 201 and r.json()["fact_hours"] == 1.5 and r.json()["worker_id"] == w["worker_id"]
    xid = r.json()["id"]
    day = a.get("/day").json()["workers"][0]
    assert [(x["title"], x["fact_hours"]) for x in day["extras"]] == [("Уборка участка", 1.5)]
    assert a.put(f"/extra/{xid}", {"fact_hours": 2}).json()["fact_hours"] == 2
    cat = a.get("/catalog").json()["orders"]
    with_order = a.post("/extra", {"title": "Переналадка станка", "hours": 0.5, "order_line_id": cat[0]["order_line_id"]})
    assert with_order.status_code == 201 and with_order.json()["order_line_id"] == cat[0]["order_line_id"]
    assert a.post("/extra", {"title": "", "hours": 1}).status_code == 422
    assert a.post("/extra", {"title": "x", "hours": 0}).status_code == 422
    assert a.post("/extra", {"title": "x", "hours": 25}).status_code == 422
    assert a.put(f"/extra/{xid}", {"fact_hours": 30}).status_code == 422
    assert a.post("/extra", {"title": "x", "hours": 1, "order_line_id": 999999}).status_code == 404
    # планировщик, веб-версия и работа вне перечня видят то же самое
    web = next(x for x in o.get("/reports/day").json()["workers"] if x["worker_id"] == w["worker_id"])["extras"]
    assert {x["title"] for x in web} == {"Уборка участка", "Переналадка станка"}
    assert a.delete(f"/extra/{xid}").json() == {"removed": True}
    assert [x["title"] for x in a.get("/day").json()["workers"][0]["extras"]] == ["Переналадка станка"]


def test_extra_work_scope_worker_and_foreman(full_org, tg):
    o = full_org
    fchat, wchat, w, me = team(o)
    a, f = open_app(o, wchat.chat_id), open_app(o, fchat.chat_id)
    other_w = next(x for x in o.get("/reports/day").json()["workers"] if x["worker_id"] != w["worker_id"])
    mine_x = a.post("/extra", {"title": "Своя работа", "hours": 1, "worker_id": other_w["worker_id"]}).json()    # worker_id сборщика игнорируется
    assert mine_x["worker_id"] == w["worker_id"]
    # чужую работу сборщик не меняет и не удаляет
    foreign_x = o.post("/extra-tasks", json={"plan_date": TODAY.isoformat(), "worker_id": other_w["worker_id"], "title": "Чужая", "hours": 1}).json()
    assert a.put(f"/extra/{foreign_x['id']}", {"fact_hours": 3}).status_code in (403, 404)
    assert a.delete(f"/extra/{foreign_x['id']}").status_code in (403, 404)
    # бригадир — только работникам своего участка
    assert f.post("/extra", {"title": "Для работника", "hours": 2, "worker_id": w["worker_id"]}).status_code == 201
    foreign = next(x for x in o.workers if x["work_center_id"] != me["work_center_id"])
    assert f.post("/extra", {"title": "Чужому", "hours": 2, "worker_id": foreign["id"]}).status_code == 403
