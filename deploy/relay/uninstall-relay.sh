#!/usr/bin/env bash
# Полное удаление релея. Другие службы, nginx/Caddy и их настройки не затрагиваются; пакет nginx не удаляется.
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "Запустите от root"; exit 1; }
[[ -f /etc/plan24-relay/relay.env ]] && . /etc/plan24-relay/relay.env
if [[ -d /run/systemd/system ]]; then
  systemctl disable --now plan24-relay 2>/dev/null; rm -f /etc/systemd/system/plan24-relay.service; systemctl daemon-reload
elif [[ -s /run/plan24-relay/nginx.pid ]]; then kill "$(cat /run/plan24-relay/nginx.pid)" 2>/dev/null; fi
if [[ -n "${LISTEN_PORT:-}" ]] && command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw delete allow "$LISTEN_PORT/tcp" >/dev/null 2>&1 || true
fi
rm -rf /etc/plan24-relay /var/lib/plan24-relay /run/plan24-relay /var/log/plan24-relay
id plan24relay >/dev/null 2>&1 && userdel plan24relay 2>/dev/null
echo "Релей удалён."
