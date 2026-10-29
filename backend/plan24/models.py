"""Модель данных (ТЗ раздел 7). Во всех таблицах данных организации есть tenant_id.

Изоляция: каждый запрос фильтруется по tenant_id в приложении (plan24/deps.py: scoped());
для PostgreSQL вторая линия защиты — RLS, см. docs/rls.sql. Ссылки между сущностями
проверяются на принадлежность одной организации в слое API (SQLite не поддерживает RLS).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true
from sqlalchemy import (JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


MAX_QUAL_LEVELS = 10


def default_quals(n: int = 3) -> list:
    """Названия уровней квалификации по умолчанию: «Уровень 1» … «Уровень n»."""
    return [f"Уровень {i}" for i in range(1, n + 1)]


ROLES = ("owner", "manager", "technologist", "foreman", "worker", "viewer", "director")


# ---------------------------------------------------------------- ядро
class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    industry: Mapped[str] = mapped_column(String(64), default="production")
    plan: Mapped[str] = mapped_column(String(32), default="trial")
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    blocked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)             # заблокирована суперадмином; пусто — работает
    blocked_reason: Mapped[Optional[str]] = mapped_column(String(300))


class TenantModule(Base):
    __tablename__ = "tenant_modules"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    module_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(200))            # полное имя «Имя Фамилия» — собирается из first_name и last_name
    first_name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    last_name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verify_token: Mapped[Optional[str]] = mapped_column(String(64))
    work_center_id: Mapped[Optional[int]] = mapped_column(ForeignKey("work_centers.id"))   # бригадир
    worker_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workers.id"))             # сборщик
    consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)                       # 152-ФЗ
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class EmailToken(Base):
    """Одноразовые ссылки из писем: подтверждение e-mail, сброс пароля, приглашение. Хранится только хеш."""
    __tablename__ = "email_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))                 # verify | reset | invite
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WorkCenter(Base):
    __tablename__ = "work_centers"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Настройки планирования участка; пусто — действует общее значение из PlannerSettings
    efficiency: Mapped[Optional[float]] = mapped_column(Float)
    max_workers_per_op: Mapped[Optional[int]] = mapped_column(Integer)
    today_load_threshold: Mapped[Optional[float]] = mapped_column(Float)
    min_start_share: Mapped[Optional[float]] = mapped_column(Float)
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)


class Worker(Base):
    __tablename__ = "workers"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    work_center_id: Mapped[Optional[int]] = mapped_column(ForeignKey("work_centers.id"))
    perf: Mapped[float] = mapped_column(Float, default=1.0)
    skill: Mapped[float] = mapped_column(Float, default=1)
    status: Mapped[str] = mapped_column(String(16), default="available")   # available | unavailable
    version: Mapped[int] = mapped_column(Integer, default=1)
    # участвует ли в автопланировании; временный работник, добавленный из «Плана на день», — нет, пока это не включат в настройках
    auto_plan: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    is_temporary: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)


class WorkerAbsence(Base):
    __tablename__ = "worker_absences"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"), index=True)
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class Holiday(Base):
    __tablename__ = "holidays"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    holiday_date: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("tenant_id", "holiday_date"),)


class PlannerSettings(Base):
    __tablename__ = "planner_settings"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    max_workers_per_op: Mapped[int] = mapped_column(Integer, default=4)
    freeze_days: Mapped[int] = mapped_column(Integer, default=2)
    today_load_threshold: Mapped[float] = mapped_column(Float, default=0.6)
    min_start_share: Mapped[float] = mapped_column(Float, default=0.5)
    priority_strictness: Mapped[float] = mapped_column(Float, default=100)
    efficiency: Mapped[float] = mapped_column(Float, default=1.0)          # D-01: 1.0 как в скрипте
    work_weekdays: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4")
    shift_hours: Mapped[float] = mapped_column(Float, default=8)           # только для отображения
    fix_group_start: Mapped[bool] = mapped_column(Boolean, default=False)  # D-04: выкл. по умолчанию
    fact_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)  # FR-22a
    snapshot_on: Mapped[Optional[date]] = mapped_column(Date)           # когда последний раз сохранялся снимок плана по дням
    qualifications: Mapped[list] = mapped_column(JSON, default=default_quals)   # названия уровней квалификации; число уровней = длина списка
    updated_by: Mapped[Optional[int]] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FactReason(Base):
    __tablename__ = "fact_reasons"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, default=1)
    # у каждого участка свои причины; пусто — «общая» причина (видна всем участкам; так заведены причины по умолчанию)
    work_center_id: Mapped[Optional[int]] = mapped_column(ForeignKey("work_centers.id"), index=True)
    __table_args__ = (UniqueConstraint("tenant_id", "work_center_id", "name"),)


# ---------------------------------------------------------------- модуль «Производство»
class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    unit: Mapped[str] = mapped_column(String(24), default="шт")
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)


class Routing(Base):
    __tablename__ = "routings"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(120), default="", server_default="")     # название версии (необязательное)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    operations: Mapped[list["Operation"]] = relationship(order_by="Operation.sequence, Operation.id")


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    routing_id: Mapped[int] = mapped_column(ForeignKey("routings.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    norm_per_day: Mapped[float] = mapped_column(Float)          # выработка за полный день при perf = 1 (BR-1)
    unit: Mapped[str] = mapped_column(String(24), default="шт")
    min_skill: Mapped[float] = mapped_column(Float, default=1)
    sequence: Mapped[float] = mapped_column(Float, default=1)
    per_item: Mapped[float] = mapped_column(Float, default=1)
    hold_qty: Mapped[float] = mapped_column(Float, default=0)
    hold_reason: Mapped[str] = mapped_column(String(200), default="")
    hold_until: Mapped[Optional[date]] = mapped_column(Date)
    # партия: следующая операция стартует, когда предыдущая выполнена на столько процентов (пусто/100 — после полного окончания)
    start_after_pct: Mapped[Optional[float]] = mapped_column(Float)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("routing_id", "name"),)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    customer: Mapped[str] = mapped_column(String(160), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)


class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    routing_id: Mapped[int] = mapped_column(ForeignKey("routings.id"))     # фиксирует версию маршрута
    ext_id: Mapped[Optional[str]] = mapped_column(String(64))
    customer: Mapped[str] = mapped_column(String(160), default="", server_default="")   # заказчик позиции
    qty: Mapped[float] = mapped_column(Float)
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="planned")     # draft | planned | done | cancelled
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    __table_args__ = (Index("ux_order_ext", "tenant_id", "ext_id", unique=True,
                            sqlite_where=text("ext_id IS NOT NULL AND deleted_at IS NULL"),
                            postgresql_where=text("ext_id IS NOT NULL AND deleted_at IS NULL")),)


class WorkerOrderLine(Base):
    """Допуск исполнителя к позициям; у исполнителя нет строк = допущен ко всем."""
    __tablename__ = "worker_order_lines"
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"), primary_key=True)
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id"), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)


class OrderLineOperation(Base):
    """Распределение операций позиции заказа по участкам: операция выполняется работниками назначенных участков.
    У операции может быть несколько участков. Нет строк = операция не распределена (ошибка планирования)."""
    __tablename__ = "order_line_operations"
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id"), primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"), primary_key=True)
    work_center_id: Mapped[int] = mapped_column(ForeignKey("work_centers.id"), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)


class DayPin(Base):
    """День работника закреплён вручную: перепланирование его не меняет (правки «Плана на день» сохраняются)."""
    __tablename__ = "day_pins"
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"), primary_key=True)
    plan_date: Mapped[date] = mapped_column(Date, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)


class ExtraTask(Base):
    """Работа вне перечня операций: по заказу (order_line_id задан) или не относящаяся к заказу (None). Учёт — в часах."""
    __tablename__ = "extra_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"))
    order_line_id: Mapped[Optional[int]] = mapped_column(ForeignKey("order_lines.id"))
    title: Mapped[str] = mapped_column(String(200))
    hours: Mapped[float] = mapped_column(Float)                       # запланировано часов
    fact_hours: Mapped[Optional[float]] = mapped_column(Float)        # сделано часов (None — факт не внесён)
    reason_id: Mapped[Optional[int]] = mapped_column(ForeignKey("fact_reasons.id"))
    note: Mapped[str] = mapped_column(String(400), default="")
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DayPlanSnapshot(Base):
    """«Что было запланировано на день»: копия строк активного плана на сегодня и будущее, обновляется при перепланировании,
    ручной правке и раз в сутки. Прошедшие дни не перезаписываются — по ним считается недовыполнение плана.
    Ссылки без внешних ключей: история не должна мешать удалению справочников."""
    __tablename__ = "day_plan_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    order_line_id: Mapped[int] = mapped_column(Integer)
    operation_id: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[int] = mapped_column(Integer)
    work_center_id: Mapped[Optional[int]] = mapped_column(Integer)      # участок работника на момент снимка
    qty: Mapped[int] = mapped_column(Integer)


# ---------------------------------------------------------------- факт, планы, аудит
class Fact(Base):
    __tablename__ = "facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    fact_date: Mapped[date] = mapped_column(Date, index=True)
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id"))
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"))
    qty: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")   # submitted | confirmed
    reason_id: Mapped[Optional[int]] = mapped_column(ForeignKey("fact_reasons.id"))
    note: Mapped[str] = mapped_column(String(400), default="")
    source: Mapped[str] = mapped_column(String(16), default="web")        # web | telegram | import
    entered_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    confirmed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # итог дня по исполнителю: повторный ввод заменяет значение (FR-23)
    __table_args__ = (UniqueConstraint("tenant_id", "fact_date", "order_line_id", "operation_id", "worker_id"),)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")     # active | archived
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)             # отчёты на момент расчёта
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # один активный план на организацию — частичный уникальный индекс
    __table_args__ = (Index("ux_plan_active", "tenant_id", unique=True,
                            sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'")),)


class PlanItem(Base):
    __tablename__ = "plan_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)     # для RLS и быстрых выборок
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), index=True)
    plan_date: Mapped[date] = mapped_column(Date)
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id"))
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"))
    qty: Mapped[int] = mapped_column(Integer)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)       # ручная фиксация — R2
    reason_code: Mapped[str] = mapped_column(String(24), default="FREE_CAPACITY")
    __table_args__ = (Index("ix_plan_items_date", "plan_id", "plan_date"),
                      Index("ix_plan_items_worker", "plan_id", "worker_id"))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(24))
    changes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# ---------------------------------------------------------------- Telegram
class TelegramLink(Base):
    """Привязка чата к исполнителю (без учётной записи) или к пользователю-бригадиру. Один чат — одна личность."""
    __tablename__ = "telegram_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    worker_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workers.id"), unique=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), unique=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # личные настройки уведомлений: {"m_on": bool, "m_at": "07:30", "e_on": bool, "e_at": "17:30", "alerts": bool}; пусто — по умолчанию
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


class TelegramCode(Base):
    """Одноразовый код привязки, выдаётся руководителем. Хранится только хеш."""
    __tablename__ = "telegram_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    worker_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workers.id"))
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TelegramState(Base):
    """Состояние диалога: ожидаемое число, подтверждение, последний ввод (для «отменить»)."""
    __tablename__ = "telegram_state"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TelegramUpdate(Base):
    """Обработанные update_id: Telegram повторяет доставку, дублей быть не должно."""
    __tablename__ = "telegram_updates"
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class NotificationLog(Base):
    __tablename__ = "notification_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    key: Mapped[str] = mapped_column(String(64))
    sent_on: Mapped[date] = mapped_column(Date)
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "key", "sent_on"),)


class RateEvent(Base):
    """События для ограничения частоты (вход, сброс пароля, коды бота). Общие для всех процессов приложения.
    Хранится только хеш ключа (e-mail/IP/чат), данных организаций нет — RLS не применяется."""
    __tablename__ = "rate_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
