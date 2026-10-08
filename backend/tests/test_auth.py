import time
from conftest import Org


def test_register_creates_org_owner_and_defaults(env):
    o = Org(env)
    me = o.get("/auth/me").json()
    assert me["role"] == "owner" and me["tenant"]["name"] == "Цех А" and "users" in me["permissions"]
    s = o.get("/settings").json()
    assert s["priority_strictness"] == 100 and s["efficiency"] == 1.0 and s["fix_group_start"] is False
    assert len(o.get("/fact-reasons").json()["items"]) >= 3


def test_register_validation(env):
    base = {"org_name": "X1", "name": "Иван", "email": "a@b.ru", "password": "password-1", "accept_terms": True}
    assert env.post("/api/v1/auth/register", json={**base, "accept_terms": False}).status_code == 422
    assert env.post("/api/v1/auth/register", json={**base, "password": "short"}).status_code == 422
    assert env.post("/api/v1/auth/register", json={**base, "email": "not-an-email"}).status_code == 422
    assert env.post("/api/v1/auth/register", json={**base, "timezone": "Mars/Base"}).status_code == 422
    assert env.post("/api/v1/auth/register", json=base).status_code == 201
    assert env.post("/api/v1/auth/register", json={**base, "email": "A@B.ru"}).status_code == 409   # e-mail без учёта регистра


def test_login_wrong_password_generic_message_and_rate_limit(env):
    Org(env)
    bad = lambda e: env.post("/api/v1/auth/login", json={"email": e, "password": "wrong-password"})
    for _ in range(5):
        r = bad("owner@a.ru")
        assert r.status_code == 401
    assert bad("owner@a.ru").status_code == 429
    # несуществующий пользователь получает то же сообщение, что и неверный пароль
    r1, r2 = bad("nobody@a.ru"), bad("owner2@a.ru")
    assert r1.json()["title"] == r2.json()["title"] == "Неверный e-mail или пароль"


def test_refresh_rotation_and_reuse_detection(env):
    o = Org(env)
    lg = env.post("/api/v1/auth/login", json={"email": "owner@a.ru", "password": "password-1"}).json()
    r1 = lg["refresh_token"]
    n = env.post("/api/v1/auth/refresh", json={"refresh_token": r1})
    assert n.status_code == 200
    r2 = n.json()["refresh_token"]
    assert r2 != r1
    # повторное использование старого токена = подозрение на кражу: вся цепочка отзывается
    assert env.post("/api/v1/auth/refresh", json={"refresh_token": r1}).status_code == 401
    assert env.post("/api/v1/auth/refresh", json={"refresh_token": r2}).status_code == 401


def test_deactivated_user_loses_access_immediately(env):
    o = Org(env)
    u = o.add_user("manager", "m@a.ru")
    assert o.get("/projects", "manager").status_code == 200
    assert o.patch(f"/users/{u['id']}", json={"is_active": False}).status_code == 200
    assert o.get("/projects", "manager").status_code == 401                 # токен ещё не истёк, но доступа нет
    assert env.post("/api/v1/auth/login", json={"email": "m@a.ru", "password": "password-1"}).status_code == 401


def test_change_password_revokes_sessions(env):
    o = Org(env)
    rt = o.data["refresh_token"]
    r = o.post("/auth/change-password", json={"current_password": "password-1", "new_password": "new-password-2"})
    assert r.status_code == 200
    assert env.post("/api/v1/auth/refresh", json={"refresh_token": rt}).status_code == 401
    assert env.post("/api/v1/auth/login", json={"email": "owner@a.ru", "password": "new-password-2"}).status_code == 200
    assert o.post("/auth/change-password", json={"current_password": "bad", "new_password": "xxxxxxxx1"}).status_code == 403


def test_unauthenticated_and_garbage_token(env):
    assert env.get("/api/v1/projects").status_code == 401
    assert env.get("/api/v1/projects", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401


def test_last_owner_cannot_be_removed(env):
    o = Org(env)
    me = o.get("/auth/me").json()
    assert o.patch(f"/users/{me['id']}", json={"role": "manager"}).status_code == 409
    assert o.patch(f"/users/{me['id']}", json={"is_active": False}).status_code == 409
    o.add_user("manager", "m@a.ru")
    other = o.post("/users", json={"email": "o2@a.ru", "name": "Второй владелец", "role": "owner", "password": "password-1"}).json()
    assert o.patch(f"/users/{me['id']}", json={"role": "manager"}).status_code == 200      # теперь есть второй


def test_role_link_requirements(env):
    o = Org(env)
    base = {"email": "x@a.ru", "name": "Тест Тестов", "password": "password-1"}
    assert o.post("/users", json={**base, "role": "foreman"}).status_code == 422          # нужен участок
    assert o.post("/users", json={**base, "role": "worker"}).status_code == 422           # нужен исполнитель
    assert o.post("/users", json={**base, "role": "god"}).status_code == 422
