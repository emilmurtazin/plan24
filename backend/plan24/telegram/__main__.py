"""Команды: python -m plan24.telegram <check | setup-webhook URL | delete-webhook | notify | scheduler | poll>"""
from __future__ import annotations

import logging
import ssl
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .. import config, db as dbm, snapshots
from . import bot, client, notifications

log = logging.getLogger("plan24.telegram")


def _session():
    return dbm.job_session()          # движок создаётся один раз при первом обращении, а не на каждое сообщение


def route_text(cfg) -> str:
    """Каким путём бот ходит в Telegram — без токена и паролей прокси."""
    host = urlsplit(cfg["api_base"]).netloc or cfg["api_base"]
    parts = ["напрямую" if host == "api.telegram.org" and not cfg["proxy"] else f"релей {host}"]
    if cfg["proxy"]:
        parts.append("прокси " + urlsplit(cfg["proxy"]).hostname)
    if cfg["ca_pem"]:
        parts.append("сертификат релея закреплён")
    return ", ".join(parts)


def explain(e: Exception) -> str:
    """Понятная причина сбоя связи с Telegram (для логов платформы без консоли)."""
    name = type(e).__name__
    if isinstance(e, ssl.SSLError) or "CERTIFICATE_VERIFY" in str(e) or "SSL" in name:
        return f"{name}: сертификат релея не принят — проверьте PLAN24_TG_CA_PEM (должен быть сертификат именно этого релея)"
    if "Timeout" in name:
        return f"{name}: релей/Telegram не отвечает — закрыт порт релея (файрвол хостера или ufw) либо фильтрация"
    if "Connect" in name:
        return f"{name}: не удалось соединиться — проверьте PLAN24_TG_API_BASE (адрес и порт релея) и файрвол релея"
    if isinstance(e, RuntimeError) and "401" in str(e):
        return "401: Telegram не принял токен — проверьте PLAN24_TG_TOKEN"
    if isinstance(e, RuntimeError) and any(f": {c} " in str(e) for c in (502, 503, 504)):
        return f"{e} — релей работает, но не достаёт до Telegram: на сервере релея выполните relay-check.sh"
    if isinstance(e, RuntimeError) and "404" in str(e):
        return "404: релей или Telegram не знает этот токен — токен в PLAN24_TG_TOKEN должен совпадать с токеном на релее"
    return f"{name}: {e}"


def probe(c, cfg, with_webhook: bool = False) -> bool:
    """Проверка связи: getMe через настроенный путь. Печатает результат (виден в логах контейнера). True — всё хорошо."""
    print("Telegram: путь —", route_text(cfg), flush=True)
    t0 = time.monotonic()
    try:
        me = c.get_me()
    except Exception as e:                                          # noqa: BLE001
        print("✘ Telegram недоступен:", explain(e), flush=True)
        return False
    print(f"✔ Telegram доступен ({(time.monotonic() - t0) * 1000:.0f} мс), бот @{me.get('username')}", flush=True)
    if cfg["bot_username"] and cfg["bot_username"].lstrip("@") != me.get("username"):
        print(f"! PLAN24_TG_BOT_USERNAME={cfg['bot_username']}, а токен принадлежит @{me.get('username')}", flush=True)
    if with_webhook:
        try:
            wh = c.get_webhook_info()
            print("  вебхук:", wh.get("url") and "установлен (" + str(wh.get("pending_update_count", 0)) + " в очереди)" or "не установлен",
                  "| последняя ошибка:", wh.get("last_error_message") or "нет", flush=True)
        except Exception as e:                                      # noqa: BLE001
            print("  вебхук: не удалось получить —", explain(e), flush=True)
    return True


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    cfg = config.telegram_settings()
    if not cfg["enabled"]:
        print("Бот не настроен: задайте PLAN24_TG_TOKEN и PLAN24_TG_SECRET"); return 2
    c = client.get_client()
    if cmd == "check":
        return 0 if probe(c, cfg, with_webhook=True) else 1
    if cmd == "setup-webhook":
        base = (argv[2] if len(argv) > 2 else config.base_url()).rstrip("/")
        url = f"{base}/api/v1/integrations/telegram/webhook/{cfg['secret']}"
        c.set_webhook(url, cfg["header_secret"])
        print("Вебхук установлен:", base + "/api/v1/integrations/telegram/webhook/***")
    elif cmd == "delete-webhook":
        c.delete_webhook(); print("Вебхук удалён")
    elif cmd == "notify":
        s = _session(); out = notifications.run_due(s, datetime.now(timezone.utc)); snapshots.sync_all(s, datetime.now(timezone.utc)); s.commit(); client.deliver(out)
        print("Сообщений отправлено:", len(out))
    elif cmd == "scheduler":
        probe(c, cfg)
        while True:
            s = _session(); out = notifications.run_due(s, datetime.now(timezone.utc)); snapshots.sync_all(s, datetime.now(timezone.utc)); s.commit(); s.close()
            client.deliver(out); time.sleep(60)
    elif cmd == "poll":          # без публичного HTTPS: бот опрашивает Telegram сам (вебхук при этом должен быть удалён)
        offset, delay = 0, 1
        probe(c, cfg)                                                  # итог проверки связи — в лог при каждом старте
        try:
            c.delete_webhook()           # при установленном вебхуке getUpdates не работает
        except Exception as e:           # noqa: BLE001
            log.error("deleteWebhook failed: %s", type(e).__name__)
        while True:
            try:
                updates = c.get_updates(offset)
                delay = 1
            except Exception as e:                                  # noqa: BLE001 — сеть до Telegram нестабильна: ждём и пробуем снова
                log.error("getUpdates failed: %s", type(e).__name__)
                time.sleep(delay); delay = min(delay * 2, 60)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                s = _session(); out: list = []
                try:
                    bot.handle_update(s, u, out); s.commit()
                except Exception as e:                              # noqa: BLE001 — один плохой апдейт не останавливает бота
                    s.rollback(); log.error("update failed: %s", type(e).__name__)
                finally:
                    s.close()
                client.deliver(out)
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
