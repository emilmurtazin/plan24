import os, sys, tempfile
from datetime import date
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TODAY = date(2026, 10, 2)          # пятница


PG_SUPER = os.environ.get("PLAN24_TEST_PG_SUPER_URL")      # включает прогон набора на PostgreSQL
PG_MODE = bool(PG_SUPER)
PG_DB = "plan24_test"


class PgUrls:
    def __init__(self, base):
        from sqlalchemy.engine import make_url
        u = make_url(base)
        mk = lambda user, pw: u.set(username=user, password=pw, database=PG_DB).render_as_string(hide_password=False)
        self.owner, self.app, self.admin = mk("plan24_owner", "owner-pw"), mk("plan24_app", "app-pw"), mk("plan24_admin", "admin-pw")


@pytest.fixture(scope="session")
def pg_db():
    """Чистая БД, схема — миграциями Alembic (владелец), приложение и служебные тесты — своими ролями."""
    if not PG_MODE:
        pytest.skip("PostgreSQL-режим выключен")
    import subprocess
    import sqlalchemy as sa
    eng = sa.create_engine(PG_SUPER, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        for role, attrs, pw in (("plan24_owner", "NOSUPERUSER NOBYPASSRLS", "owner-pw"), ("plan24_app", "NOSUPERUSER NOBYPASSRLS", "app-pw"),
                                ("plan24_admin", "NOSUPERUSER BYPASSRLS", "admin-pw")):
            if not c.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": role}).scalar():
                c.execute(sa.text(f"CREATE ROLE {role} LOGIN {attrs} PASSWORD '{pw}'"))
            else:                                   # роль могла остаться от другого прогона с другим паролем/атрибутами
                c.execute(sa.text(f"ALTER ROLE {role} LOGIN {attrs} PASSWORD '{pw}'"))
        c.execute(sa.text(f"DROP DATABASE IF EXISTS {PG_DB} WITH (FORCE)"))
        c.execute(sa.text(f"CREATE DATABASE {PG_DB} OWNER plan24_owner TEMPLATE template0"))
    urls = PgUrls(PG_SUPER)
    sup_db = sa.create_engine(urls.owner.replace("plan24_owner:owner-pw", "plan24_super:super-pw") if False else
                              PG_SUPER.rsplit("/", 1)[0] + "/" + PG_DB, isolation_level="AUTOCOMMIT")
    with sup_db.connect() as c:
        for q in ("ALTER SCHEMA public OWNER TO plan24_owner", "REVOKE ALL ON SCHEMA public FROM PUBLIC",
                  "GRANT USAGE ON SCHEMA public TO plan24_app, plan24_admin"):
            c.execute(sa.text(q))
    backend = os.path.join(os.path.dirname(__file__), "..")
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], cwd=backend,
                       env=dict(os.environ, PLAN24_DB_MIGRATE_URL=urls.owner), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    yield urls
    eng.dispose()


def truncate_all(urls):
    import sqlalchemy as sa
    e = sa.create_engine(urls.owner, isolation_level="AUTOCOMMIT")
    with e.connect() as c:
        names = [r[0] for r in c.execute(sa.text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))]
        c.execute(sa.text("TRUNCATE " + ", ".join(names) + " RESTART IDENTITY CASCADE"))
    e.dispose()


def init_test_db(tmp_path, request):
    """SQLite-файл во временном каталоге или PostgreSQL (очищенный) — в зависимости от режима."""
    from plan24 import db
    if PG_MODE:
        urls = request.getfixturevalue("pg_db")
        truncate_all(urls)
        db.init(urls.app, admin_url=urls.admin)
    else:
        db.init(f"sqlite:///{tmp_path / 't.sqlite3'}")


@pytest.fixture()
def env(tmp_path, monkeypatch, request):
    monkeypatch.setenv("PLAN24_DATA", str(tmp_path))
    monkeypatch.setenv("PLAN24_SECRET", "test-secret-test-secret-test-secret!")
    monkeypatch.setenv("PLAN24_ENV", "dev")
    monkeypatch.setenv("PLAN24_MAIL_BACKEND", "memory")
    monkeypatch.setenv("PLAN24_BASE_URL", "https://plan24.test")
    monkeypatch.setenv("PLAN24_TODAY", "2026-10-02")
    monkeypatch.setenv("PLAN24_TG_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setenv("PLAN24_TG_SECRET", "path-secret-xyz")
    monkeypatch.setenv("PLAN24_TG_BOT_USERNAME", "plan24_test_bot")
    import importlib
    from plan24 import config, db, security
    importlib.reload(config)
    init_test_db(tmp_path, request)
    from argon2 import PasswordHasher
    monkeypatch.setattr(security, '_ph', PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))   # только для тестов
    monkeypatch.setattr(security, '_DUMMY_HASH', None, raising=False)
    security.reset_limiters()
    from plan24 import mailer
    mailer.OUTBOX.clear()
    from plan24.telegram import bot as tgbot, client as tgclient
    tgbot.bind_limiter.clear_memory()
    tgclient.set_client(None)
    import plan24.routers.auth as auth_mod
    from plan24.app import create_app
    from plan24.deps import get_today
    app = create_app()
    state = {"today": TODAY}
    app.dependency_overrides[get_today] = lambda: state["today"]
    from fastapi.testclient import TestClient
    c = TestClient(app)
    c.state = state
    return c


class Org:
    """Организация с владельцем и пользователями по ролям."""
    def __init__(self, c, email="owner@a.ru", name="Цех А"):
        r = c.post("/api/v1/auth/register", json={"org_name": name, "name": "Владелец", "email": email,
                                                  "password": "password-1", "accept_terms": True})
        assert r.status_code == 201, r.text
        self.c, self.data = c, r.json()
        self.h = {"Authorization": "Bearer " + r.json()["access_token"]}
        self.users = {"owner": self.h}

    def call(self, method, path, role="owner", **kw):
        return self.c.request(method, "/api/v1" + path, headers=self.users[role], **kw)

    def get(self, path, role="owner", **kw): return self.call("GET", path, role, **kw)
    def post(self, path, role="owner", **kw): return self.call("POST", path, role, **kw)
    def patch(self, path, role="owner", **kw): return self.call("PATCH", path, role, **kw)
    def put(self, path, role="owner", **kw): return self.call("PUT", path, role, **kw)
    def delete(self, path, role="owner", **kw): return self.call("DELETE", path, role, **kw)

    def add_user(self, role, email, **links):
        r = self.post("/users", json={"email": email, "name": role.title() + " Тест", "role": role,
                                      "password": "password-1", **links})
        assert r.status_code == 201, r.text
        lg = self.c.post("/api/v1/auth/login", json={"email": email, "password": "password-1"})
        assert lg.status_code == 200, lg.text
        self.users[role] = {"Authorization": "Bearer " + lg.json()["access_token"]}
        return r.json()

    def replan_all(self, json=None, role="owner"):
        """Перепланирование «всей организации» после отмены этой возможности: первый расчёт — целиком,
        дальше — участок за участком. Возвращает ответ последнего расчёта."""
        json = dict(json or {})
        if self.get("/plans/active", role).json()["plan"] is None:
            return self.post("/plans/replan", role, json=json)
        r = None
        for wc in self.get("/work-centers", role).json()["items"]:
            r = self.post("/plans/replan", role, json={**json, "work_center_id": wc["id"]})
            assert r.status_code == 201, r.text
        return r

    def seed(self, kind="furniture"):
        r = self.post("/demo/load", params={"kind": kind})
        assert r.status_code == 201, r.text
        return r.json()

    def ids(self, path):
        return [x["id"] for x in self.get(path).json()["items"]]


@pytest.fixture()
def org(env):
    return Org(env)


@pytest.fixture()
def full_org(env):
    """Организация с демо-данными и пользователями всех ролей."""
    o = Org(env)
    o.seed("furniture")
    wcs = o.get("/work-centers").json()["items"]
    workers = o.get("/workers").json()["items"]
    w1 = next(w for w in workers if w["work_center_id"] == wcs[0]["id"])
    o.add_user("manager", "m@a.ru")
    o.add_user("foreman", "f@a.ru", work_center_id=wcs[0]["id"])
    o.add_user("worker", "w@a.ru", worker_id=w1["id"])
    o.add_user("viewer", "v@a.ru")
    o.add_user("technologist", "t@a.ru")
    o.add_user("director", "d@a.ru")
    o.wcs, o.workers, o.w1 = wcs, workers, w1
    return o
