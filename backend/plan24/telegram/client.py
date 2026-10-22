"""Клиент Bot API на httpx. Тексты сообщений и chat_id в логи не пишутся (ТЗ 12)."""
from __future__ import annotations

import logging
import ssl
from typing import Optional

import httpx

from .. import config

log = logging.getLogger("plan24.telegram")
API = "https://api.telegram.org"


class TelegramClient:
    def __init__(self, token: str, transport: Optional[httpx.BaseTransport] = None, timeout: float = 10.0,
                 api_base: str = API, proxy: Optional[str] = None, ca_pem: Optional[str] = None):
        self._token = token
        self._base = api_base.rstrip("/")
        kw = {"proxy": proxy} if proxy else {}
        if ca_pem:                          # релей по IP с самоподписанным сертификатом: доверяем только ему (закрепление сертификата)
            kw["verify"] = ssl.create_default_context(cadata=ca_pem)
        self._http = httpx.Client(timeout=timeout, transport=transport, **kw)

    def _call(self, method: str, payload: dict, timeout: Optional[float] = None) -> dict:
        r = self._http.post(f"{self._base}/bot{self._token}/{method}", json=payload,
                            **({"timeout": timeout} if timeout else {}))
        try:
            data = r.json()
        except ValueError:                  # релей/прокси вернул не JSON (502 Bad Gateway и т. п.)
            raise RuntimeError(f"telegram {method}: {r.status_code} (ответ не от Telegram — релей не смог дойти до api.telegram.org?)") from None
        if not data.get("ok"):
            # описание ошибки Telegram (например, «message is not modified») — служебный текст без данных пользователей
            raise RuntimeError(f"telegram {method}: {data.get('error_code')} {str(data.get('description') or '')[:80]}".rstrip())
        return data["result"]

    def send_message(self, chat_id: int, text: str, reply_markup: Optional[dict] = None):
        p = {"chat_id": chat_id, "text": text[:4000]}
        if reply_markup:
            p["reply_markup"] = reply_markup
        return self._call("sendMessage", p)

    def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None):
        p = {"chat_id": chat_id, "message_id": message_id, "text": text[:4000]}
        if reply_markup:
            p["reply_markup"] = reply_markup
        return self._call("editMessageText", p)

    def copy_message(self, chat_id: int, from_chat_id: int, message_id: int):
        """Переслать чужое сообщение (фото, голос, файл) без подписи «Переслано от»."""
        return self._call("copyMessage", {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id})

    def set_commands(self, chat_id: int, commands: list):
        """Меню команд (кнопка «/» рядом с полем ввода) — своё для каждого чата, по роли."""
        return self._call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands],
                                            "scope": {"type": "chat", "chat_id": chat_id}})

    def set_menu_button(self, chat_id: int, url: str, text: str = "Приложение"):
        return self._call("setChatMenuButton", {"chat_id": chat_id, "menu_button": {"type": "web_app", "text": text, "web_app": {"url": url}}})

    def answer_callback(self, callback_id: str, text: Optional[str] = None):
        return self._call("answerCallbackQuery", {"callback_query_id": callback_id, **({"text": text[:190]} if text else {})})

    def get_me(self):
        return self._call("getMe", {})

    def get_webhook_info(self):
        return self._call("getWebhookInfo", {})

    def set_webhook(self, url: str, secret_token: str):
        return self._call("setWebhook", {"url": url, "secret_token": secret_token, "allowed_updates": ["message", "callback_query"]})

    def delete_webhook(self):
        return self._call("deleteWebhook", {})

    def get_updates(self, offset: int, timeout: int = 25):
        # long polling: Telegram держит соединение до `timeout` секунд — клиентский таймаут должен быть заметно больше
        return self._call("getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
                          timeout=timeout + 15)


_override = None          # тесты подставляют поддельный клиент


def set_client(c):
    global _override
    _override = c


def get_client():
    if _override is not None:
        return _override
    cfg = config.telegram_settings()
    return TelegramClient(cfg["token"], api_base=cfg["api_base"], proxy=cfg["proxy"], ca_pem=cfg["ca_pem"]) if cfg["enabled"] else None


def deliver(outbox: list):
    """Отправляет накопленные сообщения после ответа вебхуку. Сбой одного не мешает остальным."""
    c = get_client()
    if c is None:
        return
    for item in outbox:
        try:
            if item["kind"] == "message":
                c.send_message(item["chat_id"], item["text"], item.get("markup"))
            elif item["kind"] == "answer":
                c.answer_callback(item["callback_id"], item.get("text"))
            elif item["kind"] == "edit":
                try:
                    c.edit_message(item["chat_id"], item["message_id"], item["text"], item.get("markup"))
                except RuntimeError as e:
                    if "not modified" in str(e):
                        continue                                            # тот же текст — менять нечего
                    c.send_message(item["chat_id"], item["text"], item.get("markup"))    # сообщение устарело/удалено — шлём новое
            elif item["kind"] == "copy":
                c.copy_message(item["chat_id"], item["from_chat_id"], item["message_id"])
            elif item["kind"] == "commands":
                c.set_commands(item["chat_id"], item["commands"])
            elif item["kind"] == "menu_button":
                c.set_menu_button(item["chat_id"], item["url"], item.get("text", "Приложение"))
        except Exception as e:                                     # noqa: BLE001
            log.error("telegram delivery failed: %s", type(e).__name__)
