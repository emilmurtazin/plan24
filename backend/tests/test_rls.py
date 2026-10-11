"""Защита на уровне строк PostgreSQL: проверяется напрямую в БД под ролью приложения, мимо кода приложения.

Смысл RLS — вторая линия обороны: даже если в коде забыт фильтр по организации, база не отдаст чужие строки.
Тесты выполняются только в PostgreSQL-режиме (PLAN24_TEST_PG_SUPER_URL)."""
import threading

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from conftest import Org, PG_MODE
from plan24.models import Base

pytestmark = pytest.mark.skipif(not PG_MODE, reason="нужен PostgreSQL")

TENANT_TABLES = sorted(t.name for t in Base.metadata.tables.values()
                       if "tenant_id" in t.c and t.name not in ("audit_log",))
USER_TABLES = ["refresh_tokens", "email_tokens"]
AUTH_READ = ["users", "refresh_tokens", "email_tokens", "telegram_links", "telegram_codes"]
NO_RLS = {"telegram_updates", "rate_events", "alembic_version"}


@pytest.fixture()
def two(env, request):
    a, b = Org(env, "a@a.ru", "Цех А"), Org(env, "b@b.ru", "Цех Б")
    a.seed("furniture"); b.seed("windows")
    for o in (a, b):
        o.post("/plans/replan")
        o.post("/telegram/codes", "owner", json={"worker_id": o.ids("/workers")[0]})
    urls = request.getfixturevalue("pg_db")
    app = sa.create_engine(urls.app); admin = sa.create_engine(urls.admin)
    ta, tb = a.data["user"]["tenant"]["id"], b.data["user"]["tenant"]["id"]
    yield a, b, app, admin, ta, tb
    app.dispose(); admin.dispose()


def as_tenant(conn, tid=None, auth=False):
    conn.exec_driver_sql("SELECT set_config('app.tenant_id', %s, true), set_config('app.auth_scope', %s, true)",
                         (str(tid) if tid else "", "on" if auth else ""))


def count(conn, table, where=""):
    return conn.execute(sa.text(f"SELECT count(*) FROM {table} {where}")).scalar()


def test_every_table_declares_rls_posture(two):
    """Новая таблица без осознанного решения о RLS — провал теста: забыть защиту нельзя."""
    _, _, app, admin, *_ = two
    with admin.connect() as c:
        rls = {r[0] for r in c.execute(sa.text("SELECT relname FROM pg_class WHERE relkind='r' AND relrowsecurity AND relnamespace='public'::regnamespace"))}
        allt = {r[0] for r in c.execute(sa.text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
        pols = {r[0] for r in c.execute(sa.text("SELECT DISTINCT tablename FROM pg_policies"))}
    assert allt - rls == NO_RLS, f"таблицы без RLS и без пометки в списке исключений: {allt - rls - NO_RLS}"
    assert rls <= pols, f"RLS включена, но нет политик (никто ничего не увидит): {rls - pols}"
    # любая таблица модели с tenant_id обязана быть под RLS
    assert {t.name for t in Base.metadata.tables.values() if "tenant_id" in t.c} <= rls


def test_app_role_is_restricted(two):
    _, _, app, *_ = two
    with app.connect() as c:
        su, bypass = c.execute(sa.text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
        owns = c.execute(sa.text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user")).scalar()
    assert (su, bypass, owns) == (False, False, 0)


def test_no_tenant_context_means_no_rows_and_no_writes(two):
    a, b, app, admin, ta, tb = two
    with admin.connect() as c:
        assert count(c, "projects") > 0 and count(c, "workers") > 0           # данные в БД есть
    with app.begin() as c:
        for t in TENANT_TABLES + USER_TABLES + ["tenants", "audit_log"]:
            assert count(c, t) == 0, f"{t}: без контекста организации строки видны"
    with pytest.raises(DBAPIError, match="row-level security"):
        with app.begin() as c:
            c.execute(sa.text("INSERT INTO projects (tenant_id, name, customer, version) VALUES (:t, 'x', '', 1)"), {"t": ta})


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_tenant_sees_only_own_rows_and_cannot_touch_foreign(two, table):
    a, b, app, admin, ta, tb = two
    with admin.connect() as c:
        own, foreign = count(c, table, f"WHERE tenant_id={ta}"), count(c, table, f"WHERE tenant_id={tb}")
    with app.begin() as c:
        as_tenant(c, ta)
        assert count(c, table) == own, f"{table}: видно не только своё"
        assert count(c, table, f"WHERE tenant_id={tb}") == 0
        # чужие строки нельзя ни изменить, ни удалить: затронуто 0 строк
        pk = [col.name for col in Base.metadata.tables[table].primary_key.columns][0]
        assert c.execute(sa.text(f"DELETE FROM {table} WHERE tenant_id={tb}")).rowcount == 0
        assert c.execute(sa.text(f"UPDATE {table} SET {pk}={pk} WHERE tenant_id={tb}")).rowcount == 0
        c.rollback()
    with admin.connect() as c:
        assert count(c, table, f"WHERE tenant_id={tb}") == foreign                 # чужие данные целы


def test_inserting_a_row_for_another_tenant_is_rejected(two):
    a, b, app, admin, ta, tb = two
    for target in (tb, 999999):
        with pytest.raises(DBAPIError, match="row-level security"):
            with app.begin() as c:
                as_tenant(c, ta)
                c.execute(sa.text("INSERT INTO projects (tenant_id, name, customer, version) VALUES (:t, 'взлом', '', 1)"), {"t": target})
    with app.begin() as c:                                                           # а в свою — можно
        as_tenant(c, ta)
        c.execute(sa.text("INSERT INTO projects (tenant_id, name, customer, version) VALUES (:t, 'мой', '', 1)"), {"t": ta})
    # и нельзя «перенести» свою строку в чужую организацию
    with pytest.raises(DBAPIError, match="row-level security"):
        with app.begin() as c:
            as_tenant(c, ta)
            c.execute(sa.text("UPDATE projects SET tenant_id=:t WHERE name='мой'"), {"t": tb})


def test_token_tables_are_isolated_through_their_user(two):
    a, b, app, admin, ta, tb = two
    with admin.connect() as c:
        mine = c.execute(sa.text(f"SELECT count(*) FROM refresh_tokens r JOIN users u ON u.id=r.user_id WHERE u.tenant_id={ta}")).scalar()
        total = count(c, "refresh_tokens")
    assert 0 < mine < total
    with app.begin() as c:
        as_tenant(c, ta)
        assert count(c, "refresh_tokens") == mine and count(c, "email_tokens") > 0
        assert c.execute(sa.text("DELETE FROM refresh_tokens WHERE id IN (SELECT r.id FROM refresh_tokens r)")).rowcount == mine
        c.rollback()


def test_auth_scope_is_read_only_and_limited_to_lookup_tables(two):
    a, b, app, admin, ta, tb = two
    with admin.connect() as c:
        totals = {t: count(c, t) for t in AUTH_READ}
    with app.begin() as c:
        as_tenant(c, None, auth=True)
        for t in AUTH_READ:
            assert count(c, t) == totals[t], f"{t}: в режиме поиска до входа должно быть видно всё"
        for t in ("projects", "workers", "order_lines", "facts", "plans", "audit_log", "tenants"):
            assert count(c, t) == 0, f"{t} не должна читаться в режиме поиска до входа"
    for stmt in ("UPDATE users SET name='x'", "DELETE FROM users", "UPDATE refresh_tokens SET revoked_at=now()",
                 "DELETE FROM telegram_links", "UPDATE telegram_codes SET used_at=now()"):
        with app.begin() as c:
            as_tenant(c, None, auth=True)
            assert c.execute(sa.text(stmt)).rowcount == 0, f"в режиме чтения выполнено изменение: {stmt}"
    with pytest.raises(DBAPIError, match="row-level security"):
        with app.begin() as c:
            as_tenant(c, None, auth=True)
            c.execute(sa.text("INSERT INTO refresh_tokens (user_id, token_hash, family, expires_at) VALUES (1, 'h', 'f', now())"))


def test_audit_log_is_append_only_for_the_app_role(two):
    a, b, app, admin, ta, tb = two
    with app.begin() as c:
        as_tenant(c, ta)
        assert count(c, "audit_log") > 0
        c.execute(sa.text("INSERT INTO audit_log (tenant_id, entity, action, changes, created_at) VALUES (:t,'x','y','{}', now())"), {"t": ta})
    for stmt in ("UPDATE audit_log SET action='подделка'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
        with pytest.raises(DBAPIError, match="permission denied"):
            with app.begin() as c:
                as_tenant(c, ta)
                c.execute(sa.text(stmt))
    with pytest.raises(DBAPIError, match="row-level security"):          # и записать «от имени» другой организации нельзя
        with app.begin() as c:
            as_tenant(c, ta)
            c.execute(sa.text("INSERT INTO audit_log (tenant_id, entity, action, changes, created_at) VALUES (:t,'x','y','{}', now())"), {"t": tb})


def test_tenants_table_only_self_and_never_deletable(two):
    a, b, app, admin, ta, tb = two
    with app.begin() as c:
        as_tenant(c, ta)
        assert [r[0] for r in c.execute(sa.text("SELECT id FROM tenants"))] == [ta]
    with pytest.raises(DBAPIError, match="permission denied"):
        with app.begin() as c:
            as_tenant(c, ta)
            c.execute(sa.text("DELETE FROM tenants"))
    with pytest.raises(DBAPIError, match="row-level security"):
        with app.begin() as c:
            as_tenant(c, ta)
            c.execute(sa.text("INSERT INTO tenants (id, name, timezone, industry, plan, created_at) VALUES (999999,'чужой','UTC','x','trial', now())"))


def test_context_does_not_leak_between_pooled_connections(two):
    a, b, app, admin, ta, tb = two
    from plan24 import db as dbm
    eng = dbm.get_engine()
    for _ in range(25):                                            # пройти по всем соединениям пула
        assert a.get("/projects").status_code == 200
        with eng.connect() as c:
            assert (c.execute(sa.text("SELECT current_setting('app.tenant_id', true)")).scalar() or "") == ""
            assert count(c, "projects") == 0


def test_token_with_foreign_tenant_claim_is_rejected(two):
    from plan24 import security
    a, b, app, admin, ta, tb = two
    uid = a.data["user"]["id"]
    forged = security.make_access(uid, tb)                         # пользователь A, но «организация B» (подпись валидна)
    r = a.c.get("/api/v1/projects", headers={"Authorization": "Bearer " + forged})
    assert r.status_code == 401
    assert a.c.get("/api/v1/projects", headers={"Authorization": "Bearer " + security.make_access(uid, ta)}).status_code == 200


@pytest.mark.parametrize("path", ["/work-centers", "/workers", "/projects", "/products", "/order-lines", "/fact-reasons"])
def test_rls_alone_stops_leaks_when_app_filters_are_removed(two, monkeypatch, path):
    """Имитация ошибки разработчика: фильтр по организации в коде забыт. База всё равно не отдаёт чужое."""
    import sys
    from plan24 import deps
    a, b, *_ = two
    own_ids, foreign_ids = set(a.ids(path)), set(b.ids(path))
    orig = deps.scoped

    def leaky(model, user):                                       # без where tenant_id == ...
        q = sa.select(model)
        return q.where(model.deleted_at.is_(None)) if hasattr(model, "deleted_at") else q
    for mod in list(sys.modules.values()):
        if getattr(mod, "scoped", None) is orig:
            monkeypatch.setattr(mod, "scoped", leaky)
    assert set(a.ids(path)) == own_ids and not (set(a.ids(path)) & foreign_ids)
    for i in foreign_ids:
        assert a.get(f"{path}/{i}").status_code == 404
        assert a.patch(f"{path}/{i}", json={}).status_code == 404
        assert a.delete(f"{path}/{i}").status_code == 404
    assert [x["id"] for x in b.get(path).json()["items"]] == sorted(foreign_ids)   # у B ничего не изменилось


# ---------------------------------------------------------------- согласованность и конкуренция
def test_parallel_replans_of_one_org_serialize_and_leave_one_active_plan(two, request):
    import conftest
    a, b, app, admin, ta, tb = two
    ids, errs = [], []

    def run():
        r = a.c.post("/api/v1/plans/replan", json={}, headers=a.users["owner"])
        (ids if r.status_code == 201 else errs).append(r.json().get("id") or r.text[:100])
    ths = [threading.Thread(target=run) for _ in range(5)]
    [t.start() for t in ths]; [t.join() for t in ths]
    assert not errs, errs
    assert len(set(ids)) == 5
    with admin.connect() as c:
        assert count(c, "plans", f"WHERE tenant_id={ta} AND status='active'") == 1
        assert count(c, "plans", f"WHERE tenant_id={tb} AND status='active'") == 1


def test_planning_reads_one_snapshot_even_if_data_changes_midway(two):
    """REPEATABLE READ: изменения, зафиксированные другой транзакцией посреди расчёта, расчёт не видят."""
    from plan24 import db as dbm
    from plan24.models import Project
    a, b, app, admin, ta, tb = two
    s = dbm.job_session(); dbm.set_tenant(s, ta)
    s2 = dbm.admin_session()
    try:
        dbm.begin_snapshot(s)
        before = s.scalar(sa.select(sa.func.count()).select_from(Project))
        s2.add(Project(tenant_id=ta, name="появился во время расчёта")); s2.commit()
        assert s.scalar(sa.select(sa.func.count()).select_from(Project)) == before
        s.rollback()
        assert s.scalar(sa.select(sa.func.count()).select_from(Project)) == before + 1
    finally:
        s.close(); s2.close()


def test_ready_probe_reports_schema_drift(two, request):
    a, *_ = two
    urls = request.getfixturevalue("pg_db")
    assert a.c.get("/api/v1/health/ready").json()["database"] == "postgresql"
    own = sa.create_engine(urls.owner)
    try:
        with own.begin() as c:
            c.execute(sa.text("UPDATE alembic_version SET version_num='0001'"))
        r = a.c.get("/api/v1/health/ready")
        assert r.status_code == 503 and "ревизии" in r.json()["title"]
    finally:
        with own.begin() as c:
            c.execute(sa.text("UPDATE alembic_version SET version_num='0004'"))
        own.dispose()
    assert a.c.get("/api/v1/health/ready").status_code == 200


def test_rate_limiter_state_is_shared_through_db_and_stores_only_hashes(two):
    a, *_ = two
    for _ in range(5):
        assert a.c.post("/api/v1/auth/login", json={"email": "secret-person@a.ru", "password": "wrong-password"}).status_code == 401
    assert a.c.post("/api/v1/auth/login", json={"email": "secret-person@a.ru", "password": "wrong-password"}).status_code == 429
    from plan24 import db as dbm
    s = dbm.admin_session()
    try:
        rows = [r[0] for r in s.execute(sa.text("SELECT key_hash FROM rate_events"))]
    finally:
        s.close()
    assert len(rows) == 5 and all(len(h) == 64 for h in rows) and not any("secret-person" in h for h in rows)


def test_parallel_same_fact_converges_to_one_row(two):
    """Регрессия: гонка «вставить или заменить» итога дня давала 500 на уникальном ограничении."""
    a, *_ = two
    day = a.get("/reports/day").json()
    w = day["workers"][0]; t = w["tasks"][0]
    codes = []

    def put(q):
        r = a.c.post("/api/v1/facts", headers=a.users["owner"], json={"fact_date": day["date"], "order_line_id": t["order_line_id"],
                                                                     "operation_id": t["operation_id"], "worker_id": w["worker_id"], "qty": q})
        codes.append(r.status_code)
    ths = [threading.Thread(target=put, args=(i,)) for i in range(8)]
    [x.start() for x in ths]; [x.join() for x in ths]
    assert set(codes) == {200}, codes
    facts = [f for f in a.get("/facts").json()["items"] if f["worker_id"] == w["worker_id"] and f["operation_id"] == t["operation_id"]]
    assert len(facts) == 1


def test_parallel_same_idempotency_key_returns_same_answer(two):
    a, *_ = two
    day = a.get("/reports/day").json(); w = day["workers"][0]; t = w["tasks"][0]
    out = []

    def put():
        r = a.c.post("/api/v1/facts", headers={**a.users["owner"], "Idempotency-Key": "same-key"},
                     json={"fact_date": day["date"], "order_line_id": t["order_line_id"], "operation_id": t["operation_id"],
                           "worker_id": w["worker_id"], "qty": 3})
        out.append((r.status_code, r.json().get("id")))
    ths = [threading.Thread(target=put) for _ in range(6)]
    [x.start() for x in ths]; [x.join() for x in ths]
    assert {c for c, _ in out} == {200} and len({i for _, i in out}) == 1
