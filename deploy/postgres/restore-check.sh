#!/bin/sh
# Учебное восстановление: проверяет, что копия разворачивается и защита на уровне строк сохранилась.
# Использование: PGHOST=... PGUSER=<владелец/суперпользователь> PGPASSWORD=... [OWNER_ROLE=gen_user] [APP_ROLE=plan24_app] ./restore-check.sh файл.dump
# В управляемой БД (Timeweb): PGUSER=OWNER_ROLE=gen_user; требуется привилегия CREATEDB.
set -e
DUMP="${1:?файл копии}"; DB=plan24_restore_check; OWNER="${OWNER_ROLE:-plan24_owner}"; APP="${APP_ROLE:-plan24_app}"
psql -d postgres -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)" -c "CREATE DATABASE $DB OWNER $OWNER TEMPLATE template0"
pg_restore --no-owner --role=$OWNER -d "$DB" "$DUMP" 2>/tmp/restore.err || { echo "pg_restore: предупреждения:"; head -5 /tmp/restore.err; }
echo "таблиц: $(psql -d "$DB" -tAc "select count(*) from pg_tables where schemaname='public'")"
echo "с включённой RLS: $(psql -d "$DB" -tAc "select count(*) from pg_class where relrowsecurity")"
echo "политик: $(psql -d "$DB" -tAc "select count(*) from pg_policies")"
echo "ревизия миграций: $(psql -d "$DB" -tAc "select version_num from alembic_version")"
echo "права роли приложения на audit_log (ожидается: select,insert без update/delete): $(psql -d "$DB" -tAc "select string_agg(privilege_type, ',' order by privilege_type) from information_schema.role_table_grants where grantee='$APP' and table_name='audit_log'")"
psql -d postgres -qc "DROP DATABASE $DB WITH (FORCE)"
