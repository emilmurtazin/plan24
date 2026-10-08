#!/bin/sh
# Выполняется образом postgres при первом создании кластера (каталог /docker-entrypoint-initdb.d).
# Берёт пароли ролей из переменных окружения и запускает init-roles.sql.
set -e
: "${PLAN24_OWNER_PASSWORD:?}" "${PLAN24_APP_PASSWORD:?}" "${PLAN24_ADMIN_PASSWORD:?}"
psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d postgres \
     -v owner_pw="'${PLAN24_OWNER_PASSWORD}'" -v app_pw="'${PLAN24_APP_PASSWORD}'" -v admin_pw="'${PLAN24_ADMIN_PASSWORD}'" \
     -f /plan24/init-roles.sql
