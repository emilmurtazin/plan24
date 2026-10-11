"""Справочники и данные модуля «Производство»: участки, исполнители, отпуска, праздники, причины,
проекты, изделия + маршруты (версии), позиции заказов, допуски, настройки планировщика, журнал."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import audit
from ..db import DB
from ..deps import ALL, CATALOG, MANAGERS, READERS, STAFF, get_or_404, require, scoped
from ..models import (MAX_QUAL_LEVELS, AuditLog, Fact, FactReason, Holiday, Operation, OrderLine, PlannerSettings, Product, Project,
                      Routing, User, WorkCenter, Worker, WorkerAbsence, WorkerOrderLine)
from .crud import make_crud, row

router = APIRouter(tags=["refs"])
Pos = Field(gt=0)


# ------------------------------------------------------------------ простые справочники
class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class NamePatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=160)
    version: Optional[int] = None


make_crud(router, "/work-centers", WorkCenter, NameIn, NamePatch, "work_center")
make_crud(router, "/fact-reasons", FactReason, NameIn, NamePatch, "fact_reason", read_roles=ALL)   # причины нужны и сборщику при вводе факта


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    customer: str = ""


class ProjectPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=160)
    customer: Optional[str] = None
    version: Optional[int] = None


make_crud(router, "/projects", Project, ProjectIn, ProjectPatch, "project")


class WorkerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    work_center_id: Optional[int] = None
    perf: float = Field(1.0, gt=0, le=10)
    skill: float = Field(1, gt=0, le=10)
    status: str = Field("available", pattern="^(available|unavailable)$")


class WorkerPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    work_center_id: Optional[int] = None
    perf: Optional[float] = Field(None, gt=0, le=10)
    skill: Optional[float] = Field(None, gt=0, le=10)
    status: Optional[str] = Field(None, pattern="^(available|unavailable)$")
    version: Optional[int] = None


def _worker_level_create(db, user, data):
    check_level(db, user, data.get("skill"), "Квалификация работника")


def _worker_level_update(db, user, obj, data):
    check_level(db, user, data.get("skill"), "Квалификация работника")


make_crud(router, "/workers", Worker, WorkerIn, WorkerPatch, "worker", fk={"work_center_id": WorkCenter},
          read_roles=READERS, before_create=_worker_level_create, before_update=_worker_level_update)


class AbsenceIn(BaseModel):
    worker_id: int
    date_from: date
    date_to: date
    reason: str = ""

    @model_validator(mode="after")
    def _order(self):
        if self.date_to < self.date_from:
            raise ValueError("Дата окончания раньше даты начала")
        return self


class AbsencePatch(BaseModel):
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    reason: Optional[str] = None
    version: Optional[int] = None


make_crud(router, "/absences", WorkerAbsence, AbsenceIn, AbsencePatch, "absence", fk={"worker_id": Worker})


class HolidayIn(BaseModel):
    holiday_date: date
    name: str = ""


class HolidayPatch(BaseModel):
    name: Optional[str] = None
    version: Optional[int] = None


make_crud(router, "/holidays", Holiday, HolidayIn, HolidayPatch, "holiday", order_by=Holiday.holiday_date)


# ------------------------------------------------------------------ изделия, маршруты, операции
class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    unit: str = Field("шт", max_length=24)


class ProductPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=160)
    unit: Optional[str] = Field(None, max_length=24)
    version: Optional[int] = None


def _after_product(db, user, obj):
    db.add(Routing(tenant_id=user.tenant_id, product_id=obj.id, version_no=1, is_current=True))   # маршрут v1


make_crud(router, "/products", Product, ProductIn, ProductPatch, "product", after_create=_after_product, write_roles=CATALOG)


def routing_out(r: Routing, used: bool) -> dict:
    return {"id": r.id, "product_id": r.product_id, "version_no": r.version_no, "name": r.name, "is_current": r.is_current,
            "used": used, "operations": [row(o) for o in r.operations]}


def _routing_used(db, user, routing_id: int) -> bool:
    return bool(db.scalar(select(func.count()).select_from(OrderLine).where(
        OrderLine.tenant_id == user.tenant_id, OrderLine.routing_id == routing_id, OrderLine.deleted_at.is_(None))))


@router.get("/products/{pid}/routings")
def product_routings(pid: int, user: User = Depends(require(*READERS)), db: Session = DB):
    get_or_404(db, Product, pid, user)
    rs = db.scalars(scoped(Routing, user).where(Routing.product_id == pid).order_by(Routing.version_no.desc()))
    return [routing_out(r, _routing_used(db, user, r.id)) for r in rs]


@router.get("/routings")
def all_routings(user: User = Depends(require(*READERS)), db: Session = DB):
    """Все версии маршрутов организации (кратко) — для выбора версии изделия в заказе."""
    rs = db.scalars(scoped(Routing, user).order_by(Routing.product_id, Routing.version_no.desc()))
    return {"items": [{"id": r.id, "product_id": r.product_id, "version_no": r.version_no, "name": r.name,
                       "is_current": r.is_current, "operations": len(r.operations)} for r in rs]}


class RoutingPatch(BaseModel):
    name: str = Field(max_length=120)


@router.patch("/routings/{rid}")
def rename_routing(rid: int, body: RoutingPatch, user: User = Depends(require(*CATALOG)), db: Session = DB):
    """Переименовать версию. Структуру версии это не меняет, поэтому можно и для используемой в заказах."""
    r = get_or_404(db, Routing, rid, user)
    name = body.name.strip()
    if r.name != name:
        audit.log(db, user, "routing", r.id, "update", {"name": [r.name, name]})
        r.name = name
    return routing_out(r, _routing_used(db, user, r.id))


@router.post("/products/{pid}/routings", status_code=201)
def new_routing_version(pid: int, copy: bool = True, name: str = "", user: User = Depends(require(*CATALOG)), db: Session = DB):
    """Новая версия маршрута; существующие позиции остаются на своей версии (BR-7)."""
    get_or_404(db, Product, pid, user)
    prev = db.scalar(scoped(Routing, user).where(Routing.product_id == pid, Routing.is_current.is_(True)))
    last = db.scalar(select(func.max(Routing.version_no)).where(Routing.tenant_id == user.tenant_id, Routing.product_id == pid)) or 0
    if prev:
        prev.is_current = False
    r = Routing(tenant_id=user.tenant_id, product_id=pid, version_no=last + 1, name=name.strip()[:120], is_current=True)
    db.add(r); db.flush()
    if prev and copy:
        for o in prev.operations:
            db.add(Operation(tenant_id=user.tenant_id, routing_id=r.id, name=o.name, norm_per_day=o.norm_per_day,
                             unit=o.unit, min_skill=o.min_skill, sequence=o.sequence, per_item=o.per_item))
        db.flush()
        db.refresh(r)
    audit.log(db, user, "routing", r.id, "create", {"product_id": pid, "version_no": r.version_no, "name": r.name})
    return routing_out(r, False)


class OperationIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    norm_per_day: float = Pos
    unit: str = Field("шт", max_length=24)
    min_skill: float = Field(1, gt=0, le=10)
    sequence: float = Field(1, ge=0)
    per_item: float = Field(1, gt=0)
    hold_qty: float = Field(0, ge=0)
    hold_reason: str = ""
    hold_until: Optional[date] = None


class OperationPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=160)
    norm_per_day: Optional[float] = Field(None, gt=0)
    unit: Optional[str] = None
    min_skill: Optional[float] = Field(None, gt=0, le=10)
    sequence: Optional[float] = Field(None, ge=0)
    per_item: Optional[float] = Field(None, gt=0)
    hold_qty: Optional[float] = Field(None, ge=0)
    hold_reason: Optional[str] = None
    hold_until: Optional[date] = None
    version: Optional[int] = None


HOLD_FIELDS = {"hold_qty", "hold_reason", "hold_until"}
SAFE_FIELDS = HOLD_FIELDS | {"name"}          # то, что можно менять в маршруте, уже стоящем в заказах (название операции не влияет на расчёт)


def levels_count(db, user) -> int:
    s = db.get(PlannerSettings, user.tenant_id)
    return len(s.qualifications or []) if s else MAX_QUAL_LEVELS


def check_level(db, user, value, what: str):
    n = levels_count(db, user)
    if value is not None and value > n:
        raise HTTPException(422, f"{what}: в организации {n} ур. квалификации. Число уровней настраивается в «Организация → Об организации»")
_IMMUTABLE = "Маршрут используется в позициях заказов. Создайте новую версию маршрута (изменять можно только название и блокировку операции)"


@router.post("/routings/{rid}/operations", status_code=201)
def add_operation(rid: int, body: OperationIn, user: User = Depends(require(*CATALOG)), db: Session = DB):
    r = get_or_404(db, Routing, rid, user)
    if _routing_used(db, user, rid):
        raise HTTPException(409, _IMMUTABLE)
    if db.scalar(select(Operation.id).where(Operation.routing_id == rid, Operation.name == body.name)):
        raise HTTPException(409, "Операция с таким названием уже есть в маршруте")
    check_level(db, user, body.min_skill, "Требуемая квалификация")
    o = Operation(tenant_id=user.tenant_id, routing_id=rid, **body.model_dump())
    db.add(o); db.flush()
    audit.log(db, user, "operation", o.id, "create", body.model_dump())
    return row(o)


@router.patch("/operations/{oid}")
def patch_operation(oid: int, body: OperationPatch, user: User = Depends(require(*CATALOG)), db: Session = DB):
    o = get_or_404(db, Operation, oid, user)
    data = body.model_dump(exclude_unset=True)
    ver = data.pop("version", None)
    if ver is not None and ver != o.version:
        raise HTTPException(409, "Запись изменена другим пользователем. Обновите страницу")
    if (set(data) - SAFE_FIELDS) and _routing_used(db, user, o.routing_id):
        raise HTTPException(409, _IMMUTABLE)
    if data.get("min_skill") is not None:
        check_level(db, user, data["min_skill"], "Требуемая квалификация")
    if "name" in data and db.scalar(select(Operation.id).where(
            Operation.routing_id == o.routing_id, Operation.name == data["name"], Operation.id != o.id)):
        raise HTTPException(409, "Операция с таким названием уже есть в маршруте")
    changes = {k: [getattr(o, k), v] for k, v in data.items() if getattr(o, k) != v}
    for k, (_, v) in changes.items():
        setattr(o, k, v)
    if changes:
        o.version += 1
        audit.log(db, user, "operation", o.id, "update", changes)
    return row(o)


@router.delete("/operations/{oid}", status_code=204)
def delete_operation(oid: int, user: User = Depends(require(*CATALOG)), db: Session = DB):
    o = get_or_404(db, Operation, oid, user)
    if _routing_used(db, user, o.routing_id):
        raise HTTPException(409, _IMMUTABLE)
    db.delete(o)
    audit.log(db, user, "operation", oid, "delete")


# ------------------------------------------------------------------ позиции заказов
ORDER_STATUSES = "^(draft|planned|done|cancelled)$"


class OrderLineIn(BaseModel):
    project_id: int
    product_id: int
    routing_id: Optional[int] = None
    ext_id: Optional[str] = Field(None, max_length=64)
    customer: str = Field("", max_length=160)
    qty: float = Field(gt=0, le=10_000_000)
    due_date: Optional[date] = None
    priority: int = Field(1, ge=1, le=99)
    status: str = Field("planned", pattern=ORDER_STATUSES)


class OrderLinePatch(BaseModel):
    project_id: Optional[int] = None
    routing_id: Optional[int] = None
    ext_id: Optional[str] = Field(None, max_length=64)
    customer: Optional[str] = Field(None, max_length=160)
    qty: Optional[float] = Field(None, gt=0, le=10_000_000)
    due_date: Optional[date] = None
    priority: Optional[int] = Field(None, ge=1, le=99)
    status: Optional[str] = Field(None, pattern=ORDER_STATUSES)
    version: Optional[int] = None


def _ext_free(db, user, ext_id, exclude_id=None):
    if not ext_id:
        return
    q = scoped(OrderLine, user).where(OrderLine.ext_id == ext_id)
    if exclude_id:
        q = q.where(OrderLine.id != exclude_id)
    if db.scalar(q.limit(1)):
        raise HTTPException(409, f"Позиция с номером «{ext_id}» уже есть")


def _ol_before_create(db, user, data):
    data["ext_id"] = (data.get("ext_id") or "").strip() or None
    _ext_free(db, user, data["ext_id"])
    if data.get("routing_id") is None:
        cur = db.scalar(scoped(Routing, user).where(Routing.product_id == data["product_id"], Routing.is_current.is_(True)))
        if not cur:
            raise HTTPException(422, "У изделия нет маршрута")
        data["routing_id"] = cur.id
    else:
        r = get_or_404(db, Routing, data["routing_id"], user)
        if r.product_id != data["product_id"]:
            raise HTTPException(422, "Маршрут относится к другому изделию")
    if not db.scalar(select(func.count()).select_from(Operation).where(Operation.routing_id == data["routing_id"])):
        raise HTTPException(422, "В маршруте изделия нет ни одной операции — добавьте операции")
    return data


def _ol_before_update(db, user, obj, data):
    for k in ("routing_id", "customer"):          # пустое значение не стирает обязательное поле
        if k in data and data[k] is None:
            data.pop(k)
    if "ext_id" in data:
        data["ext_id"] = (data["ext_id"] or "").strip() or None
        _ext_free(db, user, data["ext_id"], obj.id)
    if data.get("routing_id") is not None:
        r = get_or_404(db, Routing, data["routing_id"], user)
        if r.product_id != obj.product_id:
            raise HTTPException(422, "Маршрут относится к другому изделию")
    return data


def _ol_before_delete(db, user, obj):
    db.execute(delete(WorkerOrderLine).where(WorkerOrderLine.order_line_id == obj.id,
                                             WorkerOrderLine.tenant_id == user.tenant_id))


make_crud(router, "/order-lines", OrderLine, OrderLineIn, OrderLinePatch, "order_line",
          fk={"project_id": Project, "product_id": Product}, before_create=_ol_before_create,
          before_update=_ol_before_update, before_delete=_ol_before_delete)


# ------------------------------------------------------------------ допуски исполнителей к позициям (BR-9)
class AccessIn(BaseModel):
    order_line_ids: list[int]


@router.get("/workers/{wid}/access")
def get_access(wid: int, user: User = Depends(require(*READERS)), db: Session = DB):
    get_or_404(db, Worker, wid, user)
    ids = db.scalars(select(WorkerOrderLine.order_line_id).where(
        WorkerOrderLine.tenant_id == user.tenant_id, WorkerOrderLine.worker_id == wid))
    return {"order_line_ids": sorted(ids), "note": "Пусто — допущен ко всем позициям"}


@router.put("/workers/{wid}/access")
def put_access(wid: int, body: AccessIn, user: User = Depends(require(*MANAGERS)), db: Session = DB):
    get_or_404(db, Worker, wid, user)
    ids = sorted(set(body.order_line_ids))
    for i in ids:
        get_or_404(db, OrderLine, i, user)
    db.execute(delete(WorkerOrderLine).where(WorkerOrderLine.tenant_id == user.tenant_id, WorkerOrderLine.worker_id == wid))
    for i in ids:
        db.add(WorkerOrderLine(tenant_id=user.tenant_id, worker_id=wid, order_line_id=i))
    audit.log(db, user, "worker_access", wid, "update", {"order_line_ids": ids})
    return {"order_line_ids": ids}


# ------------------------------------------------------------------ настройки планировщика
class SettingsPatch(BaseModel):
    max_workers_per_op: Optional[int] = Field(None, ge=1, le=50)
    freeze_days: Optional[int] = Field(None, ge=0, le=5)
    today_load_threshold: Optional[float] = Field(None, ge=0, le=1)
    min_start_share: Optional[float] = Field(None, ge=0.1, le=1)
    priority_strictness: Optional[float] = Field(None, ge=0, le=100)
    strictness_by_level: Optional[dict[str, float]] = None
    efficiency: Optional[float] = Field(None, gt=0, le=1.5)
    work_weekdays: Optional[str] = Field(None, pattern=r"^[0-6](,[0-6])*$")
    shift_hours: Optional[float] = Field(None, gt=0, le=24)
    fix_group_start: Optional[bool] = None
    fact_confirmation: Optional[bool] = None
    qualifications: Optional[list[str]] = Field(None, min_length=1, max_length=MAX_QUAL_LEVELS)


def settings_out(s: PlannerSettings) -> dict:
    return {c.name: getattr(s, c.name) for c in s.__table__.columns if c.name not in ("tenant_id", "updated_by")}


@router.get("/settings")
def get_settings(user: User = Depends(require(*READERS)), db: Session = DB):
    return settings_out(db.get(PlannerSettings, user.tenant_id))


@router.patch("/settings")
def patch_settings(body: SettingsPatch, user: User = Depends(require(*MANAGERS)), db: Session = DB):
    s = db.get(PlannerSettings, user.tenant_id)
    data = body.model_dump(exclude_unset=True)
    if "strictness_by_level" in data and data["strictness_by_level"] is not None:
        for k, v in data["strictness_by_level"].items():
            if not k.isdigit() or not 0 <= v <= 100:
                raise HTTPException(422, "Жёсткость по уровню: ключ — номер приоритета, значение — 0…100")
    if data.get("qualifications") is not None:
        names = [str(x).strip() for x in data["qualifications"]]
        if any(not x or len(x) > 40 for x in names) or len({x.lower() for x in names}) != len(names):
            raise HTTPException(422, "Названия уровней: непустые (до 40 знаков) и без повторов")
        data["qualifications"] = names
        top_w = db.scalar(select(func.max(Worker.skill)).where(Worker.tenant_id == user.tenant_id)) or 0
        top_o = db.scalar(select(func.max(Operation.min_skill)).where(Operation.tenant_id == user.tenant_id)) or 0
        if max(top_w, top_o) > len(names):
            raise HTTPException(409, f"Уровней не может быть меньше {int(-(-max(top_w, top_o) // 1))}: этот уровень указан у работников или операций. Сначала измените их")
    changes = {}
    for k, v in data.items():
        if v is not None and getattr(s, k) != v:
            changes[k] = [getattr(s, k), v]
            setattr(s, k, v)
    if changes:
        s.updated_by = user.id
        audit.log(db, user, "settings", user.tenant_id, "update", changes)
    return settings_out(s)


# ------------------------------------------------------------------ журнал изменений
@router.get("/audit")
def audit_list(limit: int = 100, before: int = 0, entity: Optional[str] = None,
               user: User = Depends(require(*MANAGERS)), db: Session = DB):
    q = select(AuditLog, User.name).outerjoin(User, User.id == AuditLog.user_id).where(AuditLog.tenant_id == user.tenant_id)
    if before:
        q = q.where(AuditLog.id < before)
    if entity:
        q = q.where(AuditLog.entity == entity)
    lim = min(max(limit, 1), 500)
    rows = db.execute(q.order_by(AuditLog.id.desc()).limit(lim + 1)).all()
    more = len(rows) > lim
    rows = rows[:lim]
    return {"items": [{"id": a.id, "at": a.created_at.isoformat(), "user": n, "entity": a.entity, "entity_id": a.entity_id,
                       "action": a.action, "changes": a.changes} for a, n in rows],
            "next_before": rows[-1][0].id if more else None}
