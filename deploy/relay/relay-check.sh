#!/usr/bin/env bash
# Проверка релея на самом сервере релея: BOT_TOKEN='123456:ABC…' bash relay-check.sh
set -uo pipefail
: "${BOT_TOKEN:?Задайте BOT_TOKEN}"
CRT=/etc/plan24-relay/relay.crt
[[ -f /etc/plan24-relay/relay.env ]] && . /etc/plan24-relay/relay.env
PORT="${LISTEN_PORT:-9443}"
IP="${RELAY_IP:-$(openssl x509 -in "$CRT" -noout -ext subjectAltName 2>/dev/null | grep -o 'IP Address:[^,]*' | head -1 | cut -d: -f2- | tr -d ' ')}"
[[ -n "$IP" ]] || { echo "Нет сертификата $CRT — сначала запустите setup-relay.sh"; exit 1; }
HOSTP="$IP"; [[ "$IP" == *:* ]] && HOSTP="[$IP]"
ok=0
echo "1) Доступен ли Telegram с этого сервера напрямую:"
code=$(curl -m 10 -s -o /dev/null -w '%{http_code}' https://api.telegram.org/bot0/getMe); echo "   HTTP $code (401/404 — достучались; 000 — нет связи)"
[[ "$code" == 401 || "$code" == 404 ]] || ok=1
echo "2) Релей отвечает по TLS с закреплённым сертификатом:"
code=$(curl -m 10 -s --cacert "$CRT" -o /dev/null -w '%{http_code}' "https://$HOSTP:$PORT/healthz"); echo "   HTTP $code (ожидается 200)"
[[ "$code" == 200 ]] || ok=1
echo "3) getMe через релей с вашим токеном:"
out=$(curl -m 15 -s --cacert "$CRT" "https://$HOSTP:$PORT/bot$BOT_TOKEN/getMe"); echo "   ${out:0:200}"
echo "$out" | grep -q '"ok":true' || ok=1
echo "4) Чужой путь закрыт (ожидается 404):"
code=$(curl -m 10 -s --cacert "$CRT" -o /dev/null -w '%{http_code}' "https://$HOSTP:$PORT/bot111:fake/getMe"); echo "   HTTP $code"
[[ "$code" == 404 ]] || ok=1
[[ $ok -eq 0 ]] && echo "ВСЁ В ПОРЯДКЕ" || echo "ЕСТЬ ПРОБЛЕМЫ — см. выше и раздел «Если не работает» в README.md"
exit $ok
