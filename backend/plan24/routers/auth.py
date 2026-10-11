"""Регистрация организации, вход, обновление токена, пароль (FR-01, FR-02, ТЗ раздел 9, 12)."""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, config, email_tokens, mailer, security
from .. import db as dbm
from ..db import DB
from ..deps import current_user, tenant_of
from ..models import FactReason, PlannerSettings, Tenant, TenantModule, User, WorkCenter, utcnow

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("plan24.mail")
_DUMMY = security.hash_password("dummy-password-for-timing")

PERMISSIONS = {   # что может роль — для интерфейса (серверная проверка всё равно выполняется в каждом эндпоинте)
    "owner": ["refs.write", "catalog.write", "plan.run", "settings.write", "fact.any", "users", "audit", "reports"],
    "manager": ["refs.write", "catalog.write", "plan.run", "settings.write", "fact.any", "audit", "reports"],
    "technologist": ["catalog.write", "reports"],
    "foreman": ["plan.run", "fact.center", "reports"],
    "worker": ["fact.own"],
    "viewer": ["reports"],
}
DEFAULT_REASONS = ["Нет материала", "Поломка оборудования", "Отсутствие исполнителя", "Переделка", "Ожидание предыдущей операции"]


class RegisterIn(BaseModel):
    org_name: str = Field(min_length=2, max_length=200)
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    accept_terms: bool                                  # согласие на обработку ПД (152-ФЗ, раздел 12)
    timezone: str = "Europe/Moscow"


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


def user_out(u: User, t: Tenant) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "email_verified": u.email_verified,
            "work_center_id": u.work_center_id, "worker_id": u.worker_id,
            "permissions": PERMISSIONS[u.role],
            "tenant": {"id": t.id, "name": t.name, "timezone": t.timezone, "plan": t.plan,
                       "trial_ends_at": t.trial_ends_at.isoformat() if t.trial_ends_at else None}}


def _tokens(db: Session, u: User) -> dict:
    return {"access_token": security.make_access(u.id, u.tenant_id), "refresh_token": security.issue_refresh(db, u.id),
            "token_type": "bearer", "expires_in": config.ACCESS_TTL_MIN * 60}


@router.post("/register", status_code=201)
def register(body: RegisterIn, bg: BackgroundTasks, db: Session = DB):
    if not body.accept_terms:
        raise HTTPException(422, "Нужно согласие на обработку персональных данных")
    email = body.email.lower()
    if dbm.email_taken(db, email):
        raise HTTPException(409, "Этот e-mail уже зарегистрирован")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(body.timezone)
    except Exception:
        raise HTTPException(422, "Неизвестный часовой пояс")
    tid = dbm.new_tenant_id(db)
    if tid is not None:
        dbm.set_tenant(db, tid)                  # PostgreSQL: политика допускает создание только организации с id = app.tenant_id
    t = Tenant(name=body.org_name.strip(), timezone=body.timezone, trial_ends_at=utcnow() + timedelta(days=config.TRIAL_DAYS))
    if tid is not None:
        t.id = tid
    db.add(t); db.flush()
    dbm.set_tenant(db, t.id)
    db.add(TenantModule(tenant_id=t.id, module_id="production", enabled=True))
    db.add(PlannerSettings(tenant_id=t.id, strictness_by_level={}))
    for r in DEFAULT_REASONS:
        db.add(FactReason(tenant_id=t.id, name=r))
    u = User(tenant_id=t.id, email=email, password_hash=security.hash_password(body.password), name=body.name.strip(),
             role="owner", consent_at=utcnow())
    db.add(u)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(409, "Этот e-mail уже зарегистрирован")             # одновременная регистрация того же адреса
    audit.log(db, u, "tenant", t.id, "register")
    subj, text, html_ = mailer.verify_email(u.name, t.name, email_tokens.issue(db, u, "verify"))
    bg.add_task(mailer.send, email, subj, text, html_)          # после ответа и после коммита
    return {**_tokens(db, u), "user": user_out(u, t)}


@router.post("/verify")
def verify(body: TokenIn, db: Session = DB):
    u = email_tokens.consume(db, body.token, ("verify",))
    if not u:
        raise HTTPException(400, "Ссылка недействительна или устарела. Запросите новое письмо в приложении")
    u.email_verified = True
    audit.log(db, u, "user", u.id, "email_verified")
    return {"ok": True}


@router.post("/resend-verification")
def resend_verification(bg: BackgroundTasks, u: User = Depends(current_user), db: Session = DB):
    if u.email_verified:
        return {"already_verified": True}
    if security.resend_limiter.blocked(f"v{u.id}"):
        raise HTTPException(429, "Письмо уже отправлено. Повторить можно через минуту")
    security.resend_limiter.fail(f"v{u.id}")
    subj, text, html_ = mailer.verify_email(u.name, tenant_of(db, u).name, email_tokens.issue(db, u, "verify"))
    bg.add_task(mailer.send, u.email, subj, text, html_)
    return {"sent": True}


@router.post("/forgot-password")
def forgot_password(body: ForgotIn, request: Request, bg: BackgroundTasks, db: Session = DB):
    """Всегда один и тот же ответ: по нему нельзя узнать, зарегистрирован ли адрес."""
    email = body.email.lower()
    key = f"{email}|{request.client.host if request.client else ''}"
    answer = {"ok": True, "message": "Если такой адрес зарегистрирован, мы отправили письмо со ссылкой для смены пароля"}
    if security.forgot_limiter.blocked(key):
        raise HTTPException(429, "Слишком много запросов. Повторите позже")
    security.forgot_limiter.fail(key)
    dbm.auth_scope(db, True)
    u = db.scalar(select(User).where(User.email == email))
    if u and u.is_active:
        dbm.set_tenant(db, u.tenant_id)
        subj, text, html_ = mailer.reset_password(u.name, email_tokens.issue(db, u, "reset"))
        bg.add_task(mailer.send, u.email, subj, text, html_)
        audit.log(db, u, "user", u.id, "password_reset_requested")
    else:
        dbm.auth_scope(db, False)
    return answer


@router.post("/reset-password")
def reset_password(body: ResetIn, db: Session = DB):
    """Сброс по ссылке из письма или принятие приглашения (задать первый пароль). Старые сессии закрываются."""
    u = email_tokens.consume(db, body.token, ("reset", "invite"))
    if not u:
        raise HTTPException(400, "Ссылка недействительна или устарела. Запросите новое письмо")
    u.password_hash = security.hash_password(body.new_password)
    u.email_verified = True                      # доступ к ящику подтверждён самим фактом перехода по ссылке
    security.revoke_all(db, u.id)
    audit.log(db, u, "user", u.id, "password_reset")
    return {"ok": True}


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = DB):
    key = f"{body.email.lower()}|{request.client.host if request.client else ''}"
    if security.login_limiter.blocked(key):
        raise HTTPException(429, "Слишком много попыток. Повторите позже")
    dbm.auth_scope(db, True)                          # поиск учётной записи до входа: только чтение
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    ok = security.verify_password(u.password_hash if u else _DUMMY, body.password)   # одинаковое время для несуществующих
    if not (u and ok and u.is_active):
        dbm.auth_scope(db, False)
        security.login_limiter.fail(key)
        raise HTTPException(401, "Неверный e-mail или пароль")
    dbm.set_tenant(db, u.tenant_id)
    security.login_limiter.reset(key)
    return {**_tokens(db, u), "user": user_out(u, tenant_of(db, u))}


@router.post("/refresh")
def refresh(body: RefreshIn, db: Session = DB):
    r = security.rotate_refresh(db, body.refresh_token)
    if not r:
        raise HTTPException(401, "Сессия истекла")
    uid, new_refresh = r
    u = db.get(User, uid)
    if not u or not u.is_active:
        raise HTTPException(401, "Сессия истекла")
    return {"access_token": security.make_access(uid, u.tenant_id), "refresh_token": new_refresh, "token_type": "bearer",
            "expires_in": config.ACCESS_TTL_MIN * 60}


@router.post("/logout")
def logout(u: User = Depends(current_user), db: Session = DB):
    security.revoke_all(db, u.id)
    return {"ok": True}


@router.get("/me")
def me(u: User = Depends(current_user), db: Session = DB):
    return user_out(u, tenant_of(db, u))


@router.post("/change-password")
def change_password(body: PasswordIn, u: User = Depends(current_user), db: Session = DB):
    if not security.verify_password(u.password_hash, body.current_password):
        raise HTTPException(403, "Текущий пароль неверен")
    u.password_hash = security.hash_password(body.new_password)
    security.revoke_all(db, u.id)
    audit.log(db, u, "user", u.id, "password_change")
    return {"ok": True}
