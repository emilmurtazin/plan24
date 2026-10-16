"""Пользователи организации — только администратор (в коде роль «owner»; ТЗ раздел 9, «Права доступа»)."""
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, email_tokens, mailer, names, security
from .. import db as dbm
from ..db import DB
from ..deps import get_or_404, require, scoped, tenant_of
from ..models import ROLES, User, WorkCenter, Worker

ROLE_RU = {"owner": "администратор", "manager": "руководитель проекта", "technologist": "технолог", "foreman": "бригадир", "worker": "сборщик",
           "viewer": "наблюдатель", "director": "директор"}
IMPORT_LIMIT = 500
router = APIRouter(prefix="/users", tags=["users"])


class UserIn(BaseModel):
    email: EmailStr
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    name: Optional[str] = Field(None, max_length=200)          # для старых клиентов: «Имя Фамилия» одной строкой
    role: str
    password: Optional[str] = Field(None, min_length=8, max_length=128)
    send_invite: bool = False                  # отправить письмо-приглашение: человек сам задаст пароль
    work_center_id: Optional[int] = None
    worker_id: Optional[int] = None


class UserPatch(BaseModel):
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=8, max_length=128)
    work_center_id: Optional[int] = None
    worker_id: Optional[int] = None
    clear_links: bool = False


def out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "first_name": u.first_name, "last_name": u.last_name,
            "role": u.role, "is_active": u.is_active,
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
    first, last, full = names.resolve(body.first_name, body.last_name, body.name)
    u = User(tenant_id=owner.tenant_id, email=email, name=full, first_name=first, last_name=last, role=body.role,
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
    if body.first_name is not None or body.last_name is not None or body.name:
        if body.first_name is None and body.last_name is None:
            first, last = names.split_name(body.name)
        else:
            first, last = (body.first_name if body.first_name is not None else u.first_name), (body.last_name if body.last_name is not None else u.last_name)
        first, last = first.strip(), last.strip()
        if not first or not last:
            raise HTTPException(422, "Имя и фамилия не могут быть пустыми")
        u.first_name, u.last_name, u.name = first, last, names.full_name(first, last)
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


# ------------------------------------------------------------------ загрузка из файла
class ImportRow(BaseModel):
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    role: str = ""
    work_center: str = ""             # название участка (для бригадира)
    worker: str = ""                  # имя работника (для сборщика)
    password: str = ""


class ImportIn(BaseModel):
    rows: list[ImportRow] = Field(max_length=IMPORT_LIMIT)
    send_invite: bool = False         # отправить каждому приглашение по e-mail (тогда пароль в файле не нужен)
    dry_run: bool = False             # только проверить, ничего не создавая


def _role_code(text: str) -> Optional[str]:
    t = (text or "").strip().lower().replace("ё", "е")
    if t in ROLES:
        return t
    for code, ru in ROLE_RU.items():
        if t == ru.replace("ё", "е"):
            return code
    return {"владелец": "owner", "руководитель": "manager"}.get(t)       # прежние названия ролей


@router.post("/import")
def import_users(body: ImportIn, bg: BackgroundTasks, owner: User = Depends(require("owner")), db: Session = DB):
    """Пакетное создание пользователей из файла (строки уже разобраны клиентом). Всё или ничего: если в какой-то
    строке ошибка, не создаётся никто — файл исправляют и загружают целиком."""
    if not body.rows:
        raise HTTPException(422, "В файле нет строк с пользователями")
    centers = {c.name.strip().lower(): c.id for c in db.scalars(scoped(WorkCenter, owner))}
    workers = {w.name.strip().lower(): w.id for w in db.scalars(scoped(Worker, owner))}
    errors, plan, seen = [], [], set()
    for i, r in enumerate(body.rows, start=1):
        def bad(msg, i=i):
            errors.append({"row": i, "message": msg})
        email = r.email.strip().lower()
        try:
            from pydantic import TypeAdapter
            TypeAdapter(EmailStr).validate_python(email)
        except Exception:
            bad(f"Некорректный e-mail «{r.email}»")
            continue
        if email in seen:
            bad(f"E-mail {email} повторяется в файле")
            continue
        seen.add(email)
        if dbm.email_taken(db, email):
            bad(f"E-mail {email} уже зарегистрирован")
            continue
        try:
            first, last, full = names.resolve(r.first_name, r.last_name, None)
        except HTTPException as e:
            bad(e.detail)
            continue
        role = _role_code(r.role)
        if role is None:
            bad(f"Неизвестная роль «{r.role}». Допустимо: " + ", ".join(v.capitalize() for v in ROLE_RU.values()))
            continue
        wc = wk = None
        if r.work_center.strip():
            wc = centers.get(r.work_center.strip().lower())
            if wc is None:
                bad(f"Участок «{r.work_center}» не найден")
                continue
        if r.worker.strip():
            wk = workers.get(r.worker.strip().lower())
            if wk is None:
                bad(f"Работник «{r.worker}» не найден")
                continue
        if role == "foreman" and wc is None:
            bad("Бригадиру нужен участок")
            continue
        if role == "worker" and wk is None:
            bad("Сборщику нужен работник из списка работников")
            continue
        if role != "foreman":
            wc = None
        if role != "worker":
            wk = None
        pwd = r.password.strip()
        if not body.send_invite and len(pwd) < 8:
            bad("Нужен пароль не короче 8 символов или включите «Отправить приглашения»")
            continue
        plan.append((email, first, last, full, role, wc, wk, pwd))
    if errors:
        return {"created": 0, "errors": errors, "total": len(body.rows), "dry_run": body.dry_run}
    if body.dry_run:
        return {"created": 0, "errors": [], "total": len(plan), "dry_run": True, "would_create": len(plan)}
    for email, first, last, full, role, wc, wk, pwd in plan:
        u = User(tenant_id=owner.tenant_id, email=email, name=full, first_name=first, last_name=last, role=role,
                 password_hash=security.hash_password(secrets.token_urlsafe(24) if body.send_invite else pwd),
                 work_center_id=wc, worker_id=wk, email_verified=not body.send_invite)
        db.add(u)
        db.flush()
        if body.send_invite:
            _queue_invite(db, bg, owner, u)
    audit.log(db, owner, "user", None, "import", {"count": len(plan), "invite": body.send_invite})
    return {"created": len(plan), "errors": [], "total": len(plan), "dry_run": False}
