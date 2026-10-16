"""Имя пользователя: хранится раздельно (имя, фамилия) и собирается в полное «Имя Фамилия» (User.name)."""
from __future__ import annotations

from typing import Optional, Tuple

from fastapi import HTTPException


def split_name(name: str) -> Tuple[str, str]:
    """«Иван Петров» → («Иван», «Петров»): первое слово — имя, остальное — фамилия."""
    parts = (name or "").strip().split(None, 1)
    return (parts[0] if parts else ""), (parts[1].strip() if len(parts) > 1 else "")


def full_name(first: str, last: str) -> str:
    return f"{(first or '').strip()} {(last or '').strip()}".strip()


def resolve(first: Optional[str], last: Optional[str], name: Optional[str]) -> Tuple[str, str, str]:
    """Имя и фамилия из тела запроса: отдельные поля; для совместимости со старым клиентом — одно поле name."""
    legacy = first is None and last is None
    if legacy:
        first, last = split_name(name or "")
    first, last = (first or "").strip(), (last or "").strip()
    if len(first) < 1:
        raise HTTPException(422, "Укажите имя")
    if len(last) < 1 and not legacy:           # старый клиент мог прислать одно слово; в новых полях фамилия обязательна
        raise HTTPException(422, "Укажите фамилию")
    return first, last, full_name(first, last)
