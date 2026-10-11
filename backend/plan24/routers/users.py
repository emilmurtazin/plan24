"""Пользователи организации — только владелец (ТЗ раздел 9, «Права доступа»)."""
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, email_tokens, mailer, security
from .. import db as dbm
from ..db import DB
from ..deps import get_or_404, require, scoped, tenant_of
from ..models import ROLES, User, WorkCenter, Worker

ROLE_RU = {"owner": "владелец", "manager": "руководитель", "technologist": "технолог", "foreman": "бригадир", "worker": "сборщик", "viewer": "наблюдатель"}
router = APIRouter(prefix="/users", tags=["users"])


class UserIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=200)
    role: str
    password: Optional[str] = Field(None, min_length=8, max_length=128)
    send_invite: bool = False                  # отправить письмо-приглашение: человек сам задаст пароль
    work_center_id: Optional[int] = None
    worker_id: Optional[int] = None


class UserPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=8, max_length=128)
    work_center_id: Optional[int] = None
    worker_id: Optional[int] = None
    clear_links: bool = False


def out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "is_active": u.is_active,
            "work_center_id": u.work_center_id, "worker_id": u.worker_id, "email_verified": u.email_verified}


def _validate(db, owner: User, role: str, wc: Optional[int], wk: Optional[int]):
    if role not in ROLES:
        raise HTTPException(422, f"Роль должна быть одной из: {', '.join(ROLES)}")
    if wc is not None:
        get_or_404(db, WorkCenter, wc, owner)
    if wk is not None:
        get_or_404(db, Worker, wk, owner)
    if role == "foreman" and wc is None:
        raise HTTPException(422, "Бригадиру нужно назначить участок")
    if role == "worker" and wk is None:
        raise HTTPException(422, "Сборщику нужно сопоставить исполнителя")


def _owners_left(db, owner: User, excluding: int) -> int:
    return db.scalar(select(func.count()).select_from(User).where(
        User.tenant_id == owner.tenant_id, User.role == "owner", User.is_active.is_(True), User.id != excluding))


@router.get("")
def list_users(owner: User = Depends(require("owner")), db: Session = DB):
    return [out(u) for u in db.scalars(scoped(User, owner).order_by(User.id))]


@router.post("", status_code=201)
def create_user(body: UserIn, bg: BackgroundTasks, owner: User = Depends(require("owner")), db: Session = DB):
    email = body.email.lower()
    if not body.send_invite and not body.password:
        raise HTTPException(422, "Укажите пароль или отправьте приглашение по e-mail")
    if dbm.email_taken(db, email):
        raise HTTPException(409, "Этот e-mail уже зарегистрирован")
    _validate(db, owner, body.role, body.work_center_id, body.worker_id)
    # при приглашении пароль неизвестен никому: случайный, войти можно только после установки своего по ссылке
    pwd = body.password if not body.send_invite else secrets.token_urlsafe(24)
    u = User(tenant_id=owner.tenant_id, email=email, name=body.name.strip(), role=body.role,
             password_hash=security.hash_password(pwd), work_center_id=body.work_center_id,
             worker_id=body.worker_id, email_verified=not body.send_invite)
    db.add(u)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(409, "Этот e-mail уже зарегистрирован")
    audit.log(db, owner, "user", u.id, "create", {"email": email, "role": body.role, "invite": body.send_invite})
    if body.send_invite:
        _queue_invite(db, bg, owner, u)
    return out(u)


def _queue_invite(db, bg, owner: User, u: User):
    subj, text, html_ = mailer.invite(u.name, tenant_of(db, owner).name, owner.name, ROLE_RU[u.role],
                                      email_tokens.issue(db, u, "invite"))
    bg.add_task(mailer.send, u.email, subj, text, html_)


@router.post("/{uid}/invite")
def resend_invite(uid: int, bg: BackgroundTasks, owner: User = Depends(require("owner")), db: Session = DB):
    """Повторное приглашение (прежняя ссылка перестаёт работать)."""
    u = get_or_404(db, User, uid, owner)
    if not u.is_active:
        raise HTTPException(409, "Пользователь отключён")
    _queue_invite(db, bg, owner, u)
    audit.log(db, owner, "user", u.id, "invite_resent")
    return {"sent": True}


@router.patch("/{uid}")
def patch_user(uid: int, body: UserPatch, owner: User = Depends(require("owner")), db: Session = DB):
    u = get_or_404(db, User, uid, owner)
    new_role = body.role or u.role
    wc = body.work_center_id if (body.work_center_id is not None or body.clear_links) else u.work_center_id
    wk = body.worker_id if (body.worker_id is not None or body.clear_links) else u.worker_id
    _validate(db, owner, new_role, wc, wk)
    losing_owner = u.role == "owner" and (new_role != "owner" or body.is_active is False)
    if losing_owner and _owners_left(db, owner, u.id) == 0:
        raise HTTPException(409, "Нельзя оставить организацию без владельца")
    changes = {}
    if new_role != u.role:
        changes["role"] = [u.role, new_role]
    if body.is_active is not None and body.is_active != u.is_active:
        changes["is_active"] = [u.is_active, body.is_active]
    u.role, u.work_center_id, u.worker_id = new_role, wc, wk
    if body.name:
        u.name = body.name.strip()
    if body.is_active is not None:
        u.is_active = body.is_active
        if not body.is_active:
            security.revoke_all(db, u.id)
    if body.password:
        u.password_hash = security.hash_password(body.password)
        security.revoke_all(db, u.id)
        changes["password"] = "reset"
    audit.log(db, owner, "user", u.id, "update", changes)
    return out(u)
