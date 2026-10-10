"""Предполётная проверка перед миграциями: каждая причина сбоя — свой код выхода (виден в логе деплоя App Platform)."""
import os
import subprocess
import sys

import pytest

from conftest import PG_MODE
from plan24 import preflight

BACKEND = os.path.join(os.path.dirname(__file__), "..")


@pytest.mark.parametrize("text,code", [
    ('FATAL:  password authentication failed for user "x"', 13),
    ('connection failed: connection to server at "10.0.0.5", port 5432 failed: timeout expired', 12),
    ('could not translate host name "nope" to address: Name or service not known', 12),
    ('FATAL:  database "zzz" does not exist', 15),
    ('FATAL:  no pg_hba.conf entry for host "1.2.3.4", user "u", database "d", no encryption', 19),
    ('server does not support SSL, but SSL was required', 14),
    ('something unexpected', 1),
])
def test_error_text_is_classified_to_exit_code(text, code):
    assert preflight.classify(Exception(text))[0] == code


def run(env):
    e = {k: v for k, v in os.environ.items() if not k.startswith("PLAN24_DB")}
    e.update(env)
    return subprocess.run([sys.executable, "-m", "plan24.preflight"], cwd=BACKEND, env=e, capture_output=True, text=True)


def test_missing_or_garbled_url_gives_code_10_without_leaking_password():
    assert run({}).returncode == 10
    r = run({"PLAN24_DB_MIGRATE_URL": "not a url", "PLAN24_DB_URL": "postgresql+psycopg://u:SECRETPW@h/d"})
    assert r.returncode == 10 and "SECRETPW" not in r.stdout + r.stderr
    r = run({"PLAN24_DB_MIGRATE_URL": "postgresql+psycopg://u:SECRETPW@/d", "PLAN24_DB_URL": "postgresql+psycopg://u:p@h/d"})
    assert r.returncode == 10                                                    # нет хоста


pg = pytest.mark.skipif(not PG_MODE, reason="нужен PostgreSQL")


@pytest.fixture()
def urls(request):
    return request.getfixturevalue("pg_db")


def swap(url, **kw):
    from sqlalchemy.engine import make_url
    return make_url(url).set(**kw).render_as_string(hide_password=False)


@pg
def test_each_failure_reason_has_its_own_exit_code(urls):
    ok = {"PLAN24_DB_MIGRATE_URL": urls.owner, "PLAN24_DB_URL": urls.app}
    r = run(ok)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "подключение приложения ок" in r.stdout
    cases = {
        13: {**ok, "PLAN24_DB_MIGRATE_URL": swap(urls.owner, password="wrong")},          # неверный пароль владельца
        17: {**ok, "PLAN24_DB_URL": swap(urls.app, password="wrong")},                    # роль есть, пароль не тот
        16: {**ok, "PLAN24_DB_URL": swap(urls.app, username="plan24_nobody")},            # роли приложения нет
        15: {**ok, "PLAN24_DB_MIGRATE_URL": swap(urls.owner, database="no_such_db")},      # базы нет
        12: {**ok, "PLAN24_DB_MIGRATE_URL": swap(urls.owner, host="127.0.0.1", port=1)},   # порт закрыт
    }
    for code, env in cases.items():
        r = run(env)
        assert r.returncode == code, f"ожидался код {code}, получен {r.returncode}: {r.stdout}{r.stderr}"
        assert "wrong" not in r.stdout + r.stderr and "owner-pw" not in r.stdout + r.stderr   # пароли не печатаются


def test_entrypoint_migrate_propagates_preflight_exit_code(tmp_path):
    """Скрипт контейнера отдаёт платформе именно код preflight, а не общий 1."""
    ep = os.path.join(BACKEND, "..", "deploy", "entrypoint.sh")
    r = subprocess.run(["sh", ep, "migrate"], cwd=BACKEND, capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if not k.startswith("PLAN24_DB")})
    assert r.returncode == 10 and "PLAN24_DB_MIGRATE_URL" in r.stdout
