"""Справочники и данные модуля «Производство»: участки, исполнители, отпуска, праздники, причины,
проекты, изделия + маршруты (версии), позиции заказов, допуски, настройки планировщика, журнал."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, object_session

from .. import audit, distribution
from ..db import DB
from ..deps import ALL, CATALOG, MANAGERS, READERS, STAFF, get_or_404, require, scoped
from ..models import (MAX_QUAL_LEVELS, AuditLog, OrderLineOperation, Fact, FactReason, Holiday, Operation, OrderLine, PlannerSettings, Product, Project,
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


class ReasonIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    work_center_id: Optional[int] = None          # участок причины; пусто — общая причина (для всех участков)


class ReasonPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=160)
    work_center_id: Optional[int] = None
    version: Optional[int] = None


def _reason_unique(db, user, name: str, center: Optional[int], skip_id: Optional[int] = None):
    key = name.strip().casefold()                       # сравнение в Python: lower() в SQLite не знает кириллицы
    for r in db.scalars(scoped(FactReason, user)):
        if r.id != skip_id and r.work_center_id == center and r.name.strip().casefold() == key:
            raise HTTPException(409, "У этого участка уже есть такая причина" if center else "Такая общая причина уже есть")


def _reason_before_create(db, user, data):
    data["name"] = data["name"].strip()
    _reason_unique(db, user, data["name"], data.get("work_center_id"))


def _reason_before_update(db, user, obj, data):
    name = (data.get("name") or obj.name).strip()
    center = data["work_center_id"] if "work_center_id" in data else obj.work_center_id
    if "name" in data:
        data["name"] = name
    _reason_unique(db, user, name, center, obj.id)


# причины нужны и сборщику при вводе факта; у каждого участка — свои (work_center_id), без участка — общие
make_crud(router, "/fact-reasons", FactReason, ReasonIn, ReasonPatch, "fact_reason", read_roles=ALL, fk={"work_center_id": WorkCenter},
          before_create=_reason_before_create, before_update=_reason_before_update)


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
    auto_plan: bool = True                 # участвует в автопланировании (временному работнику из «Плана на день» — нет)
    is_temporary: bool = False


class WorkerPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    work_center_id: Optional[int] = None
    perf: Optional[float] = Field(None, gt=0, le=10)
    skill: Optional[float] = Field(None, gt=0, le=10)
    status: Optional[str] = Field(None, pattern="^(available|unavailable)$")
    auto_plan: Optional[bool] = None
    is_temporary: Optional[bool] = None
    version: Optional[int] = None


def _worker_level_create(db, user, data):
    check_level(db, user, data.get("skill"), "Квалификация работника")


def _worker_level_update(db, user, obj, data):
    check_level(db, user, data.get("skill"), "Квалификация работника")


def _own_center(user):
    """Бригадир (руководитель участка) работает только с работниками своего участка; без участка — ни с кем."""
    if user.work_center_id is None:
        raise HTTPException(403, "Вам не назначен участок — обратитесь к администратору")
    return user.work_center_id


def _worker_scope(user, q):
    return q.where(Worker.work_center_id == _own_center(user)) if user.role == "foreman" else q


def _worker_check(user, w):
    if user.role == "foreman" and w.work_center_id != _own_center(user):
        raise HTTPException(404, "Не найдено")               # чужой участок для бригадира — как будто записи нет


def _worker_create(db, user, data):
    _worker_level_create(db, user, data)
    if user.role == "foreman":
        center = _own_center(user)
        if data.get("work_center_id") not in (None, center):
            raise HTTPException(403, "Бригадир добавляет работников только на свой участок")
        data["work_center_id"] = center                        # новый работник сразу на участке бригадира
    return data


def _worker_update(db, user, obj, data):
    _worker_level_update(db, user, obj, data)
    if user.role == "foreman" and "work_center_id" in data and data["work_center_id"] != obj.work_center_id:
        raise HTTPException(403, "Перевести работника на другой участок может руководитель проекта или администратор")
    return data


# работники: руководители — все, бригадир — полный доступ к работникам своего участка (создание, правка, удаление, допуски, отсутствия);
# перевести работника на другой участок бригадир не может — это право руководителя проекта
make_crud(router, "/workers", Worker, WorkerIn, WorkerPatch, "worker", fk={"work_center_id": WorkCenter},
          read_roles=READERS, write_roles=STAFF, row_scope=_worker_scope, row_check=_worker_check,
          before_create=_worker_create, before_update=_worker_update)


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


def _absence_scope(user, q):
    if user.role != "foreman":
        return q
    return q.where(WorkerAbsence.worker_id.in_(select(Worker.id).where(Worker.tenant_id == user.tenant_id,
                                                                       Worker.work_center_id == _own_center(user))))


def _absence_guard(db, user, worker_id):
    if user.role == "foreman":
        w = db.get(Worker, worker_id)
        if w is None or w.tenant_id != user.tenant_id or w.work_center_id != _own_center(user):
            raise HTTPException(404, "Не найдено")


def _absence_check(user, a):
    if user.role == "foreman":
        _absence_guard(object_session(a), user, a.worker_id)


def _absence_create(db, user, data):
    _absence_guard(db, user, data["worker_id"])
    return data


# отсутствия: руководители — любые, бригадир — только работников своего участка
make_crud(router, "/absences", WorkerAbsence, AbsenceIn, AbsencePatch, "absence", fk={"worker_id": Worker},
          write_roles=STAFF, row_scope=_absence_scope, row_check=_absence_check, before_create=_absence_create)


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


# ------------------------------------------------------------------ загрузка каталога из файла
CATALOG_IMPORT_LIMIT = 2000


class CatalogRow(BaseModel):
    product: str = ""
    unit: str = ""
    version_name: str = ""
    operation: str = ""
    sequence: str = ""
    norm_per_day: str = ""
    min_skill: str = ""               # число или название уровня квалификации организации
    per_item: str = ""


class CatalogImportIn(BaseModel):
    rows: list[CatalogRow] = Field(max_length=CATALOG_IMPORT_LIMIT)
    on_existing: str = Field("skip", pattern="^(skip|version)$")     # изделие уже есть: пропустить или добавить новую версию
    dry_run: bool = False


def _num(text: str) -> Optional[float]:
    t = (text or "").strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        raise ValueError(text)


@router.post("/products/import")
def import_catalog(body: CatalogImportIn, user: User = Depends(require(*CATALOG)), db: Session = DB):
    """Загрузка изделий и операций из файла (по строке на операцию; строки уже разобраны клиентом). Всё или ничего.
    Новое изделие создаётся с версией v1; если изделие есть — по выбору пропускается или получает новую версию."""
    if not body.rows:
        raise HTTPException(422, "В файле нет строк с изделиями")
    st = db.get(PlannerSettings, user.tenant_id)
    quals = [q.strip().lower() for q in ((st.qualifications if st else None) or [])]
    levels = len(quals) or MAX_QUAL_LEVELS
    errors, groups = [], {}
    for i, r in enumerate(body.rows, start=1):
        pname = r.product.strip()
        if not pname:
            errors.append({"row": i, "message": "Не указано изделие"})
            continue
        if len(pname) > 160:
            errors.append({"row": i, "message": "Название изделия длиннее 160 символов"})
            continue
        oname = r.operation.strip()
        try:
            norm, seq, per = _num(r.norm_per_day), _num(r.sequence), _num(r.per_item)
            skill_txt = r.min_skill.strip()
            if not skill_txt:
                skill = 1.0
            elif skill_txt.lower() in quals:
                skill = float(quals.index(skill_txt.lower()) + 1)
            else:
                skill = _num(skill_txt)
        except ValueError as e:
            errors.append({"row": i, "message": f"Не удалось прочитать число «{e.args[0]}»"})
            continue
        if not oname:
            errors.append({"row": i, "message": f"У изделия «{pname}» не указана операция"})
            continue
        if norm is None or norm <= 0:
            errors.append({"row": i, "message": f"Операция «{oname}»: норма за день должна быть больше нуля"})
            continue
        if per is not None and per <= 0:
            errors.append({"row": i, "message": f"Операция «{oname}»: «На изделие» должно быть больше нуля"})
            continue
        if skill is None or not (0 < skill <= levels):
            errors.append({"row": i, "message": f"Операция «{oname}»: квалификация должна быть уровнем от 1 до {levels}"})
            continue
        g = groups.setdefault(pname.lower(), {"name": pname, "unit": r.unit.strip()[:24] or "шт", "version": r.version_name.strip()[:120], "ops": []})
        if any(o["name"].lower() == oname.lower() for o in g["ops"]):
            errors.append({"row": i, "message": f"Операция «{oname}» повторяется у изделия «{pname}»"})
            continue
        g["ops"].append({"name": oname[:160], "norm_per_day": norm, "min_skill": skill, "per_item": per if per is not None else 1.0,
                         "sequence": seq if seq is not None else float(len(g["ops"]) + 1)})
    if errors:
        return {"created": 0, "versions": 0, "skipped": [], "errors": errors, "dry_run": body.dry_run}
    existing = {p.name.lower(): p for p in db.scalars(scoped(Product, user))}
    created = versions = 0
    skipped = []
    for key, g in groups.items():
        prod = existing.get(key)
        if prod is not None and body.on_existing == "skip":
            skipped.append(prod.name)
            continue
        if body.dry_run:
            versions, created = (versions + 1, created) if prod else (versions, created + 1)
            continue
        if prod is None:
            prod = Product(tenant_id=user.tenant_id, name=g["name"], unit=g["unit"])
            db.add(prod)
            db.flush()
            audit.log(db, user, "product", prod.id, "create", {"name": prod.name, "import": True})
            version_no = 1
            created += 1
        else:
            prev = db.scalar(scoped(Routing, user).where(Routing.product_id == prod.id, Routing.is_current.is_(True)))
            if prev:
                prev.is_current = False
            version_no = (db.scalar(select(func.max(Routing.version_no)).where(
                Routing.tenant_id == user.tenant_id, Routing.product_id == prod.id)) or 0) + 1
            versions += 1
        r = Routing(tenant_id=user.tenant_id, product_id=prod.id, version_no=version_no, name=g["version"], is_current=True)
        db.add(r)
        db.flush()
        for o in g["ops"]:
            db.add(Operation(tenant_id=user.tenant_id, routing_id=r.id, unit=prod.unit, **o))
        audit.log(db, user, "routing", r.id, "create", {"product_id": prod.id, "version_no": version_no, "operations": len(g["ops"]), "import": True})
    return {"created": created, "versions": versions, "skipped": skipped, "errors": [], "dry_run": body.dry_run}


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
        if r.id != obj.routing_id:           # другая версия — другие операции: распределение начинается заново
            distribution.clear(db, user, obj.id)
            distribution.auto_assign(db, user, obj.id, r.id)
    return data


def _ol_after_create(db, user, obj):
    distribution.auto_assign(db, user, obj.id, obj.routing_id)


def _ol_before_delete(db, user, obj):
    distribution.clear(db, user, obj.id)
    db.execute(delete(WorkerOrderLine).where(WorkerOrderLine.order_line_id == obj.id,
                                             WorkerOrderLine.tenant_id == user.tenant_id))


class DistributionItem(BaseModel):
    operation_id: int
    work_center_ids: list[int] = Field(max_length=200)


class DistributionIn(BaseModel):
    assignments: list[DistributionItem] = Field(max_length=500)


@router.get("/order-lines/{oid}/operations")
def order_line_operations(oid: int, user: User = Depends(require(*READERS)), db: Session = DB):
    """Операции позиции заказа и их участки. Бригадир видит только операции своего участка."""
    return distribution.view(db, user, get_or_404(db, OrderLine, oid, user))


@router.put("/order-lines/{oid}/operations")
def distribute_order_line(oid: int, body: DistributionIn, user: User = Depends(require(*MANAGERS)), db: Session = DB):
    """Задать участки операций позиции. Перечисленные операции получают ровно указанный набор участков
    (пустой список — операция снова не распределена); не перечисленные не меняются."""
    ol = get_or_404(db, OrderLine, oid, user)
    ops = {o.id: o for o in db.scalars(select(Operation).where(Operation.routing_id == ol.routing_id))}
    centers = {c.id: c for c in distribution.centers_of(db, user)}
    changes = {}
    for it in body.assignments:
        if it.operation_id not in ops:
            raise HTTPException(422, "Операция не относится к маршруту позиции")
        want = set(it.work_center_ids)
        if want - set(centers):
            raise HTTPException(422, "Участок не найден")
        have = set(db.scalars(select(OrderLineOperation.work_center_id).where(
            OrderLineOperation.order_line_id == ol.id, OrderLineOperation.operation_id == it.operation_id)))
        if have == want:
            continue
        db.execute(delete(OrderLineOperation).where(OrderLineOperation.order_line_id == ol.id,
                                                    OrderLineOperation.operation_id == it.operation_id))
        for cid in sorted(want):
            db.add(OrderLineOperation(tenant_id=user.tenant_id, order_line_id=ol.id, operation_id=it.operation_id, work_center_id=cid))
        changes[ops[it.operation_id].name] = [sorted(centers[c].name for c in have if c in centers), sorted(centers[c].name for c in want)]
    if changes:
        db.flush()
        audit.log(db, user, "order_line", ol.id, "distribute", changes)
    return distribution.view(db, user, ol)


@router.get("/order-distribution")
def order_distribution(user: User = Depends(require(*MANAGERS)), db: Session = DB):
    """Сколько операций каждой позиции распределено по участкам."""
    return {"items": distribution.summary(db, user)}


make_crud(router, "/order-lines", OrderLine, OrderLineIn, OrderLinePatch, "order_line",
          fk={"project_id": Project, "product_id": Product}, before_create=_ol_before_create,
          before_update=_ol_before_update, before_delete=_ol_before_delete, after_create=_ol_after_create)


# ------------------------------------------------------------------ допуски исполнителей к позициям (BR-9)
class AccessIn(BaseModel):
    order_line_ids: list[int]


def _access_worker(db, user, wid: int) -> Worker:
    w = get_or_404(db, Worker, wid, user)
    _worker_check(user, w)                                   # бригадиру чужой работник «не существует»
    return w


def _selectable_orders(db, user) -> Optional[set]:
    """Позиции, к которым можно допускать: руководителю — любые, бригадиру — те, где есть операции его участка."""
    return distribution.order_ids_of_center(db, user, user.work_center_id) if user.role == "foreman" else None


@router.get("/workers/{wid}/access")
def get_access(wid: int, user: User = Depends(require(*READERS)), db: Session = DB):
    _access_worker(db, user, wid)
    ids = db.scalars(select(WorkerOrderLine.order_line_id).where(
        WorkerOrderLine.tenant_id == user.tenant_id, WorkerOrderLine.worker_id == wid))
    sel = _selectable_orders(db, user)
    return {"order_line_ids": sorted(ids), "note": "Пусто — допущен ко всем позициям",
            "selectable_ids": None if sel is None else sorted(sel)}               # None — можно выбирать любые


@router.put("/workers/{wid}/access")
def put_access(wid: int, body: AccessIn, user: User = Depends(require(*STAFF)), db: Session = DB):
    """Допуски работника к позициям (пусто — ко всем). Бригадир задаёт допуски своих работников только по позициям своего участка;
    допуски к другим позициям (их ставит руководитель) он не видит и не меняет."""
    _access_worker(db, user, wid)
    ids = set(body.order_line_ids)
    for i in ids:
        get_or_404(db, OrderLine, i, user)
    sel = _selectable_orders(db, user)
    existing = set(db.scalars(select(WorkerOrderLine.order_line_id).where(WorkerOrderLine.tenant_id == user.tenant_id,
                                                                          WorkerOrderLine.worker_id == wid)))
    if sel is not None:
        if ids - sel:
            raise HTTPException(403, "Бригадир допускает работников только к позициям своего участка")
        ids |= existing - sel                                # чужие допуски сохраняются как есть
    ids = sorted(ids)
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
    return {c.name: getattr(s, c.name) for c in s.__table__.columns if c.name not in ("tenant_id", "updated_by", "snapshot_on")}


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
