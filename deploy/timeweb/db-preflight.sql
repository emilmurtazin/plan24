-- Проверка управляемой БД ПЕРЕД установкой. Запускается пользователем кластера (в Timeweb — gen_user):
--   psql "postgresql://gen_user:ПАРОЛЬ@ХОСТ:5432/ИМЯ_БД?sslmode=require" -f db-preflight.sql
-- Каждая строка: проверка | значение | вердикт. Любое «НЕТ» нужно устранить до установки (см. deploy/timeweb/README.md).
\pset format aligned
\pset border 2
SELECT 'версия PostgreSQL' AS проверка, current_setting('server_version') AS значение,
       CASE WHEN current_setting('server_version_num')::int >= 140000 THEN 'ОК' ELSE 'НЕТ: нужна 14+ (проверялось на 16)' END AS вердикт
UNION ALL SELECT 'пользователь', current_user, 'информация'
UNION ALL SELECT 'суперпользователь', rolsuper::text, 'информация (в управляемых БД обычно false)' FROM pg_roles WHERE rolname = current_user
UNION ALL SELECT 'может создавать роли', (rolcreaterole OR rolsuper)::text,
       CASE WHEN rolcreaterole OR rolsuper THEN 'ОК' ELSE 'НЕТ: выдайте привилегию CREATE_ROLE (на минимальном тарифе пользователей создавать нельзя)' END
       FROM pg_roles WHERE rolname = current_user
UNION ALL SELECT 'владелец БД', pg_get_userbyid(datdba),
       CASE WHEN pg_get_userbyid(datdba) = current_user THEN 'ОК' ELSE 'ВНИМАНИЕ: миграции нужно выполнять от владельца БД' END
       FROM pg_database WHERE datname = current_database()
UNION ALL SELECT 'может создавать таблицы в public', has_schema_privilege(current_user, 'public', 'CREATE')::text,
       CASE WHEN has_schema_privilege(current_user, 'public', 'CREATE') THEN 'ОК' ELSE 'НЕТ: нет права CREATE в схеме public' END
UNION ALL SELECT 'соединение по TLS', COALESCE((SELECT ssl::text FROM pg_stat_ssl WHERE pid = pg_backend_pid()), 'false'),
       CASE WHEN COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()), false) THEN 'ОК' ELSE 'ВНИМАНИЕ: подключайтесь по домену с TLS или по приватному IP' END
UNION ALL SELECT 'max_connections / занято', current_setting('max_connections') || ' / ' || (SELECT count(*) FROM pg_stat_activity)::text,
       'формула: воркеры × (PLAN24_DB_POOL + PLAN24_DB_OVERFLOW) должна быть заметно меньше'
UNION ALL SELECT 'уже есть роли plan24_*', COALESCE((SELECT string_agg(rolname, ', ') FROM pg_roles WHERE rolname LIKE 'plan24\_%'), 'нет'), 'информация'
UNION ALL SELECT 'таблиц в public', (SELECT count(*) FROM pg_tables WHERE schemaname = 'public')::text,
       CASE WHEN (SELECT count(*) FROM pg_tables WHERE schemaname = 'public') = 0 THEN 'ОК (чистая БД)' ELSE 'ВНИМАНИЕ: БД не пустая' END;
