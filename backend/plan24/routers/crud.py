"""Фабрика CRUD-эндпоинтов с изоляцией по организации, аудитом и оптимистичной блокировкой (ТЗ раздел 9)."""

from datetime import date, datetime
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..db import DB
from ..deps import MANAGERS, READERS, get_or_404, require, scoped
from ..models import User, utcnow


def row(obj, hide=("tenant_id",)) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name not in hide}


def make_crud(router: APIRouter, path: str, Model, Create, Patch, entity: str, *,
              fk: dict | None = None, read_roles=READERS, write_roles=MANAGERS,
              before_create: Optional[Callable] = None, before_update: Optional[Callable] = None,
              to_out: Callable = row, order_by=None, before_delete: Optional[Callable] = None,
              after_create: Optional[Callable] = None, delete_roles=None,
              row_scope: Optional[Callable] = None, row_check: Optional[Callable] = None):
    """row_scope(user, query) → query — какие записи видит роль (например, бригадир — только свой участок);
    row_check(user, obj) — проверка доступа к одной записи (поднимает 404/403). delete_roles — если удаление уже, чем правка."""
    fk = fk or {}
    delete_roles = delete_roles or write_roles

    def one(db, user, oid):
        obj = get_or_404(db, Model, oid, user)
        if row_check:
            row_check(user, obj)
        return obj

    def check_fk(db, user, data):
        for field, model in fk.items():
            if data.get(field) is not None:
                get_or_404(db, model, data[field], user)   # связь только внутри своей организации

    @router.get(path)
    def list_(limit: int = 200, cursor: int = 0, user: User = Depends(require(*read_roles)), db: Session = DB):
        q = scoped(Model, user).where(Model.id > cursor)
        if row_scope:
            q = row_scope(user, q)
        q = q.order_by(order_by or Model.id).limit(min(max(limit, 1), 1000) + 1)
        rows = list(db.scalars(q))
        lim = min(max(limit, 1), 1000)
        more = len(rows) > lim
        rows = rows[:lim]
        return {"items": [to_out(r) for r in rows], "next_cursor": rows[-1].id if more else None}

    @router.get(path + "/{oid}")
    def get_(oid: int, user: User = Depends(require(*read_roles)), db: Session = DB):
        return to_out(one(db, user, oid))

    @router.post(path, status_code=201)
    def create_(body: Create, user: User = Depends(require(*write_roles)), db: Session = DB):
        data = body.model_dump()
        check_fk(db, user, data)
        if before_create:
            data = before_create(db, user, data) or data
        obj = Model(tenant_id=user.tenant_id, **data)
        db.add(obj)
        try:
            db.flush()
        except IntegrityError:
            raise HTTPException(409, "Такая запись уже существует")
        if after_create:
            after_create(db, user, obj)
        audit.log(db, user, entity, obj.id, "create", data)
        return to_out(obj)

    @router.patch(path + "/{oid}")
    def patch_(oid: int, body: Patch, user: User = Depends(require(*write_roles)), db: Session = DB):
        obj = one(db, user, oid)
        data = body.model_dump(exclude_unset=True)
        ver = data.pop("version", None)
        if ver is not None and ver != obj.version:
            raise HTTPException(409, "Запись изменена другим пользователем. Обновите страницу")
        check_fk(db, user, data)
        if before_update:
            data = before_update(db, user, obj, data) or data
        changes = {}
        for k, v in data.items():
            old = getattr(obj, k)
            if old != v:
                changes[k] = [old, v]
                setattr(obj, k, v)
        if changes:
            obj.version = (obj.version or 1) + 1
            try:
                db.flush()
            except IntegrityError:
                raise HTTPException(409, "Такая запись уже существует")
            audit.log(db, user, entity, obj.id, "update", changes)
        return to_out(obj)

    @router.delete(path + "/{oid}", status_code=204)
    def delete_(oid: int, user: User = Depends(require(*delete_roles)), db: Session = DB):
        obj = one(db, user, oid)
        if before_delete:
            before_delete(db, user, obj)
        if hasattr(obj, "deleted_at"):
            obj.deleted_at = utcnow()
        else:
            db.delete(obj)
        try:
            db.flush()
        except IntegrityError:
            raise HTTPException(409, "Запись используется в других данных — удалить нельзя")
        audit.log(db, user, entity, oid, "delete")
