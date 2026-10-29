"""Демо-данные организации (FR-41): синтетические наборы, даты привязаны к сегодняшнему дню организации."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..compat import norm_id
from ..db import DB
from ..deps import MANAGERS, get_today, require
from ..models import (Holiday, Operation, OrderLine, OrderLineOperation, Product, Project, Routing, User, WorkCenter, Worker,
                      WorkerOrderLine)
from ..synthetic import make_datasets

router = APIRouter(prefix="/demo", tags=["demo"])
BASE = date(2026, 10, 2)


@router.get("/kinds")
def kinds(user: User = Depends(require(*MANAGERS))):
    return {"kinds": [k for k in make_datasets() if not k.startswith("edge_")]}


def seed_dataset(db: Session, user: User, ds: dict, shift_days: int = 0, settings: bool = True) -> dict:
    """Записывает набор формата tools/emulate.js в организацию пользователя. Используется демо и тестами паритета."""
    t = user.tenant_id
    sh = lambda iso: date.fromisoformat(iso) + timedelta(days=shift_days)
    centers = [WorkCenter(tenant_id=t, name=n) for n in ("Участок 1", "Участок 2")]
    db.add_all(centers); db.flush()
    workers = {}
    for i, w in enumerate(ds["workers"]):
        wk = Worker(tenant_id=t, name=w["name"], perf=w["perf"], skill=w["skill"], work_center_id=centers[i % 2].id,
                    status="available" if str(w.get("status") or "доступен").lower() == "доступен" else "unavailable")
        db.add(wk); workers[w["name"]] = wk
    db.flush()

    ops_by_item: dict = {}
    for o in ds["ops"]:
        ops_by_item.setdefault(o["item"], []).append(o)
    for o in ds["orders"]:
        ops_by_item.setdefault(o["item"], [])          # изделие без операций тоже существует (предупреждение NO_OPERATIONS)
    products = {}
    for item, ops in ops_by_item.items():
        p = Product(tenant_id=t, name=item); db.add(p); db.flush()
        r = Routing(tenant_id=t, product_id=p.id, version_no=1, is_current=True); db.add(r); db.flush()
        for o in ops:
            db.add(Operation(tenant_id=t, routing_id=r.id, name=o["op"], norm_per_day=o["norm"], min_skill=o["skill"],
                             sequence=o["order"], per_item=o["perItem"], hold_qty=o["blocked"]))
        products[item] = (p, r)
    projects, lines = {}, {}
    for o in ds["orders"]:
        if o["project"] not in projects:
            pr = Project(tenant_id=t, name=o["project"]); db.add(pr); db.flush(); projects[o["project"]] = pr
        p, r = products[o["item"]]
        ol = OrderLine(tenant_id=t, project_id=projects[o["project"]].id, product_id=p.id, routing_id=r.id,
                       ext_id=str(o["id"]), qty=o["qty"], due_date=sh(o["due"]), priority=int(o["priority"]))
        db.add(ol); db.flush(); lines[norm_id(o["id"])] = ol
        for op_ in db.scalars(select(Operation).where(Operation.routing_id == r.id)):   # демо: операции на все участки
            for c in centers:
                db.add(OrderLineOperation(tenant_id=t, order_line_id=ol.id, operation_id=op_.id, work_center_id=c.id))
    for w in ds["workers"]:
        for oid in [norm_id(x) for x in str(w.get("projects") or "").split(",") if norm_id(x)]:
            if oid in lines:
                db.add(WorkerOrderLine(tenant_id=t, worker_id=workers[w["name"]].id, order_line_id=lines[oid].id))
    for h in ds["holidays"]:
        db.add(Holiday(tenant_id=t, holiday_date=sh(h), name="Праздничный день"))
    if settings:
        from ..models import PlannerSettings
        st, cfg = db.get(PlannerSettings, t), ds.get("settings") or {}
        if "strictness" in cfg:
            st.priority_strictness = float(cfg["strictness"])
        if "maxWorkers" in cfg:
            st.max_workers_per_op = int(cfg["maxWorkers"])
        st.freeze_days = 0 if cfg.get("_no_freeze") else st.freeze_days
        top = max([float(w["skill"]) for w in ds["workers"]] + [float(o["skill"]) for o in ds["ops"]] + [1])
        if len(st.qualifications or []) < top:          # уровни квалификации должны покрывать загруженные данные
            from ..models import default_quals
            st.qualifications = default_quals(int(-(-top // 1)))
    db.flush()
    return {"orders": len(lines), "workers": len(workers), "products": len(products)}


@router.post("/load", status_code=201)
def load_demo(kind: str = "furniture", user: User = Depends(require(*MANAGERS)), db: Session = DB,
              today: date = Depends(get_today)):
    sets = make_datasets()
    if kind not in sets or kind.startswith("edge_"):
        raise HTTPException(404, "Нет такого демо-набора")
    t = user.tenant_id
    if any(db.scalar(select(func.count()).select_from(m).where(m.tenant_id == t)) for m in (Product, Worker, OrderLine)):
        raise HTTPException(409, "Демо-данные загружаются только в пустую организацию")
    ds, _ = sets[kind]
    res = seed_dataset(db, user, ds, (today - BASE).days)
    audit.log(db, user, "demo", None, "load", {"kind": kind})
    return {"kind": kind, **res}
