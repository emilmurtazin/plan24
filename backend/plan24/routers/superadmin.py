"""Панель суперадмина платформы: организации, статистика, блокировка, пользователи, вход под организацией для поддержки.

Доступ — только по токену суперадмина (typ=platform), выдаваемому при входе по e-mail и паролю из настроек сервера
(PLAN24_SUPERADMIN_EMAIL и PLAN24_SUPERADMIN_PASSWORD_HASH). Без этих настроек панели нет: все адреса отвечают 404.
В базе действует отдельный режим платформы (RLS, миграция 0013): чтение сводных данных и несколько точечных записей.
Каждое действие над организацией записывается в её журнал аудита (user_id пуст, changes.by = "superadmin")."""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import audit, config, email_tokens, mailer, security
from .. import db as dbm
from ..db import DB
from ..models import (AuditLog, OrderLine, Plan, RefreshToken, TelegramLink, Tenant, User, Worker, utcnow)

router = APIRouter(prefix="/superadmin", tags=["superadmin"])
log = logging.getLogger("plan24.superadmin")


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class BlockIn(BaseModel):
    reason: str = Field("", max_length=300)


class ImpersonateIn(BaseModel):
    user_id: Optional[int] = None          # кем войти; по умолчанию — владелец организации


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/login")
def login(body: LoginIn, request: Request):
    creds = config.superadmin_credentials()
    if not creds:
        raise HTTPException(404, "Not Found")
    ip = _ip(request)
    if security.platform_limiter.blocked(ip) or security.platform_global_limiter.blocked("all"):
        raise HTTPException(429, "Слишком много попыток. Повторите позже")
    # пароль проверяется всегда (одинаковое время при неверном e-mail), e-mail сравнивается за постоянное время
    ok_pw = security.verify_password(creds[1], body.password)
    ok_email = hmac.compare_digest(body.email.strip().lower().encode(), creds[0].encode())
    if not (ok_pw and ok_email):
        security.platform_limiter.fail(ip)
        security.platform_global_limiter.fail("all")
        log.warning("superadmin login failed from %s", ip)
        raise HTTPException(401, "Неверный e-mail или пароль")
    security.platform_limiter.reset(ip)
    log.warning("superadmin login ok from %s", ip)
    return {"access_token": security.make_platform(creds[0]), "token_type": "bearer", "email": creds[0],
            "expires_in": config.PLATFORM_TTL_MIN * 60}


def platform(request: Request, db: Session = DB) -> str:
    """Проверка токена суперадмина и включение режима платформы в БД. Токен организации сюда не подходит."""
    if not config.superadmin_credentials():
        raise HTTPException(404, "Not Found")
    auth = request.headers.get("authorization", "")
    email = security.decode_platform(auth[7:]) if auth.lower().startswith("bearer ") else None
    if email is None:
        raise HTTPException(401, "Требуется вход суперадмина")
    dbm.platform_scope(db, True)
    return email


# ------------------------------------------------------------------ чтение
def _count(db: Session, col, model, *extra) -> dict:
    q = select(col, func.count()).select_from(model).group_by(col)
    for c in extra:
        q = q.where(c)
    return dict(db.execute(q).all())


def _alive(model) -> list:
    """Не удалённые записи (у моделей с мягким удалением)."""
    return [model.deleted_at.is_(None)] if hasattr(model, "deleted_at") else []


def _stats(db: Session) -> dict:
    """Сводные числа по каждой организации одним проходом (по 6 запросам на все организации)."""
    return {
        "users": _count(db, User.tenant_id, User, User.is_active.is_(True)),
        "workers": _count(db, Worker.tenant_id, Worker, *_alive(Worker)),
        "orders": _count(db, OrderLine.tenant_id, OrderLine, *_alive(OrderLine)),
        "telegram": _count(db, TelegramLink.tenant_id, TelegramLink),
        "last_plan": dict(db.execute(select(Plan.tenant_id, func.max(Plan.created_at)).group_by(Plan.tenant_id)).all()),
        "last_activity": dict(db.execute(select(AuditLog.tenant_id, func.max(AuditLog.created_at)).group_by(AuditLog.tenant_id)).all()),
    }


def _iso(v):
    return v.isoformat() if v else None


def _owners(db: Session) -> dict:
    out: dict = {}
    for u in db.scalars(select(User).where(User.role == "owner").order_by(User.id)):
        out.setdefault(u.tenant_id, u)                      # первый (старший) владелец
    return out


def _row(t: Tenant, st: dict, owners: dict) -> dict:
    o = owners.get(t.id)
    return {"id": t.id, "name": t.name, "created_at": _iso(t.created_at), "timezone": t.timezone,
            "blocked": t.blocked_at is not None, "blocked_at": _iso(t.blocked_at), "blocked_reason": t.blocked_reason,
            "owner": {"id": o.id, "name": o.name, "email": o.email, "verified": o.email_verified} if o else None,
            "users": st["users"].get(t.id, 0), "workers": st["workers"].get(t.id, 0), "orders": st["orders"].get(t.id, 0),
            "telegram": st["telegram"].get(t.id, 0), "last_plan_at": _iso(st["last_plan"].get(t.id)),
            "last_activity_at": _iso(st["last_activity"].get(t.id))}


@router.get("/me")
def me(email: str = Depends(platform)):
    return {"email": email}


@router.get("/overview")
def overview(_: str = Depends(platform), db: Session = DB):
    tenants = list(db.scalars(select(Tenant)))
    st = _stats(db)
    now = utcnow()
    active7 = sum(1 for t in tenants if st["last_activity"].get(t.id) and (now - st["last_activity"][t.id]).days < 7)
    new30 = sum(1 for t in tenants if t.created_at and (now - t.created_at).days < 30)
    return {"tenants": len(tenants), "blocked": sum(1 for t in tenants if t.blocked_at), "active_7d": active7, "new_30d": new30,
            "users": sum(st["users"].values()), "workers": sum(st["workers"].values()), "orders": sum(st["orders"].values()),
            "telegram_links": sum(st["telegram"].values())}


@router.get("/tenants")
def tenants(q: str = "", _: str = Depends(platform), db: Session = DB):
    rows = list(db.scalars(select(Tenant).order_by(Tenant.id.desc())))
    st, owners = _stats(db), _owners(db)
    out = [_row(t, st, owners) for t in rows]
    needle = q.strip().lower()
    if needle:
        out = [r for r in out if needle in r["name"].lower() or (r["owner"] and needle in r["owner"]["email"].lower())
               or needle == str(r["id"])]
    return {"items": out}


def _tenant(db: Session, tid: int) -> Tenant:
    t = db.get(Tenant, tid)
    if not t:
        raise HTTPException(404, "Организация не найдена")
    return t


@router.get("/tenants/{tid}")
def tenant_detail(tid: int, _: str = Depends(platform), db: Session = DB):
    t = _tenant(db, tid)
    st, owners = _stats(db), _owners(db)
    users = list(db.scalars(select(User).where(User.tenant_id == tid).order_by(User.id)))
    events = list(db.scalars(select(AuditLog).where(AuditLog.entity == "tenant", AuditLog.tenant_id == tid,
                                                    AuditLog.action.like("platform_%")).order_by(AuditLog.id.desc()).limit(20)))
    return {**_row(t, st, owners),
            "users_list": [{"id": u.id, "name": u.name, "email": u.email, "role": u.role, "active": u.is_active,
                            "verified": u.email_verified, "created_at": _iso(u.created_at)} for u in users],
            "history": [{"at": _iso(e.created_at), "action": e.action, "reason": (e.changes or {}).get("reason"),
                         "target": (e.changes or {}).get("target")} for e in events]}


# ------------------------------------------------------------------ действия
def _note(db: Session, tid: int, entity_id: int | None, action: str, **changes):
    """Запись в журнал организации о действии суперадмина (RLS допускает только добавление в режиме платформы)."""
    db.add(AuditLog(tenant_id=tid, user_id=None, entity="tenant", entity_id=entity_id, action=action,
                    changes={"by": "superadmin", **{k: v for k, v in changes.items() if v is not None}}))


def _revoke_sessions(db: Session, user_ids: list[int]):
    if user_ids:
        db.execute(update(RefreshToken).where(RefreshToken.user_id.in_(user_ids), RefreshToken.revoked_at.is_(None))
                   .values(revoked_at=utcnow()))


@router.post("/tenants/{tid}/block")
def block(tid: int, body: BlockIn, _: str = Depends(platform), db: Session = DB):
    t = _tenant(db, tid)
    t.blocked_at, t.blocked_reason = utcnow(), body.reason.strip() or None
    _revoke_sessions(db, list(db.scalars(select(User.id).where(User.tenant_id == tid))))     # обновление токенов тоже закрыто
    _note(db, tid, tid, "platform_block", reason=t.blocked_reason)
    return {"ok": True}


@router.post("/tenants/{tid}/unblock")
def unblock(tid: int, _: str = Depends(platform), db: Session = DB):
    t = _tenant(db, tid)
    t.blocked_at, t.blocked_reason = None, None
    _note(db, tid, tid, "platform_unblock")
    return {"ok": True}


def _user(db: Session, uid: int) -> User:
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    return u


@router.post("/users/{uid}/deactivate")
def deactivate(uid: int, _: str = Depends(platform), db: Session = DB):
    u = _user(db, uid)
    u.is_active = False
    _revoke_sessions(db, [u.id])
    _note(db, u.tenant_id, u.id, "platform_user_off", target=u.email)
    return {"ok": True}


@router.post("/users/{uid}/activate")
def activate(uid: int, _: str = Depends(platform), db: Session = DB):
    u = _user(db, uid)
    u.is_active = True
    _note(db, u.tenant_id, u.id, "platform_user_on", target=u.email)
    return {"ok": True}


@router.post("/users/{uid}/reset-password")
def reset_password(uid: int, bg: BackgroundTasks, _: str = Depends(platform), db: Session = DB):
    """Отправляет пользователю письмо со ссылкой для нового пароля. Сам пароль суперадмин не видит и не задаёт."""
    u = _user(db, uid)
    if not u.is_active:
        raise HTTPException(409, "Пользователь отключён: сначала включите его")
    subj, text, html_ = mailer.reset_password(u.name, email_tokens.issue(db, u, "reset"))
    bg.add_task(mailer.send, u.email, subj, text, html_)
    _note(db, u.tenant_id, u.id, "platform_reset_link", target=u.email)
    return {"ok": True, "sent_to": u.email}


@router.post("/tenants/{tid}/impersonate")
def impersonate(tid: int, body: ImpersonateIn, _: str = Depends(platform), db: Session = DB):
    """Короткий токен (без обновления) для входа в приложение под пользователем организации — для поддержки.
    В режиме поддержки нельзя менять пароль и e-mail; все действия помечаются в журнале организации (via_support)."""
    t = _tenant(db, tid)
    if body.user_id:
        u = _user(db, body.user_id)
        if u.tenant_id != tid:
            raise HTTPException(404, "Пользователь не найден")
    else:
        u = _owners(db).get(tid)
    if not u or not u.is_active:
        raise HTTPException(409, "В организации нет активного пользователя для входа")
    _note(db, tid, u.id, "platform_login", target=u.email)
    return {"access_token": security.make_access(u.id, tid, support=True), "expires_in": config.IMPERSONATE_TTL_MIN * 60,
            "user": {"id": u.id, "name": u.name, "role": u.role, "email": u.email}, "tenant": {"id": t.id, "name": t.name}}
