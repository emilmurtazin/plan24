"""Команды: python -m plan24.telegram <setup-webhook URL | delete-webhook | notify | scheduler | poll>"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

from .. import config, db as dbm, snapshots
from . import bot, client, notifications

log = logging.getLogger("plan24.telegram")


def _session():
    return dbm.job_session()          # движок создаётся один раз при первом обращении, а не на каждое сообщение


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    cfg = config.telegram_settings()
    if not cfg["enabled"]:
        print("Бот не настроен: задайте PLAN24_TG_TOKEN и PLAN24_TG_SECRET"); return 2
    c = client.get_client()
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
        while True:
            s = _session(); out = notifications.run_due(s, datetime.now(timezone.utc)); snapshots.sync_all(s, datetime.now(timezone.utc)); s.commit(); s.close()
            client.deliver(out); time.sleep(60)
    elif cmd == "poll":          # без публичного HTTPS: бот опрашивает Telegram сам (вебхук при этом должен быть удалён)
        offset, delay = 0, 1
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
