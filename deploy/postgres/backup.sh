#!/bin/sh
# Резервная копия (формат custom, сжатая). Запуск по cron от имени роли с чтением всех данных (plan24_admin).
# Использование: PGHOST=... PGUSER=<владелец БД или plan24_admin> PGPASSWORD=... PGDATABASE=... ./backup.sh /путь/к/каталогу
# Копию должна делать роль, обходящая RLS: владелец БД (в Timeweb — gen_user) или plan24_admin. Роль приложения увидит 0 строк.
set -e
DIR="${1:?каталог для копий}"; STAMP=$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$DIR"
pg_dump -Fc --no-owner -d "${PGDATABASE:-plan24}" -f "$DIR/plan24-$STAMP.dump"
echo "$DIR/plan24-$STAMP.dump"
