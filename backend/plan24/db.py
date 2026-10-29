"""Слой БД: SQLite (разработка) и PostgreSQL (боевой режим) с защитой на уровне строк (RLS).

PostgreSQL:
  * схему ведёт Alembic (backend/migrations); приложение при старте проверяет, что схема на последней ревизии;
  * приложение работает под ролью без прав владельца и без обхода RLS;
  * в каждой транзакции выставляются app.tenant_id и app.auth_scope (см. migrations/versions/0002_rls_and_grants.py).
    Без app.tenant_id политики не пропускают ни одной строки — забытый фильтр в коде не приводит к утечке.
SQLite: одна организация на файл БД не требуется, изоляция — фильтрами приложения (RLS в SQLite нет).
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

from fastapi import Depends, Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .models import Base

_engine = None
_Session = None
_admin_engine = None
_AdminSession = None
LOCK_CLASS = 7241            # пространство advisory-блокировок Plan 24 (блокировка перепланирования организации)
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def is_pg() -> bool:
    return _engine is not None and _engine.dialect.name == "postgresql"


# ------------------------------------------------------------------ события сессии
@event.listens_for(Session, "after_begin")
def _on_begin(session, transaction, connection):
    if transaction.nested:                          # SAVEPOINT — внутри уже начатой транзакции
        return
    if connection.dialect.name == "sqlite":
        # запись, начатая в «устаревшем» снимке WAL, получает немедленный отказ («database is locked»):
        # запросы на изменение сразу берут блокировку записи, чтение — обычный снимок
        connection.exec_driver_sql("BEGIN IMMEDIATE" if session.info.get("write") else "BEGIN")
    elif connection.dialect.name == "postgresql":
        _apply_context(session, connection)


def _apply_context(session: Session, connection=None):
    """Транзакционные настройки для RLS. is_local=true: действуют до конца транзакции и не «утекают» в пул."""
    tid, auth = session.info.get("tenant_id"), session.info.get("auth_scope")
    sql = ("SELECT set_config('app.tenant_id', %s, true), set_config('app.auth_scope', %s, true), "
           "set_config('app.platform_scope', %s, true)")
    args = (str(tid) if tid else "", "on" if auth else "", "on" if session.info.get("platform_scope") else "")
    if connection is not None:
        connection.exec_driver_sql(sql, args)
    elif session.in_transaction() and is_pg():
        session.connection().exec_driver_sql(sql, args)


def set_tenant(session: Session, tenant_id: int | None):
    """Закрепляет организацию за сессией (и выключает режим чтения при входе). Все дальнейшие запросы — только её данные."""
    session.info["tenant_id"] = tenant_id
    session.info["auth_scope"] = False
    session.info["platform_scope"] = False
    _apply_context(session)


def auth_scope(session: Session, on: bool = True):
    """Режим «только чтение для поиска учётной записи до входа» (users, токены, привязки Telegram).
    Допускается ТОЛЬКО в точках входа без авторизации; записывать в этом режиме нельзя (политики это запрещают)."""
    session.info["auth_scope"] = on
    _apply_context(session)


def platform_scope(session: Session, on: bool = True):
    """Режим суперадмина платформы: чтение организаций и их сводных данных и несколько точечных записей
    (блокировка, отключение пользователей, токены). Включается ТОЛЬКО после проверки токена суперадмина
    (routers/superadmin.py); любой вход в организацию (set_tenant) этот режим снимает."""
    session.info["platform_scope"] = on
    _apply_context(session)


# ------------------------------------------------------------------ инициализация
def init(url: str | None = None, *, check_schema: bool = True, admin_url: str | None = None):
    global _engine, _Session, _admin_engine, _AdminSession
    url = url or config.db_url()
    if url.startswith("sqlite"):
        _engine = create_engine(url, connect_args={"check_same_thread": False})

        # Явное управление транзакциями: BEGIN в начале каждой, WAL — читатель видит единый снимок
        @event.listens_for(_engine, "connect")
        def _on_connect(dbapi, _):
            dbapi.isolation_level = None
            cur = dbapi.cursor()
            for pragma in ("PRAGMA foreign_keys=ON", "PRAGMA journal_mode=WAL", "PRAGMA busy_timeout=5000"):
                cur.execute(pragma)
            cur.close()
        Base.metadata.create_all(_engine)
    else:
        _engine = create_engine(url, pool_pre_ping=True, **config.pool_settings(),
                                connect_args={"options": "-c application_name=plan24"})
        if check_schema:
            cur, head = schema_revision()
            if cur != head:
                raise RuntimeError(f"Схема БД на ревизии {cur!r}, нужна {head!r}. Выполните: alembic -c backend/alembic.ini upgrade head")
            assert_restricted_role()
    _Session = sessionmaker(_engine, expire_on_commit=False)
    admin = admin_url or config.admin_db_url()
    if admin:
        _admin_engine = create_engine(admin, pool_pre_ping=True)
        _AdminSession = sessionmaker(_admin_engine, expire_on_commit=False)
    else:
        _admin_engine, _AdminSession = None, None
    return _engine


def assert_restricted_role():
    """Приложение не должно работать под суперпользователем, владельцем таблиц или ролью с BYPASSRLS —
    иначе защита на уровне строк для него не действует."""
    if os.environ.get("PLAN24_ALLOW_PRIVILEGED_DB") == "1":
        return
    with _engine.connect() as c:
        su, bypass, owner = c.execute(text(
            "SELECT r.rolsuper, r.rolbypassrls, COALESCE((SELECT c.relowner = r.oid FROM pg_class c "
            "WHERE c.relname = 'users' AND c.relnamespace = 'public'::regnamespace), false) "
            "FROM pg_roles r WHERE r.rolname = current_user")).one()
    if su or bypass or owner:
        raise RuntimeError("Приложение подключено ролью с правами обхода RLS (суперпользователь, BYPASSRLS или владелец таблиц). "
                           "Используйте роль plan24_app из deploy/postgres/init-roles.sql")


def new_tenant_id(session: Session) -> int | None:
    """PostgreSQL: идентификатор новой организации выделяется заранее, чтобы политика RLS пропустила её создание.
    (Создавать организацию с чужим id нельзя: политика требует id = app.tenant_id.)"""
    if is_pg():
        return session.execute(text("SELECT nextval(pg_get_serial_sequence('tenants', 'id'))")).scalar()
    return None


def email_taken(session: Session, email: str) -> bool:
    """Глобальная проверка уникальности e-mail: в режиме поиска до входа, потому что RLS скрывает чужие организации."""
    from sqlalchemy import select
    from .models import User
    prev = session.info.get("auth_scope")
    auth_scope(session, True)
    try:
        return session.scalar(select(User.id).where(User.email == email)) is not None
    finally:
        auth_scope(session, bool(prev))


def get_engine():
    if _engine is None:
        init()
    return _engine


def schema_revision() -> tuple:
    """(текущая ревизия БД, последняя ревизия в коде)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    head = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
    with _engine.connect() as c:
        try:
            cur = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:
            cur = None
    return cur, head


def admin_session() -> Session:
    """Сессия с обходом RLS — только для тестов и обслуживания. В SQLite это обычная сессия."""
    if _engine is None:
        init()
    s = (_AdminSession or _Session)()
    s.info["write"] = True
    return s


def job_session() -> Session:
    """Сессия фонового задания (напоминания, бот в режиме опроса): роль приложения, организация выставляется по ходу работы."""
    if _engine is None:
        init()
    s = _Session()
    s.info["write"] = True
    return s


# ------------------------------------------------------------------ зависимость запроса
def get_db(request: Request):
    if _Session is None:
        init()
    s: Session = _Session()
    s.info["write"] = request.method not in ("GET", "HEAD", "OPTIONS")
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# Коммит выполняется ДО отправки ответа (scope="function"): иначе клиент получает 201 и сразу читает
# ещё не сохранённое. По умолчанию FastAPI завершает такие зависимости уже после ответа.
DB = Depends(get_db, scope="function")


# ------------------------------------------------------------------ согласованность перепланирования
def begin_snapshot(session: Session):
    """PostgreSQL: дальнейшие чтения транзакции — один согласованный снимок (REPEATABLE READ).
    Должно вызываться до первого запроса транзакции, поэтому текущая (только читавшая) транзакция завершается."""
    if is_pg():
        session.commit()
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})


@contextlib.contextmanager
def tenant_plan_lock(tenant_id: int):
    """Одно перепланирование организации за раз. Блокировка держится отдельным соединением, пока перепланирование
    не зафиксировано; при обрыве соединения PostgreSQL снимает её сам. В SQLite записи и так упорядочены."""
    if not is_pg():
        yield
        return
    conn = _engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        conn.execute(text("SELECT pg_advisory_lock(:c, :t)"), {"c": LOCK_CLASS, "t": tenant_id})
        yield
    finally:
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:c, :t)"), {"c": LOCK_CLASS, "t": tenant_id})
        finally:
            conn.close()
