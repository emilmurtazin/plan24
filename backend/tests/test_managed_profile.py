"""Профиль управляемой БД (Timeweb Cloud): нет суперпользователя, есть только пользователь кластера с CREATE_ROLE/CREATEDB.
Воспроизводится на PostgreSQL: проверяется, что установка, миграции и защита работают без суперправ."""
import os
import subprocess
import sys

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from conftest import PG_MODE, PG_SUPER
from test_migrations import alembic

pytestmark = pytest.mark.skipif(not PG_MODE, reason="нужен PostgreSQL")
ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "postgres")
DB, GEN, APP = "plan24_tw_t", "tw_gen_t", "plan24_app_tw_t"


@pytest.fixture()
def managed(request):
    request.getfixturevalue("pg_db")
    u = make_url(PG_SUPER)
    sup = sa.create_engine(PG_SUPER, isolation_level="AUTOCOMMIT")
    with sup.connect() as c:
        c.execute(sa.text(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)"))
        for r in (APP, GEN):
            c.execute(sa.text(f"DROP ROLE IF EXISTS {r}"))
        c.execute(sa.text(f"CREATE ROLE {GEN} LOGIN CREATEROLE CREATEDB NOSUPERUSER PASSWORD 'gen-pw'"))   # как gen_user у провайдера
        c.execute(sa.text(f"CREATE DATABASE {DB} OWNER {GEN} TEMPLATE template0"))
    mk = lambda user, pw: u.set(username=user, password=pw, database=DB).render_as_string(hide_password=False)

    class M:
        owner, app = mk(GEN, "gen-pw"), mk(APP, "tw-app-pw")
        host, port = u.host, str(u.port or 5432)

        @staticmethod
        def psql(script, user=GEN, pw="gen-pw", extra=()):
            return subprocess.run(["psql", "-X", "-h", u.host, "-p", str(u.port or 5432), "-U", user, "-d", DB, "-v", "ON_ERROR_STOP=1", *extra, "-f", script],
                                  capture_output=True, text=True, env=dict(os.environ, PGPASSWORD=pw))
    yield M
    with sup.connect() as c:
        c.execute(sa.text(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)"))
        for r in (APP, GEN):
            c.execute(sa.text(f"DROP ROLE IF EXISTS {r}"))
    sup.dispose()


def install(m):
    init = os.path.join(ROOT, "init-roles-managed.sql")
    for _ in range(2):                                                          # повторный запуск безопасен
        r = m.psql(init, extra=("-v", "app_pw=tw-app-pw", "-v", f"app_role={APP}"))
        assert r.returncode == 0, r.stderr
    r = alembic(m.owner, "upgrade", "head", env={"PLAN24_APP_ROLE": APP})
    assert r.returncode == 0, r.stderr


def test_preflight_script_reports_ok_for_a_fresh_managed_db(managed):
    r = managed.psql(os.path.join(ROOT, "..", "timeweb", "db-preflight.sql"))
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for need in ("может создавать роли", "владелец БД", "может создавать таблицы в public"):
        line = next(l for l in out.splitlines() if need in l)
        assert "ОК" in line, line
    assert "НЕТ" not in out


def test_install_without_superuser_and_rls_holds(managed):
    install(managed)
    eng_app, eng_own = sa.create_engine(managed.app), sa.create_engine(managed.owner)
    with eng_own.begin() as c:
        c.execute(sa.text("INSERT INTO tenants (id,name,timezone,industry,plan,created_at) VALUES (1,'А','UTC','x','trial',now()),(2,'Б','UTC','x','trial',now())"))
        c.execute(sa.text("INSERT INTO projects (tenant_id,name,customer,version) VALUES (1,'п1','',1),(2,'п2','',1)"))
    with eng_own.connect() as c:                                                 # владелец БД видит всё (так и задумано)
        assert c.execute(sa.text("SELECT count(*) FROM projects")).scalar() == 2
    with eng_app.begin() as c:
        su, bypass, owns = c.execute(sa.text(
            "SELECT r.rolsuper, r.rolbypassrls, (SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user) "
            "FROM pg_roles r WHERE r.rolname=current_user")).one()
        assert (su, bypass, owns) == (False, False, 0)
        assert c.execute(sa.text("SELECT count(*) FROM projects")).scalar() == 0           # без контекста — ничего
        c.exec_driver_sql("SELECT set_config('app.tenant_id','1',true)")
        assert [r[0] for r in c.execute(sa.text("SELECT name FROM projects"))] == ["п1"]
    with pytest.raises(sa.exc.DBAPIError, match="permission denied"):                    # приложение не создаёт объекты и не правит аудит
        with eng_app.begin() as c:
            c.execute(sa.text("CREATE TABLE evil (id int)"))
    with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
        with eng_app.begin() as c:
            c.exec_driver_sql("SELECT set_config('app.tenant_id','1',true)")
            c.execute(sa.text("UPDATE audit_log SET action='x'"))
    eng_app.dispose(); eng_own.dispose()


def test_app_startup_guard_in_managed_profile(managed, monkeypatch):
    from plan24 import db as dbm
    install(managed)
    monkeypatch.delenv("PLAN24_ALLOW_PRIVILEGED_DB", raising=False)
    with pytest.raises(RuntimeError, match="обхода RLS"):
        dbm.init(managed.owner)                                                   # под gen_user приложение не стартует
    dbm.init(managed.app)                                                         # под созданной ролью — стартует
    assert dbm.is_pg()
