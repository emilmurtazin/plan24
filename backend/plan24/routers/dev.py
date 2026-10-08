"""Отладочные маршруты. Существуют только при PLAN24_ENV=dev; в prod отвечают 404."""
from fastapi import APIRouter, HTTPException

from .. import config, mailer

router = APIRouter(prefix="/dev", tags=["dev"], include_in_schema=False)


@router.get("/outbox")
def outbox(to: str | None = None):
    if config.env() != "dev":
        raise HTTPException(404, "Not found")
    mails = [m for m in mailer.OUTBOX if not to or m["to"] == to.lower()]
    return {"items": [{"to": m["to"], "subject": m["subject"], "text": m["text"]} for m in mails]}
