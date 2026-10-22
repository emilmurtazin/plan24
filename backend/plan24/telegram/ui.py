"""Вывод бота: сообщения, кнопки, постоянное меню, форматирование. Без обращений к данным — модуль можно импортировать откуда угодно."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MAX_QTY = 1_000_000


def say(out: list, chat_id: int, text: str, markup: Optional[dict] = None):
    out.append({"kind": "message", "chat_id": chat_id, "text": text, **({"markup": markup} if markup else {})})


def show(out: list, chat_id: int, message_id: Optional[int], text: str, markup: Optional[dict] = None):
    """Показать экран: если известно сообщение, из которого пришла кнопка, — переписать его на месте, иначе — новое сообщение."""
    if message_id:
        out.append({"kind": "edit", "chat_id": chat_id, "message_id": message_id, "text": text, **({"markup": markup} if markup else {})})
    else:
        say(out, chat_id, text, markup)


def answer(out: list, cb_id: str, text: Optional[str] = None):
    out.append({"kind": "answer", "callback_id": cb_id, **({"text": text} if text else {})})


def copy_message(out: list, chat_id: int, from_chat_id: int, message_id: int):
    out.append({"kind": "copy", "chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id})


def kb(rows: list) -> dict:
    """Кнопки под сообщением: (текст, callback_data) или готовый словарь кнопки (например, web_app)."""
    return {"inline_keyboard": [[b if isinstance(b, dict) else {"text": b[0], "callback_data": b[1]} for b in row] for row in rows]}


def app_button(url: str, text: str = "📱 Открыть приложение") -> dict:
    """Кнопка запуска мини-приложения Telegram (передаёт приложению подписанные данные пользователя)."""
    return {"text": text, "web_app": {"url": url}}


def menu_button(out: list, chat_id: int, url: str):
    """Синяя кнопка «Приложение» рядом с полем ввода — для этого чата."""
    out.append({"kind": "menu_button", "chat_id": chat_id, "url": url, "text": "Приложение"})


def fnum(x) -> str:
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def fday(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d} ({WD[d.weekday()]})"


def parse_qty(text: str) -> Optional[float]:
    t = (text or "").strip().replace(",", ".").replace(" ", "")
    if not re.fullmatch(r"\d{1,7}(\.\d{1,3})?", t):
        return None
    v = float(t)
    return v if 0 <= v <= MAX_QTY else None


# ------------------------------------------------------------------ постоянное меню внизу экрана и меню команд
MENU = {
    "worker": [["📋 Задания", "✅ Закрыть день"], ["⚠ Проблема", "🤒 Не выйду"], ["📅 Завтра", "⚙ Настройки"]],
    "foreman": [["👥 По людям", "✅ Закрыть день"], ["📥 Очередь", "🤒 Отсутствие"], ["📅 Завтра", "⚙ Настройки"]],
}
# текст кнопки меню (без значка, в нижнем регистре) → действие
MENU_ACTIONS = {"задания": "tasks", "по людям": "tasks", "мои задания": "tasks", "план": "tasks", "закрыть день": "closeday",
                "проблема": "problem", "не выйду": "absent", "отсутствие": "absent", "завтра": "tomorrow",
                "настройки": "settings", "очередь": "queue", "отменить": "undo"}
COMMANDS = {
    "worker": [("tasks", "Задания на сегодня"), ("closeday", "Закрыть день по плану"), ("problem", "Сообщить о проблеме"),
               ("absent", "Не смогу выйти"), ("tomorrow", "Что завтра"), ("undo", "Отменить последний ввод"),
               ("settings", "Настройки уведомлений"), ("help", "Помощь")],
    "foreman": [("tasks", "Участок: кто что делает"), ("closeday", "Закрыть день по плану"), ("queue", "Факт на подтверждение"),
                ("absent", "Отметить отсутствие"), ("tomorrow", "Что завтра"), ("undo", "Отменить последний ввод"),
                ("settings", "Настройки уведомлений"), ("help", "Помощь")],
}


def menu_keyboard(role: str) -> dict:
    rows = MENU["foreman" if role == "foreman" else "worker"]
    return {"keyboard": [[{"text": t} for t in row] for row in rows], "resize_keyboard": True, "is_persistent": True}


def menu_action(text: str) -> Optional[str]:
    """Нажатие кнопки меню приходит обычным текстом: «📋 Задания» → tasks."""
    key = re.sub(r"^[^\wа-яё]+", "", (text or "").lower()).strip()
    return MENU_ACTIONS.get(key)


def set_commands(out: list, chat_id: int, role: str):
    out.append({"kind": "commands", "chat_id": chat_id, "commands": COMMANDS["foreman" if role == "foreman" else "worker"]})
