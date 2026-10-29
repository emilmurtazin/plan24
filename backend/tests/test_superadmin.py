"""Панель суперадмина: вход, изоляция токенов, статистика, блокировка организации, пользователи, вход под организацией.
Выполняется и на SQLite, и на PostgreSQL (там дополнительно проверяются политики RLS режима платформы)."""
import pytest
from sqlalchemy import select

from conftest import Org
from plan24 import db as dbm, mailer, security
from plan24.models import AuditLog
from test_telegram import tg  # noqa: F401  (фикстура фальшивого Telegram)

SA_EMAIL, SA_PASSWORD = "root@plan24.test", "very-long-superadmin-password"
SA = "/api/v1/superadmin"


@pytest.fixture()
def sa_on(monkeypatch):
    monkeypatch.setenv("PLAN24_SUPERADMIN_EMAIL", SA_EMAIL)
    monkeypatch.setenv("PLAN24_SUPERADMIN_PASSWORD_HASH", security.hash_password(SA_PASSWORD))


def sa_login(env, password=SA_PASSWORD, email=SA_EMAIL):
    return env.post(f"{SA}/login", json={"email": email, "password": password})


@pytest.fixture()
def admin(env, sa_on):
    r = sa_login(env)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def get(env, admin, path):
    return env.get(SA + path, headers=admin)


def post(env, admin, path, **kw):
    return env.post(SA + path, headers=admin, **kw)


def tid_of(o):
    return o.data["user"]["tenant"]["id"]


def test_panel_does_not_exist_without_server_settings(env):
    assert env.get("/admin").status_code == 404
    assert sa_login(env).status_code == 404
    assert env.get(f"{SA}/overview").status_code == 404


def test_login_and_token_isolation(env, sa_on):
    org = Org(env, "owner@a.ru", "Цех А")
    assert sa_login(env, "wrong-password-123").status_code == 401
    assert sa_login(env, email="other@x.ru").status_code == 401
    r = sa_login(env)
    assert r.status_code == 200 and r.json()["email"] == SA_EMAIL
    platform = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert get(env, platform, "/me").json() == {"email": SA_EMAIL}
    assert env.get("/api/v1/auth/me", headers=platform).status_code == 401           # токеном суперадмина API организаций недоступно
    assert get(env, org.h, "/overview").status_code == 401                            # токеном организации панель недоступна
    assert env.get(f"{SA}/overview").status_code == 401                               # без токена
    assert env.get("/admin").status_code == 200 and env.get("/admin").headers["x-robots-tag"].startswith("noindex")


def test_login_is_rate_limited(env, sa_on):
    for _ in range(5):
        assert sa_login(env, "wrong-password-123").status_code == 401
    assert sa_login(env, "wrong-password-123").status_code == 429
    assert sa_login(env).status_code == 429                                           # даже верный пароль — после блокировки


def test_overview_list_and_detail(env, admin):
    a, b = Org(env, "owner@a.ru", "Цех А"), Org(env, "boss@b.ru", "Цех Б")
    a.seed("furniture")
    a.add_user("manager", "m@a.ru")
    ov = get(env, admin, "/overview").json()
    assert ov["tenants"] == 2 and ov["blocked"] == 0 and ov["users"] == 3 and ov["workers"] > 0 and ov["new_30d"] == 2
    items = get(env, admin, "/tenants").json()["items"]
    by = {x["name"]: x for x in items}
    assert by["Цех А"]["users"] == 2 and by["Цех А"]["workers"] > 0 and by["Цех А"]["orders"] > 0
    assert by["Цех А"]["owner"]["email"] == "owner@a.ru" and by["Цех Б"]["workers"] == 0
    assert [x["name"] for x in get(env, admin, "/tenants?q=boss@b").json()["items"]] == ["Цех Б"]
    assert [x["name"] for x in get(env, admin, "/tenants?q=%D1%86%D0%B5%D1%85%20%D0%B0").json()["items"]] == ["Цех А"]
    d = get(env, admin, f"/tenants/{tid_of(a)}").json()
    assert [u["email"] for u in d["users_list"]] == ["owner@a.ru", "m@a.ru"] and d["history"] == []
    assert get(env, admin, "/tenants/99999").status_code == 404
    assert "password" not in str(d).lower()                                           # хешей и паролей в ответе нет


def test_block_and_unblock_organization(env, admin):
    a, b = Org(env, "owner@a.ru", "Цех А"), Org(env, "boss@b.ru", "Цех Б")
    refresh = a.data["refresh_token"]
    assert a.get("/auth/me").status_code == 200
    assert post(env, admin, f"/tenants/{tid_of(a)}/block", json={"reason": "неоплата"}).status_code == 200
    assert a.get("/auth/me").status_code == 403                                       # токен перестаёт действовать сразу
    assert "приостановлен" in a.get("/auth/me").json()["title"]
    assert env.post("/api/v1/auth/login", json={"email": "owner@a.ru", "password": "password-1"}).status_code == 403
    assert env.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 401      # сессии закрыты
    assert b.get("/auth/me").status_code == 200                                       # чужая организация не затронута
    row = next(x for x in get(env, admin, "/tenants").json()["items"] if x["id"] == tid_of(a))
    assert row["blocked"] and row["blocked_reason"] == "неоплата"
    assert get(env, admin, "/overview").json()["blocked"] == 1
    assert post(env, admin, f"/tenants/{tid_of(a)}/unblock").status_code == 200
    assert env.post("/api/v1/auth/login", json={"email": "owner@a.ru", "password": "password-1"}).status_code == 200
    hist = get(env, admin, f"/tenants/{tid_of(a)}").json()["history"]
    assert [h["action"] for h in hist] == ["platform_unblock", "platform_block"] and hist[1]["reason"] == "неоплата"


def test_users_deactivate_activate_and_reset_link(env, admin):
    a = Org(env, "owner@a.ru", "Цех А")
    m = a.add_user("manager", "m@a.ru")
    assert a.get("/auth/me", "manager").status_code == 200
    assert post(env, admin, f"/users/{m['id']}/deactivate").status_code == 200
    assert a.get("/auth/me", "manager").status_code == 401                            # отключённый теряет доступ сразу
    assert env.post("/api/v1/auth/login", json={"email": "m@a.ru", "password": "password-1"}).status_code == 401
    assert post(env, admin, f"/users/{m['id']}/reset-password").status_code == 409   # отключённому ссылку не шлём
    assert post(env, admin, f"/users/{m['id']}/activate").status_code == 200
    mailer.OUTBOX.clear()
    r = post(env, admin, f"/users/{m['id']}/reset-password")
    assert r.status_code == 200 and r.json()["sent_to"] == "m@a.ru"
    sent = [x for x in mailer.OUTBOX if x["to"] == "m@a.ru"]
    assert len(sent) == 1 and "/#/reset" in sent[0]["text"] or "reset" in sent[0]["text"]
    assert post(env, admin, "/users/99999/deactivate").status_code == 404
    acts = [h["action"] for h in get(env, admin, f"/tenants/{tid_of(a)}").json()["history"]]
    assert acts == ["platform_reset_link", "platform_user_on", "platform_user_off"]


def test_impersonation_for_support(env, admin):
    a = Org(env, "owner@a.ru", "Цех А")
    r = post(env, admin, f"/tenants/{tid_of(a)}/impersonate", json={})
    assert r.status_code == 200 and r.json()["user"]["email"] == "owner@a.ru" and "refresh_token" not in r.json()
    sup = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert env.get("/api/v1/auth/me", headers=sup).json()["email"] == "owner@a.ru"
    # учётные данные менять нельзя, настоящие сессии владельца не закрываются
    assert env.post("/api/v1/auth/change-password", headers=sup, json={"current_password": "password-1", "new_password": "hacked-password-1"}).status_code == 403
    assert env.patch("/api/v1/auth/profile", headers=sup, json={"first_name": "Х"}).status_code == 403
    assert env.post("/api/v1/auth/logout", headers=sup).status_code == 200
    assert env.post("/api/v1/auth/refresh", json={"refresh_token": a.data["refresh_token"]}).status_code == 200
    # действия помечаются в журнале
    assert env.post("/api/v1/work-centers", headers=sup, json={"name": "Сборка"}).status_code == 201
    s = dbm.admin_session()
    try:
        rows = list(s.scalars(select(AuditLog).where(AuditLog.tenant_id == tid_of(a))))
    finally:
        s.close()
    assert any((x.changes or {}).get("via_support") and x.entity == "work_center" for x in rows)
    assert any(x.action == "platform_login" and x.user_id is None for x in rows)
    # при блокировке поддержка всё равно может войти и посмотреть, а обычные пользователи — нет
    post(env, admin, f"/tenants/{tid_of(a)}/block", json={"reason": "x"})
    assert env.get("/api/v1/auth/me", headers=sup).status_code == 200 and a.get("/auth/me").status_code == 403
    assert post(env, admin, f"/tenants/{tid_of(a)}/impersonate", json={"user_id": 99999}).status_code == 404


def test_telegram_stops_for_blocked_organization(env, admin, full_org, tg):
    from test_telegram import link_worker, text
    chat, wid, name = link_worker(full_org)
    assert "приостановлен" not in text(chat.say("/tasks"))
    post(env, admin, f"/tenants/{tid_of(full_org)}/block", json={})
    assert "приостановлен" in text(chat.say("/tasks"))
    post(env, admin, f"/tenants/{tid_of(full_org)}/unblock")
    assert "приостановлен" not in text(chat.say("/tasks"))


def test_every_superadmin_route_is_closed_without_platform_token(env, admin):
    """Все адреса панели (кроме входа) закрыты: без токена и с токеном организации — 401; панель не выключена — иначе 404."""
    org = Org(env, "owner@a.ru", "Цех А")
    paths = env.app.openapi()["paths"]
    checked = 0
    for path, ops in paths.items():
        if not path.startswith(SA) or path == f"{SA}/login":
            continue
        for method in ops:
            url = path.replace("{tid}", "1").replace("{uid}", "1")
            for headers in ({}, org.h):
                r = env.request(method.upper(), url, headers=headers, json={} if method != "get" else None)
                assert r.status_code == 401, f"{method.upper()} {path} с {'токеном организации' if headers else 'без токена'}: {r.status_code}"
            checked += 1
    assert checked >= 10
