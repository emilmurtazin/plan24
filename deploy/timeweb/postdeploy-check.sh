#!/bin/sh
# Проверка развёрнутого сервиса снаружи (только чтение, данные не создаются):
#   sh postdeploy-check.sh https://plan.example.com
set -u
BASE="${1:?адрес, например https://plan.example.com}"; BASE="${BASE%/}"
FAIL=0
ok()   { echo "  ОК     $1"; }
warn() { echo "  ВНИМАНИЕ $1"; }
bad()  { echo "  ОШИБКА $1"; FAIL=1; }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }
echo "Проверка $BASE"
H=$(curl -s -m 10 -D - -o /dev/null "$BASE/")
check "главная отвечает 200"                         "echo \"\$H\" | head -1 | grep -q ' 200'"
check "HSTS включён"                                  "echo \"\$H\" | grep -qi '^strict-transport-security:'"
check "Content-Security-Policy задан"                 "echo \"\$H\" | grep -qi '^content-security-policy:'"
check "X-Frame-Options: DENY"                         "echo \"\$H\" | grep -qi '^x-frame-options: DENY'"
check "версия сервера не раскрывается"                "! echo \"\$H\" | grep -qi '^server: uvicorn'"
R=$(curl -s -m 10 "$BASE/api/v1/health/ready")
check "готовность: БД доступна, схема на последней ревизии" "echo \"\$R\" | grep -q '\"status\":\"ready\"'"
check "БД — PostgreSQL"                               "echo \"\$R\" | grep -q 'postgresql'"
check "отладочный ящик почты закрыт (/dev/outbox → 404)" "[ \"\$(curl -s -m 10 -o /dev/null -w '%{http_code}' $BASE/api/v1/dev/outbox)\" = 404 ]"
check "описание API не публикуется (404)"             "[ \"\$(curl -s -m 10 -o /dev/null -w '%{http_code}' $BASE/api/openapi.json)\" = 404 ]"
check "защищённые методы требуют вход (401)"          "[ \"\$(curl -s -m 10 -o /dev/null -w '%{http_code}' $BASE/api/v1/projects)\" = 401 ]"
# редирект HTTP→HTTPS зависит от платформы/прокси: это предупреждение, а не ошибка (HSTS защищает повторные заходы)
if curl -s -m 10 -o /dev/null -w '%{redirect_url}' "http://${BASE#https://}/" | grep -q '^https://'; then ok "HTTP перенаправляет на HTTPS"
else warn "HTTP не перенаправляется на HTTPS — пользователи должны открывать https-адрес (письма содержат его); уточните настройку SSL в панели"; fi
echo; [ $FAIL = 0 ] && echo "Все проверки пройдены." || { echo "Есть ошибки — см. deploy/timeweb/README.md, раздел «Диагностика»."; exit 1; }
