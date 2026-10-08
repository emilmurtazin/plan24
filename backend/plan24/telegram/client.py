"""Клиент Bot API на httpx. Тексты сообщений и chat_id в логи не пишутся (ТЗ 12)."""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from .. import config

log = logging.getLogger("plan24.telegram")
API = "https://api.telegram.org"


class TelegramClient:
    def __init__(self, token: str, transport: Optional[httpx.BaseTransport] = None, timeout: float = 10.0,
                 api_base: str = API, proxy: Optional[str] = None):
        self._token = token
        self._base = api_base.rstrip("/")
        kw = {"proxy": proxy} if proxy else {}
        self._http = httpx.Client(timeout=timeout, transport=transport, **kw)

    def _call(self, method: str, payload: dict, timeout: Optional[float] = None) -> dict:
        r = self._http.post(f"{self._base}/bot{self._token}/{method}", json=payload,
                            **({"timeout": timeout} if timeout else {}))
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('error_code')}")
        return data["result"]

    def send_message(self, chat_id: int, text: str, reply_markup: Optional[dict] = None):
        p = {"chat_id": chat_id, "text": text[:4000]}
        if reply_markup:
            p["reply_markup"] = reply_markup
        return self._call("sendMessage", p)

    def answer_callback(self, callback_id: str, text: Optional[str] = None):
        return self._call("answerCallbackQuery", {"callback_query_id": callback_id, **({"text": text[:190]} if text else {})})

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
    return TelegramClient(cfg["token"], api_base=cfg["api_base"], proxy=cfg["proxy"]) if cfg["enabled"] else None


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
        except Exception as e:                                     # noqa: BLE001
            log.error("telegram delivery failed: %s", type(e).__name__)
