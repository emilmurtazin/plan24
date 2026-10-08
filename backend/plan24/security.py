"""Пароли (argon2), JWT с коротким сроком, refresh с ротацией и обнаружением повторного использования."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import config
from .models import RefreshToken, User, utcnow

_ph = PasswordHasher()
ALGO = "HS256"


def hash_password(p: str) -> str:
    return _ph.hash(p)


def verify_password(h: str, p: str) -> bool:
    try:
        return _ph.verify(h, p)
    except VerificationError:
        return False


def make_access(user_id: int, tenant_id: int) -> str:
    """Токен несёт организацию (tid): по ней выставляется контекст RLS до обращения к базе."""
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "tid": tenant_id, "typ": "access", "iat": now,
                       "exp": now + timedelta(minutes=config.ACCESS_TTL_MIN)}, config.secret_key(), algorithm=ALGO)


def decode_access(token: str) -> tuple | None:
    """→ (id пользователя, id организации) или None."""
    try:
        d = jwt.decode(token, config.secret_key(), algorithms=[ALGO])
        return (int(d["sub"]), int(d["tid"])) if d.get("typ") == "access" else None
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def _h(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def issue_refresh(db: Session, user_id: int, family: str | None = None) -> str:
    raw = secrets.token_urlsafe(48)
    db.add(RefreshToken(user_id=user_id, token_hash=_h(raw), family=family or secrets.token_hex(8),
                        expires_at=utcnow() + timedelta(days=config.REFRESH_TTL_DAYS)))
    return raw


def rotate_refresh(db: Session, raw: str) -> tuple[int, str] | None:
    """Одноразовый refresh: использованный токен отзывается; повтор отозванного = кража → отзыв всей семьи.
    Поиск токена — до входа (режим только чтения), все изменения — уже в контексте организации пользователя."""
    from . import db as dbm
    dbm.auth_scope(db, True)
    t = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _h(raw)))
    user = db.get(User, t.user_id) if t else None
    if not t or not user:
        dbm.auth_scope(db, False)
        return None
    dbm.set_tenant(db, user.tenant_id)
    if t.revoked_at is not None:
        db.execute(update(RefreshToken).where(RefreshToken.family == t.family, RefreshToken.revoked_at.is_(None))
                   .values(revoked_at=utcnow()))
        db.commit()
        return None
    if t.expires_at < utcnow():
        return None
    t.revoked_at = utcnow()
    return t.user_id, issue_refresh(db, t.user_id, t.family)


def revoke_all(db: Session, user_id: int):
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
               .values(revoked_at=utcnow()))


class RateLimiter:
    """Ограничение частоты событий по ключу.

    PostgreSQL: события хранятся в таблице rate_events и общие для всех процессов приложения (отдельное соединение
    с автокоммитом: запись не откатывается вместе с запросом, завершившимся ошибкой 401/429).
    SQLite (разработка, один процесс): в памяти — отдельное соединение конфликтовало бы с блокировкой записи запроса.
    В БД хранится только хеш ключа (e-mail, IP, чат)."""

    def __init__(self, name: str = "", max_events: int | None = None, window_sec: int | None = None):
        self.name = name
        self._h: dict[str, list[float]] = {}
        self._l = threading.Lock()
        self.max_events = max_events if max_events is not None else config.LOGIN_MAX_FAILS
        self.window = window_sec if window_sec is not None else config.LOGIN_WINDOW_SEC

    # --- БД ---
    def _hk(self, key: str) -> str:
        return hashlib.sha256(f"{self.name}:{key}".encode()).hexdigest()

    @staticmethod
    def _pg():
        from . import db as dbm
        return dbm.get_engine() if dbm.is_pg() else None

    def blocked(self, key: str) -> bool:
        eng = self._pg()
        if eng is not None:
            from sqlalchemy import func
            from .models import RateEvent
            with eng.begin() as c:
                n = c.scalar(select(func.count()).select_from(RateEvent.__table__).where(
                    RateEvent.__table__.c.key_hash == self._hk(key),
                    RateEvent.__table__.c.at > utcnow() - timedelta(seconds=self.window)))
            return n >= self.max_events
        with self._l:
            now = time.time()
            self._h[key] = [t for t in self._h.get(key, []) if now - t < self.window]
            return len(self._h[key]) >= self.max_events

    def fail(self, key: str):
        eng = self._pg()
        if eng is not None:
            from .models import RateEvent
            t = RateEvent.__table__
            with eng.begin() as c:
                c.execute(t.insert().values(key_hash=self._hk(key), at=utcnow()))
                c.execute(t.delete().where(t.c.key_hash == self._hk(key), t.c.at < utcnow() - timedelta(seconds=self.window)))
            return
        with self._l:
            self._h.setdefault(key, []).append(time.time())

    def reset(self, key: str):
        eng = self._pg()
        if eng is not None:
            from .models import RateEvent
            with eng.begin() as c:
                c.execute(RateEvent.__table__.delete().where(RateEvent.__table__.c.key_hash == self._hk(key)))
            return
        with self._l:
            self._h.pop(key, None)

    def clear_memory(self):
        with self._l:
            self._h.clear()


login_limiter = RateLimiter("login")
forgot_limiter = RateLimiter("forgot", 3, 3600)        # запросы сброса пароля: 3 в час на e-mail+IP
resend_limiter = RateLimiter("resend", 1, 60)          # повторная отправка подтверждения: 1 в минуту на пользователя


def reset_limiters():
    """Для тестов: очистить состояние в памяти."""
    for l in (login_limiter, forgot_limiter, resend_limiter):
        l.clear_memory()
