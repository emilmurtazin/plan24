#!/bin/sh
# Команды контейнера: migrate | web | scheduler | tg | tg-poll | tg-webhook <https://адрес>
set -e
case "$1" in
  migrate)      # выполняется владельцем схемы (PLAN24_DB_MIGRATE_URL), НЕ ролью приложения
    exec python -m alembic -c alembic.ini upgrade head ;;
  web)
    exec python -m uvicorn plan24.app:app --host 0.0.0.0 --port 8000 --workers "${PLAN24_WORKERS:-2}" \
         --proxy-headers --forwarded-allow-ips="${PLAN24_FORWARDED_ALLOW_IPS:-127.0.0.1}" --no-server-header ;;
  scheduler)    # утренние задания и вечерние напоминания (запускать в одном экземпляре; дубли в любом случае исключены БД)
    exec python -m plan24.telegram scheduler ;;
  tg)           # бот по переменной PLAN24_TG_MODE: off (по умолчанию) | poll | webhook. Не завершается (платформа без профилей compose)
    case "${PLAN24_TG_MODE:-off}" in
      poll)    exec python -m plan24.telegram poll ;;
      webhook) python -m plan24.telegram setup-webhook "${PLAN24_BASE_URL:?}" || echo "setWebhook не удался — проверьте доступ к Telegram"
               exec sleep infinity ;;
      *)       echo "Telegram-бот выключен (PLAN24_TG_MODE=off)"; exec sleep infinity ;;
    esac ;;
  tg-poll)      # бот без публичного HTTPS (опрос Telegram)
    exec python -m plan24.telegram poll ;;
  tg-webhook)
    exec python -m plan24.telegram setup-webhook "$2" ;;
  *) echo "Неизвестная команда: $1 (migrate|web|scheduler|tg|tg-poll|tg-webhook)"; exit 2 ;;
esac
