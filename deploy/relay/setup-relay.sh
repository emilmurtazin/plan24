#!/usr/bin/env bash
# Установка релея Telegram на сервер за пределами РФ (Ubuntu/Debian, запуск от root).
#
#   BOT_TOKEN='123456:ABC…' bash setup-relay.sh
#
# Релей — отдельная служба plan24-relay: свой экземпляр nginx со своим конфигом, портом и пользователем.
# Другие nginx/Caddy/сервисы на сервере, их конфигурация, порты 80/443 и вебхуки НЕ затрагиваются и не перезагружаются.
# Сначала можно посмотреть, что и как встанет, ничего не меняя:  bash preflight-relay.sh
#
# Необязательные переменные:
#   LISTEN_PORT=9443                            порт релея (по умолчанию 9443; убедитесь, что он свободен — проверит preflight)
#   RELAY_IPS="72.56.68.43 2a03:6f02::1:2496"   адреса сервера для сертификата (по умолчанию — определяются автоматически)
#   ALLOW_FROM="203.0.113.5 198.51.100.0/24"    пускать только с этих адресов (адрес основного сервера; если он не постоянный — не задавайте)
#   CERT_DAYS=3650                              срок действия сертификата
# Служебные (проверка скрипта без реального Telegram): UPSTREAM, SKIP_PACKAGES=1, SKIP_FIREWALL=1, SKIP_SYSTEMD=1.
set -euo pipefail

: "${BOT_TOKEN:?Задайте токен бота: BOT_TOKEN='123456:ABC…' bash setup-relay.sh}"
[[ "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || { echo "Токен не похож на токен бота (формат 123456:ABC-def…)"; exit 1; }
[[ $EUID -eq 0 ]] || { echo "Запустите от root (sudo -i)"; exit 1; }

LISTEN_PORT="${LISTEN_PORT:-9443}"
CERT_DAYS="${CERT_DAYS:-3650}"
UPSTREAM="${UPSTREAM:-https://api.telegram.org}"
UPSTREAM_HOST="${UPSTREAM#*://}"; UPSTREAM_HOST="${UPSTREAM_HOST%%/*}"
DIR=/etc/plan24-relay
USER_NAME=plan24relay
UNIT=/etc/systemd/system/plan24-relay.service
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
use_systemd=1; [[ "${SKIP_SYSTEMD:-0}" == 1 || ! -d /run/systemd/system ]] && use_systemd=0

# ---- 0. порт релея должен быть свободен (если релей уже стоит и слушает этот порт — это обновление, можно)
if ss -ltnH "sport = :$LISTEN_PORT" 2>/dev/null | grep -q . && ! ss -ltnpH "sport = :$LISTEN_PORT" 2>/dev/null | grep -q "plan24-relay\|nginx.*plan24"; then
  if ! { [[ -f "$DIR/relay.env" ]] && grep -q "^LISTEN_PORT=$LISTEN_PORT$" "$DIR/relay.env"; }; then
    echo "Порт $LISTEN_PORT уже занят другим процессом — релей рядом с ним не ставлю. Задайте другой: LISTEN_PORT=9444"; exit 1
  fi
fi

# ---- 1. nginx: ставим пакет ТОЛЬКО если бинарника нет; стандартная служба nginx не запускается и не включается
NGINX_BIN="$(command -v nginx || true)"
if [[ -z "$NGINX_BIN" ]]; then
  [[ "${SKIP_PACKAGES:-0}" != 1 ]] || { echo "nginx не найден"; exit 1; }
  export DEBIAN_FRONTEND=noninteractive
  printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d; chmod +x /usr/sbin/policy-rc.d      # запрет автозапуска служб при установке пакета
  trap 'rm -f /usr/sbin/policy-rc.d' EXIT
  apt-get update -qq
  apt-get install -y -qq nginx openssl curl >/dev/null
  rm -f /usr/sbin/policy-rc.d; trap - EXIT
  systemctl disable nginx >/dev/null 2>&1 || true       # пакет поставили мы, стандартный сайт на :80 не нужен
  NGINX_BIN="$(command -v nginx)"
  echo "Пакет nginx установлен (стандартная служба nginx отключена; работает только plan24-relay)"
else
  echo "Найден nginx ($NGINX_BIN) — пакеты не устанавливаются, существующий nginx не трогаю"
fi
command -v openssl >/dev/null || { echo "Нужен openssl: apt-get install openssl"; exit 1; }

# ---- 2. адреса сервера для сертификата
if [[ -z "${RELAY_IPS:-}" ]]; then
  RELAY_IPS="$(curl -4 -fsS -m 8 https://api.ipify.org 2>/dev/null || true) $(curl -6 -fsS -m 8 https://api64.ipify.org 2>/dev/null | grep ':' || true)"
fi
RELAY_IPS="$(echo "$RELAY_IPS" | xargs)"
[[ -n "$RELAY_IPS" ]] || { echo "Не удалось определить адрес сервера — задайте RELAY_IPS=\"72.56.68.43\""; exit 1; }
echo "Адреса в сертификате: $RELAY_IPS"

# ---- 3. отдельный пользователь и каталоги
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$DIR" /var/log/plan24-relay /var/lib/plan24-relay /run/plan24-relay
chown root:"$USER_NAME" "$DIR"; chmod 750 "$DIR"
chown "$USER_NAME":"$USER_NAME" /var/log/plan24-relay /var/lib/plan24-relay /run/plan24-relay

# ---- 4. самоподписанный сертификат с адресами в SAN (создаётся один раз; чтобы выпустить заново, удалите $DIR/relay.crt)
if [[ ! -s "$DIR/relay.crt" ]]; then
  SAN="$(for ip in $RELAY_IPS; do printf 'IP:%s,' "$ip"; done)"; SAN="${SAN%,}"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days "$CERT_DAYS" \
    -keyout "$DIR/relay.key" -out "$DIR/relay.crt" -subj "/CN=plan24-telegram-relay" \
    -addext "subjectAltName=$SAN" -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,digitalSignature,keyCertSign" -addext "extendedKeyUsage=serverAuth" 2>/dev/null
  echo "Сертификат создан (действует $CERT_DAYS дн.)"
else
  echo "Используется существующий сертификат $DIR/relay.crt"
fi
chown root:"$USER_NAME" "$DIR/relay.key" "$DIR/relay.crt"; chmod 640 "$DIR/relay.key"; chmod 644 "$DIR/relay.crt"

# ---- 5. собственный конфиг nginx
ALLOW=""
if [[ -n "${ALLOW_FROM:-}" ]]; then
  for a in $ALLOW_FROM; do ALLOW+="allow $a;"$'\n        '; done
  ALLOW+="deny all;"
fi
LISTEN6=""     # IPv6 слушаем, только если он есть на сервере
[[ -s /proc/net/if_inet6 ]] && LISTEN6="listen [::]:$LISTEN_PORT ssl ipv6only=on;"
CONF="$DIR/nginx.conf"
TOKEN_RE="$BOT_TOKEN" python3 - "$HERE/nginx-relay.conf.template" "$CONF" "$LISTEN_PORT" "$DIR/relay.crt" "$DIR/relay.key" "$UPSTREAM" "$UPSTREAM_HOST" "$ALLOW" "$LISTEN6" <<'PY'
import os, sys
tpl, out, port, crt, key, up, uph, allow, listen6 = sys.argv[1:10]
s = open(tpl, encoding="utf8").read()
for k, v in {"@PORT@": port, "@CERT@": crt, "@KEY@": key, "@UPSTREAM@": up, "@UPSTREAM_HOST@": uph,
             "@ALLOW@": allow, "@LISTEN6@": listen6, "@TOKEN@": os.environ["TOKEN_RE"]}.items():
    s = s.replace(k, v)
open(out, "w", encoding="utf8").write(s)
PY
chown root:"$USER_NAME" "$CONF"; chmod 640 "$CONF"            # в файле токен бота
printf 'LISTEN_PORT=%s\nRELAY_IPS="%s"\n' "$LISTEN_PORT" "$RELAY_IPS" > "$DIR/relay.env"
NGX=("$NGINX_BIN" -e /var/log/plan24-relay/error.log -c "$CONF")
if ! runuser -u "$USER_NAME" -- "${NGX[@]}" -t 2>/tmp/plan24-relay-test.log; then
  cat /tmp/plan24-relay-test.log; echo "nginx отклонил конфигурацию релея (ваши другие сервисы не затронуты — их конфигурацию скрипт не менял)."; exit 1
fi

# ---- 6. служба plan24-relay
if [[ $use_systemd -eq 1 ]]; then
  cat > "$UNIT" <<UNITEOF
[Unit]
Description=Plan 24 Telegram relay (изолированный nginx)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
Group=$USER_NAME
RuntimeDirectory=plan24-relay
ExecStart=$NGINX_BIN -e /var/log/plan24-relay/error.log -c $CONF -g 'daemon off;'
ExecReload=/bin/kill -HUP \$MAINPID
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=yes
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNITEOF
  systemctl daemon-reload
  systemctl enable plan24-relay >/dev/null 2>&1
  systemctl restart plan24-relay
  sleep 1
  systemctl is-active --quiet plan24-relay || { journalctl -u plan24-relay -n 20 --no-pager; echo "Служба не запустилась (см. выше). Другие сервисы не затронуты."; exit 1; }
  echo "Служба plan24-relay запущена"
else
  [[ -s /run/plan24-relay/nginx.pid ]] && kill "$(cat /run/plan24-relay/nginx.pid)" 2>/dev/null && sleep 1 || true
  runuser -u "$USER_NAME" -- "${NGX[@]}"
  echo "(без systemd) nginx релея запущен напрямую"
fi

# ---- 7. файрвол: одно правило на порт релея (если ufw включён)
if [[ "${SKIP_FIREWALL:-0}" != 1 ]] && command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  if [[ -n "${ALLOW_FROM:-}" ]]; then for a in $ALLOW_FROM; do ufw allow from "$a" to any port "$LISTEN_PORT" proto tcp >/dev/null; done
  else ufw allow "$LISTEN_PORT/tcp" >/dev/null; fi
  echo "ufw: открыт порт $LISTEN_PORT"
fi

# ---- 8. что вписать в настройки основного сервера
FIRST_IP="$(echo "$RELAY_IPS" | awk '{print $1}')"
[[ "$FIRST_IP" == *:* ]] && FIRST_IP="[$FIRST_IP]"
CA_B64="$(base64 -w0 "$DIR/relay.crt")"
cat <<OUT

════════════ ГОТОВО ════════════
Проверьте на этом сервере:   BOT_TOKEN='…' bash $HERE/relay-check.sh
Если у хостера есть внешний файрвол — откройте в панели TCP-порт $LISTEN_PORT.

Переменные для основного сервера (сервисы tg и scheduler, а при режиме webhook — ещё и web):

PLAN24_TG_API_BASE=https://$FIRST_IP:$LISTEN_PORT
PLAN24_TG_CA_PEM=$CA_B64

(PLAN24_TG_CA_PEM — сертификат релея в base64 одной строкой; он публичный, его можно хранить в переменных окружения.)
Остальное (PLAN24_TG_TOKEN, PLAN24_TG_SECRET, PLAN24_TG_BOT_USERNAME, PLAN24_TG_MODE=poll) — см. README.md рядом со скриптом.
Полностью убрать релей: bash $HERE/uninstall-relay.sh
OUT
