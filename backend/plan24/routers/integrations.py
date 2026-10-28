"""Telegram: вебхук бота и выдача/отзыв кодов привязки (ТЗ раздел 9, «Интеграции»)."""
from __future__ import annotations

import hmac
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .. import audit, config
from ..db import DB
from ..deps import MANAGERS, STAFF, get_or_404, require, scoped
from ..models import TelegramCode, TelegramLink, TelegramState, User, Worker, utcnow
from ..telegram import bot, client

router = APIRouter(tags=["telegram"])
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # без 0/O/1/I: код вводят вручную


@router.post("/integrations/telegram/webhook/{secret}", include_in_schema=False)
def webhook(secret: str, request: Request, bg: BackgroundTasks, body: dict = Body(...), db: Session = DB):
    cfg = config.telegram_settings()
    # неверный секрет в URL — 404 (существование адреса не раскрываем); неверная подпись заголовка — 403
    if not cfg["enabled"] or not hmac.compare_digest(secret, cfg["secret"]):
        raise HTTPException(404, "Not found")
    if not hmac.compare_digest(request.headers.get("x-telegram-bot-api-secret-token", ""), cfg["header_secret"]):
        raise HTTPException(403, "Bad signature")
    out: list = []
    bot.handle_update(db, body, out)
    bg.add_task(client.deliver, out)                  # после ответа вебхуку и после коммита
    return {"ok": True}


class CodeIn(BaseModel):
    worker_id: Optional[int] = None
    user_id: Optional[int] = None


def _identity_name(db, link: TelegramLink) -> tuple:
    if link.worker_id:
        w = db.get(Worker, link.worker_id)
        return "worker", w.name if w else "?"
    u = db.get(User, link.user_id)
    return "user", u.name if u else "?"


def _foreman_owns(db: Session, user: User, worker_id) -> bool:
    if worker_id is None or user.work_center_id is None:
        return False
    w = db.get(Worker, worker_id)
    return w is not None and w.tenant_id == user.tenant_id and w.work_center_id == user.work_center_id


@router.get("/telegram/status")
def status(user: User = Depends(require(*STAFF)), db: Session = DB):
    cfg = config.telegram_settings()
    links = []
    for l in db.scalars(scoped(TelegramLink, user).order_by(TelegramLink.id)):
        if user.role == "foreman" and not _foreman_owns(db, user, l.worker_id):      # бригадир видит привязки своих работников
            continue
        kind, name = _identity_name(db, l)
        links.append({"id": l.id, "kind": kind, "worker_id": l.worker_id, "user_id": l.user_id, "name": name,
                      "linked_at": l.linked_at.isoformat()})
    return {"configured": cfg["enabled"], "bot_username": cfg["bot_username"], "links": links}


@router.post("/telegram/codes", status_code=201)
def issue_code(body: CodeIn, user: User = Depends(require(*STAFF)), db: Session = DB):
    """Руководитель выдаёт одноразовый код исполнителю или бригадиру; бригадир — только исполнителям своего участка."""
    if user.role == "foreman" and not (body.worker_id is not None and _foreman_owns(db, user, body.worker_id)):
        raise HTTPException(403, "Бригадир выдаёт коды только работникам своего участка")
    cfg = config.telegram_settings()
    if not cfg["enabled"]:
        raise HTTPException(409, "Telegram-бот не настроен администратором сервиса")
    if (body.worker_id is None) == (body.user_id is None):
        raise HTTPException(422, "Укажите исполнителя или пользователя (бригадира)")
    if body.worker_id is not None:
        who = get_or_404(db, Worker, body.worker_id, user).name
    else:
        u = get_or_404(db, User, body.user_id, user)
        if u.role != "foreman":
            raise HTTPException(422, "Telegram доступен исполнителям и бригадирам")
        who = u.name
    # действует только последний выданный код
    db.execute(update(TelegramCode).where(TelegramCode.tenant_id == user.tenant_id, TelegramCode.used_at.is_(None),
                                          TelegramCode.worker_id == body.worker_id, TelegramCode.user_id == body.user_id)
               .values(used_at=utcnow()))
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    db.add(TelegramCode(tenant_id=user.tenant_id, code_hash=bot.code_hash(raw), worker_id=body.worker_id,
                        user_id=body.user_id, created_by=user.id, expires_at=utcnow() + timedelta(seconds=config.TG_CODE_TTL)))
    audit.log(db, user, "telegram", body.worker_id or body.user_id, "code_issued", {"for": who})
    uname = cfg["bot_username"]
    return {"code": f"{raw[:4]}-{raw[4:]}", "for": who, "expires_in_hours": config.TG_CODE_TTL // 3600,
            "deep_link": f"https://t.me/{uname}?start={raw}" if uname else None,
            "instruction": f"Откройте бота{' @' + uname if uname else ''} и отправьте: /start {raw[:4]}-{raw[4:]}"}


@router.delete("/telegram/links/{lid}", status_code=204)
def unlink(lid: int, user: User = Depends(require(*STAFF)), db: Session = DB):
    l = get_or_404(db, TelegramLink, lid, user)
    if user.role == "foreman" and not _foreman_owns(db, user, l.worker_id):
        raise HTTPException(404, "Не найдено")
    audit.log(db, user, "telegram", lid, "unlinked")
    # состояние диалога принадлежит организации; если оставить его, чат нельзя будет привязать к другой организации
    # (запись с тем же chat_id в чужой организации невидима, а вставка нового состояния упирается в первичный ключ)
    db.execute(delete(TelegramState).where(TelegramState.chat_id == l.chat_id))
    db.delete(l)
