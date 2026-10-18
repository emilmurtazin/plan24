#!/usr/bin/env bash
# ТОЛЬКО ЧТЕНИЕ: показывает, что уже работает на сервере и сможет ли релей встать рядом, ничего не меняя.
#   bash preflight-relay.sh            (порт релея по умолчанию 9443; другой: LISTEN_PORT=…)
set -uo pipefail
PORT="${LISTEN_PORT:-9443}"
echo "== Что слушает порты на этом сервере (TCP) =="
ss -ltnpH 2>/dev/null | awk '{printf "   %-28s %s\n", $4, $6}' | sort -u
echo
echo "== Веб-серверы и контейнеры =="
for s in nginx caddy apache2 httpd haproxy traefik; do systemctl is-active --quiet "$s" 2>/dev/null && echo "   работает: $s"; done
command -v docker >/dev/null && docker ps --format '   docker: {{.Names}}  {{.Ports}}' 2>/dev/null | head -20
echo
echo "== Файрвол =="
if command -v ufw >/dev/null; then ufw status 2>/dev/null | head -3 | sed 's/^/   /'; else echo "   ufw не установлен"; fi
echo
echo "== Что сделает setup-relay.sh =="
echo "   • создаст отдельную службу plan24-relay (свой nginx-процесс, свой конфиг в /etc/plan24-relay, пользователь plan24relay);"
echo "   • займёт ТОЛЬКО порт $PORT; чужие nginx/Caddy/сервисы, их конфигурацию, порты 80/443 и вебхуки не трогает и не перезагружает;"
echo "   • пакет nginx поставит только если его бинарника на сервере нет (и сразу отключит стандартную службу nginx);"
echo "   • откроет порт $PORT в ufw, если ufw включён (одно правило; удаляется uninstall-relay.sh)."
echo
if ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q .; then
  echo "✘ ПОРТ $PORT УЖЕ ЗАНЯТ — выберите другой: LISTEN_PORT=9444 bash preflight-relay.sh"; exit 1
fi
echo "✔ Порт $PORT свободен — установка не должна затронуть существующие сервисы."
echo "  (Если у хостера есть внешний файрвол/«группа безопасности», порт $PORT нужно открыть в панели.)"
