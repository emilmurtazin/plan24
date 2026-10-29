"""Зависимости API: текущий пользователь, роли, изоляция по организации."""
from __future__ import annotations

from datetime import date

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db as dbm
from .db import DB
from .models import Tenant, User
from .security import decode_access_full

ALL = ("owner", "manager", "technologist", "foreman", "worker", "viewer", "director")
MANAGERS = ("owner", "manager")
STAFF = ("owner", "manager", "foreman")
READERS = ("owner", "manager", "technologist", "foreman", "viewer", "director")
CATALOG = ("owner", "manager", "technologist")        # создание и редактирование каталога изделий


def current_user(request: Request, db: Session = DB) -> User:
    auth = request.headers.get("authorization", "")
    ids = decode_access_full(auth[7:]) if auth.lower().startswith("bearer ") else None
    if ids is None:
        raise HTTPException(401, "Требуется вход")
    uid, tid, support = ids
    dbm.set_tenant(db, tid)           # контекст RLS — до первого обращения к данным; чужой tid строку пользователя не вернёт
    u = db.get(User, uid)
    if not u or not u.is_active or u.tenant_id != tid:           # деактивированный пользователь теряет доступ сразу, не по истечении токена
        raise HTTPException(401, "Требуется вход")
    if not support:
        ensure_not_blocked(db, tid)
    else:
        db.info["support"] = True     # вход суперадмина под организацией: действия помечаются в журнале, учётные данные менять нельзя
        request.state.support = True
    return u


BLOCKED_MSG = "Доступ организации приостановлен. Обратитесь в поддержку"


def ensure_not_blocked(db: Session, tenant_id: int) -> None:
    """Заблокированная суперадмином организация не работает ни в вебе, ни в Telegram: токены перестают действовать сразу."""
    t = db.get(Tenant, tenant_id)
    if t is None or t.blocked_at is not None:
        raise HTTPException(403, BLOCKED_MSG)


def not_support(request: Request) -> None:
    """Запрет действий над учётными данными при входе поддержки (пароль, e-mail)."""
    if getattr(request.state, "support", False):
        raise HTTPException(403, "В режиме поддержки это действие недоступно")


def require(*roles: str):
    def dep(u: User = Depends(current_user)) -> User:
        if u.role not in roles:
            raise HTTPException(403, "Недостаточно прав")
        return u
    return dep


def scoped(model, user: User):
    """SELECT только по своей организации. Все запросы к данным идут через этот помощник."""
    q = select(model).where(model.tenant_id == user.tenant_id)
    if hasattr(model, "deleted_at"):
        q = q.where(model.deleted_at.is_(None))
    return q


def get_or_404(db: Session, model, obj_id: int, user: User):
    obj = db.scalar(scoped(model, user).where(model.id == obj_id))
    if obj is None:
        raise HTTPException(404, "Запись не найдена")     # чужая запись неотличима от несуществующей
    return obj


def tenant_of(db: Session, user: User) -> Tenant:
    return db.get(Tenant, user.tenant_id)


def get_today(user: User = Depends(current_user), db: Session = DB) -> date:
    """«Сегодня» — в часовом поясе организации (ТЗ 3.2). В тестах подменяется через dependency_overrides."""
    from .timeutil import today_in
    return today_in(tenant_of(db, user).timezone)
