from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AuditLog


def log(db: Session, user, entity: str, entity_id: int | None, action: str, changes: dict | None = None):
    ch = _jsonable(changes or {})
    if db.info.get("support"):                       # вход суперадмина под организацией: в журнале видно, кто на самом деле действовал
        ch = {**ch, "via_support": True}
    db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id, entity=entity, entity_id=entity_id,
                    action=action, changes=ch))


def _jsonable(o):
    from datetime import date, datetime
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return o
