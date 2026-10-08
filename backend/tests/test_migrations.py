"""Миграции Alembic: схема = модели, откат/повтор, данные переживают обновление, защитные проверки запуска."""
import os
import subprocess
import sys

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from conftest import PG_MODE, PG_SUPER, PgUrls
from plan24.models import Base

BACKEND = os.path.join(os.path.dirname(__file__), "..")


def alembic(url, *args, env=None):
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", *args], cwd=BACKEND, capture_output=True, text=True,
                       env=dict(os.environ, PLAN24_DB_MIGRATE_URL=url, **(env or {})))
    return r


def drift(url):
    """Различия между БД и моделями (пусто — миграции описывают модели полностью)."""
    eng = sa.create_engine(url)
    with eng.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
    eng.dispose()
    return [d for d in diff if not (isinstance(d, tuple) and False)]


# ---------------------------------------------------------------- SQLite (любой режим)
def test_sqlite_migrations_match_models_and_are_repeatable(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.sqlite3'}"
    assert alembic(url, "upgrade", "head").returncode == 0
    assert alembic(url, "upgrade", "head").returncode == 0                       # повторный запуск безопасен
    assert drift(url) == []
    assert alembic(url, "downgrade", "base").returncode == 0
    eng = sa.create_engine(url)
    assert sa.inspect(eng).get_table_names() == ["alembic_version"]


# ---------------------------------------------------------------- PostgreSQL
pg = pytest.mark.skipif(not PG_MODE, reason="нужен PostgreSQL")


@pytest.fixture()
def fresh(request):
    """Пустая БД PostgreSQL со схемой и правами, как после init-roles.sql (без миграций)."""
    request.getfixturevalue("pg_db")                                              # роли уже созданы
    name = "plan24_mig"
    sup = sa.create_engine(PG_SUPER, isolation_level="AUTOCOMMIT")
    with sup.connect() as c:
        c.execute(sa.text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        c.execute(sa.text(f"CREATE DATABASE {name} OWNER plan24_owner TEMPLATE template0"))
    base = PG_SUPER.rsplit("/", 1)[0] + "/" + name
    with sa.create_engine(base, isolation_level="AUTOCOMMIT").connect() as c:
        for q in ("ALTER SCHEMA public OWNER TO plan24_owner", "REVOKE ALL ON SCHEMA public FROM PUBLIC",
                  "GRANT USAGE ON SCHEMA public TO plan24_app, plan24_admin"):
            c.execute(sa.text(q))
    from sqlalchemy.engine import make_url
    u = make_url(PG_SUPER)
    mk = lambda user, pw: u.set(username=user, password=pw, database=name).render_as_string(hide_password=False)

    class F:
        owner, app, admin = mk("plan24_owner", "owner-pw"), mk("plan24_app", "app-pw"), mk("plan24_admin", "admin-pw")
    yield F
    with sup.connect() as c:
        c.execute(sa.text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
    sup.dispose()


@pg
def test_postgres_migrations_match_models(fresh):
    r = alembic(fresh.owner, "upgrade", "head")
    assert r.returncode == 0, r.stderr
    assert drift(fresh.owner) == []
    assert alembic(fresh.owner, "upgrade", "head").returncode == 0               # идемпотентность


@pg
def test_postgres_downgrade_to_base_and_upgrade_again(fresh):
    assert alembic(fresh.owner, "upgrade", "head").returncode == 0
    assert alembic(fresh.owner, "downgrade", "0001").returncode == 0
    eng = sa.create_engine(fresh.owner)
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM pg_policies")).scalar() == 0
        assert c.execute(sa.text("SELECT count(*) FROM pg_class WHERE relrowsecurity")).scalar() == 0
    assert alembic(fresh.owner, "downgrade", "base").returncode == 0
    with eng.connect() as c:
        assert [r[0] for r in c.execute(sa.text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))] == ["alembic_version"]
    assert alembic(fresh.owner, "upgrade", "head").returncode == 0
    eng.dispose()


@pg
def test_rls_migration_refuses_without_app_role(fresh):
    assert alembic(fresh.owner, "upgrade", "0001").returncode == 0
    r = alembic(fresh.owner, "upgrade", "head", env={"PLAN24_APP_ROLE": "no_such_role"})
    assert r.returncode != 0 and "init-roles.sql" in r.stderr
    eng = sa.create_engine(fresh.owner)
    with eng.connect() as c:                                                      # транзакционная DDL: ничего не применилось наполовину
        assert c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == "0001"
        assert c.execute(sa.text("SELECT count(*) FROM pg_policies")).scalar() == 0
    eng.dispose()


@pg
def test_existing_data_survives_the_rls_upgrade(fresh):
    """Обновление боевой БД: данные, созданные до RLS, остаются целыми и видны владельцу организации."""
    assert alembic(fresh.owner, "upgrade", "0001").returncode == 0
    own = sa.create_engine(fresh.owner)
    with own.begin() as c:
        c.execute(sa.text("INSERT INTO tenants (id, name, timezone, industry, plan, created_at) VALUES (7,'Старый цех','UTC','x','trial', now())"))
        c.execute(sa.text("INSERT INTO projects (tenant_id, name, customer, version) VALUES (7,'Проект до миграции','',1)"))
        c.execute(sa.text("INSERT INTO tenants (id, name, timezone, industry, plan, created_at) VALUES (8,'Другой','UTC','x','trial', now())"))
        c.execute(sa.text("INSERT INTO projects (tenant_id, name, customer, version) VALUES (8,'Чужой','',1)"))
    assert alembic(fresh.owner, "upgrade", "head").returncode == 0
    app = sa.create_engine(fresh.app)
    with app.begin() as c:
        assert c.execute(sa.text("SELECT count(*) FROM projects")).scalar() == 0              # без контекста — ничего
        c.exec_driver_sql("SELECT set_config('app.tenant_id', '7', true)")
        assert [r[0] for r in c.execute(sa.text("SELECT name FROM projects"))] == ["Проект до миграции"]
    with own.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM projects")).scalar() == 2              # владелец (миграции) видит всё
    app.dispose(); own.dispose()


@pg
def test_app_refuses_to_start_with_privileged_role_or_stale_schema(fresh, monkeypatch):
    from plan24 import db as dbm
    assert alembic(fresh.owner, "upgrade", "head").returncode == 0
    monkeypatch.delenv("PLAN24_ALLOW_PRIVILEGED_DB", raising=False)
    for url in (fresh.owner, fresh.admin):                                       # владелец таблиц и роль с BYPASSRLS
        with pytest.raises(RuntimeError, match="обхода RLS"):
            dbm.init(url)
    monkeypatch.setenv("PLAN24_ALLOW_PRIVILEGED_DB", "1")                          # осознанный обход (только разработка)
    dbm.init(fresh.owner)
    monkeypatch.delenv("PLAN24_ALLOW_PRIVILEGED_DB")
    dbm.init(fresh.app)                                                            # штатная роль — запускается
    own = sa.create_engine(fresh.owner)
    with own.begin() as c:
        c.execute(sa.text("UPDATE alembic_version SET version_num='0001'"))
    with pytest.raises(RuntimeError, match="alembic"):
        dbm.init(fresh.app)
    own.dispose()


@pg
def test_init_roles_script_is_idempotent_and_creates_restricted_roles():
    """deploy/postgres/init-roles.sql можно запускать повторно; создаёт роли с нужными атрибутами."""
    root = os.path.join(BACKEND, "..", "deploy", "postgres", "init-roles.sql")
    sup = sa.create_engine(PG_SUPER, isolation_level="AUTOCOMMIT")
    with sup.connect() as c:
        c.execute(sa.text("DROP DATABASE IF EXISTS plan24 WITH (FORCE)"))
    from sqlalchemy.engine import make_url
    u = make_url(PG_SUPER)
    env = dict(os.environ, PGPASSWORD=u.password)
    cmd = ["psql", "-h", u.host, "-U", u.username, "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-v", "owner_pw='o1'", "-v", "app_pw='a1'",
           "-v", "admin_pw='d1'", "-q", "-f", root]
    for _ in range(2):
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
    with sup.connect() as c:
        rows = {r[0]: (r[1], r[2]) for r in c.execute(sa.text("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname LIKE 'plan24_%'"))}
        assert rows["plan24_app"] == (False, False) and rows["plan24_owner"] == (False, False) and rows["plan24_admin"] == (False, True)
        assert c.execute(sa.text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='plan24'")).scalar() == "plan24_owner"
        assert c.execute(sa.text("SELECT has_schema_privilege('plan24_app','public','CREATE')")).scalar() is False   # приложение не создаёт объекты
    sup.dispose()
