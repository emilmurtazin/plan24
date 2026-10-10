"""Проверка перед миграциями: подключение к БД, права, роль приложения. Нужна на платформах без консоли (App Platform):
в логе деплоя виден только КОД ВЫХОДА контейнера, поэтому каждая причина сбоя имеет свой код (таблица ниже).
Пароли и адреса подключения не печатаются — только хост, пользователь и тип проблемы.

Коды выхода:
  0   всё в порядке
  10  не задана или некорректна переменная PLAN24_DB_MIGRATE_URL / PLAN24_DB_URL
  12  нет сети до БД (неверный адрес/порт, таймаут, имя не найдено)
  13  неверный пароль или пользователь
  14  проблема с SSL (sslmode, сертификат)
  15  такой базы данных нет (проверьте имя базы в адресе)
  16  роль приложения не создана в БД
  17  роль приложения существует, но пароль в PLAN24_DB_URL не подошёл
  18  роль приложения не смогла подключиться по другой причине
  19  сервер БД не разрешает подключение с этого адреса (pg_hba)
  21  у владельца нет права создавать таблицы в схеме public
  1   прочая ошибка подключения (текст — в логе контейнера)
  20  (выдаёт entrypoint) сами миграции Alembic завершились ошибкой
"""
from __future__ import annotations

import os
import sys

import psycopg
from sqlalchemy.engine import make_url

DEFAULT_APP_ROLE = "plan24_app"


def classify(exc: Exception) -> tuple:
    """(код выхода, объяснение) по тексту ошибки psycopg/libpq."""
    m = str(exc).lower()
    if "password authentication failed" in m or "authentication failed" in m:
        return 13, "неверный пароль или имя пользователя"
    if "no pg_hba.conf entry" in m or "not allowed" in m:
        return 19, "сервер БД не разрешает подключение с адреса приложения (проверьте, что приложение и БД в одной приватной сети и используется приватный IP)"
    if "database" in m and "does not exist" in m:
        return 15, "базы данных с таким именем нет — проверьте имя базы в адресе"
    if "ssl" in m or "sslmode" in m or "certificate" in m:
        return 14, "проблема с SSL — проверьте sslmode в адресе подключения"
    if any(s in m for s in ("could not connect", "connection refused", "timeout", "timed out", "could not translate host",
                            "name or service not known", "network is unreachable", "no route to host", "connection failed")):
        return 12, "нет сети до БД: проверьте адрес и порт, что используется ПРИВАТНЫЙ IP и что приложение подключено к той же сети, что и БД"
    return 1, "ошибка подключения: " + type(exc).__name__


def conn_args(url: str) -> dict:
    u = make_url(url)
    if not u.host or not u.database or not u.username:
        raise ValueError("в адресе нет хоста, базы или пользователя")
    q = {k: v for k, v in u.query.items() if k in ("sslmode", "sslrootcert", "sslcert", "sslkey")}
    return dict(host=u.host, port=u.port or 5432, dbname=u.database, user=u.username, password=u.password, connect_timeout=10, **q)


def _fail(code: int, msg: str):
    print(f"[preflight] ОШИБКА (код {code}): {msg}", flush=True)
    sys.exit(code)


def main() -> int:
    mig, app = os.environ.get("PLAN24_DB_MIGRATE_URL"), os.environ.get("PLAN24_DB_URL")
    for name, val in (("PLAN24_DB_MIGRATE_URL", mig), ("PLAN24_DB_URL", app)):
        if not val:
            _fail(10, f"переменная {name} не задана (проверьте «Переменные» приложения)")
    try:
        ma, aa = conn_args(mig), conn_args(app)
    except Exception:
        _fail(10, "адрес подключения не разобран: ожидается postgresql+psycopg://пользователь:пароль@хост:порт/база?sslmode=require "
                  "(пароль со спецсимволами @ / # : ? % ломает адрес — используйте только буквы и цифры)")
    print(f"[preflight] владелец БД: {ma['user']}@{ma['host']}:{ma['port']}/{ma['dbname']} | приложение: {aa['user']}", flush=True)

    try:
        with psycopg.connect(**ma) as c:
            ver = c.execute("SHOW server_version").fetchone()[0]
            can_create = c.execute("SELECT has_schema_privilege(current_user, 'public', 'CREATE')").fetchone()[0]
            role_ok = c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (aa["user"],)).fetchone()
            tables = c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]
    except Exception as e:                                       # noqa: BLE001
        code, why = classify(e)
        _fail(code, f"владелец БД не подключился — {why}")
    print(f"[preflight] подключение владельца ок: PostgreSQL {ver}, таблиц в public: {tables}", flush=True)
    if not can_create:
        _fail(21, "у владельца нет права CREATE в схеме public — миграции не смогут создать таблицы")
    if not role_ok:
        _fail(16, f"роль приложения «{aa['user']}» не создана в БД — создайте этого пользователя в панели (раздел «Пользователи») и повторите деплой")

    try:
        with psycopg.connect(**aa) as c:
            c.execute("SELECT 1")
    except Exception as e:                                       # noqa: BLE001
        code, why = classify(e)
        _fail(17 if code == 13 else 18, f"роль приложения «{aa['user']}» не подключилась — {why}")
    print("[preflight] подключение приложения ок — запускаю миграции", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
