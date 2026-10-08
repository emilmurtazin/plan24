#!/bin/sh
# Сквозная проверка интерфейса: поднимает сервер на чистой БД и проходит сценарий в jsdom.
set -e
cd "$(dirname "$0")"
[ -d node_modules ] || npm install --silent
D=$(mktemp -d)
( cd ../backend && PLAN24_ENV=dev PLAN24_TODAY=2026-10-02 PLAN24_TG_TOKEN=1:TEST PLAN24_TG_SECRET=ui-secret PLAN24_TG_BOT_USERNAME=plan24_ui_bot PLAN24_DATA="$D" exec python3 -m uvicorn plan24.app:app --port 8765 --log-level warning ) &
PID=$!
trap 'kill $PID 2>/dev/null; rm -rf "$D"' EXIT
i=0; until curl -sf http://127.0.0.1:8765/api/v1/health >/dev/null; do i=$((i+1)); [ $i -gt 40 ] && { echo "сервер не поднялся"; exit 1; }; sleep 0.5; done
node smoke.js
