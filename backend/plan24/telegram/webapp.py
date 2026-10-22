"""Проверка данных запуска мини-приложения Telegram (initData).

Telegram подписывает строку запуска ключом, производным от токена бота: подделать её без токена нельзя.
Алгоритм (документация Telegram, «Validating data received via the Mini App»):
  secret = HMAC_SHA256(key="WebAppData", msg=<токен бота>); hash = HMAC_SHA256(secret, строка "ключ=значение" по алфавиту через \\n).
Дополнительно проверяем свежесть auth_date, чтобы перехваченная строка не работала вечно.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import parse_qsl

MAX_LEN = 8192


class InitDataError(ValueError):
    """Данные запуска не прошли проверку (подпись, срок, формат)."""


def validate(init_data: str, bot_token: str, max_age: int = 86400, now: Optional[float] = None) -> dict:
    """→ {"user": {...}, "auth_date": int}. Поднимает InitDataError."""
    if not init_data or len(init_data) > MAX_LEN or not bot_token:
        raise InitDataError("пустые или слишком длинные данные")
    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    if len(data) != len(pairs):
        raise InitDataError("повторяющиеся поля")
    got = data.pop("hash", None)
    if not got:
        raise InitDataError("нет подписи")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got):
        raise InitDataError("подпись не совпала")
    try:
        auth_date = int(data["auth_date"])
        user = json.loads(data["user"])
        uid = int(user["id"])
    except (KeyError, ValueError, TypeError):
        raise InitDataError("нет auth_date или пользователя") from None
    age = (time.time() if now is None else now) - auth_date
    if age > max_age:
        raise InitDataError("данные устарели — откройте приложение заново")
    if age < -300:
        raise InitDataError("дата из будущего")
    user["id"] = uid
    return {"user": user, "auth_date": auth_date}
