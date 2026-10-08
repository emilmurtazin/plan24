"""Одноразовые токены из писем (подтверждение, сброс пароля, приглашение). В БД — только sha256 токена."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import config
from .models import EmailToken, User, utcnow


def _h(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue(db: Session, user: User, kind: str) -> str:
    """Выдаёт токен и аннулирует прежние неиспользованные того же рода (действует только последняя ссылка)."""
    db.execute(update(EmailToken).where(EmailToken.user_id == user.id, EmailToken.kind == kind,
                                        EmailToken.used_at.is_(None)).values(used_at=utcnow()))
    raw = secrets.token_urlsafe(32)
    db.add(EmailToken(user_id=user.id, kind=kind, token_hash=_h(raw),
                      expires_at=utcnow() + timedelta(seconds=config.EMAIL_TOKEN_TTL[kind])))
    db.flush()
    return raw


def consume(db: Session, raw: str, kinds: tuple) -> User | None:
    """Погашает токен и возвращает пользователя; None — токен неизвестен, использован, просрочен или пользователь отключён.
    Поиск — до входа (режим только чтения); погашение — уже в контексте организации пользователя."""
    from . import db as dbm
    dbm.auth_scope(db, True)
    t = db.scalar(select(EmailToken).where(EmailToken.token_hash == _h(raw or "")))
    u = db.get(User, t.user_id) if t else None
    if not t or not u or not u.is_active or t.kind not in kinds or t.used_at is not None or t.expires_at < utcnow():
        dbm.auth_scope(db, False)
        return None
    dbm.set_tenant(db, u.tenant_id)
    t.used_at = utcnow()
    return u
