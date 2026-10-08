-- Инициализация для УПРАВЛЯЕМОЙ БД (Timeweb Cloud и аналоги): суперпользователя нет.
-- Выполняется владельцем БД (в Timeweb — gen_user) в базе проекта:
--   psql "postgresql://gen_user:...@host/db?sslmode=require" -v ON_ERROR_STOP=1 -v app_pw='ПАРОЛЬ' -f init-roles-managed.sql
-- (необязательно: -v app_role=имя_роли, по умолчанию plan24_app; тогда при миграциях задайте PLAN24_APP_ROLE)
--
-- Модель защиты та же, что в init-roles.sql, но проще:
--   владелец БД (gen_user)  — выполняет миграции и владеет таблицами; RLS на владельца не действует (так и задумано);
--   plan24_app              — роль приложения: не владелец, NOBYPASSRLS → подчиняется RLS.
-- Приложение откажется стартовать, если его подключили под владельцем или суперпользователем.
-- Роли plan24_admin нет: BYPASSRLS без суперпользователя создать нельзя, а владелец БД и так видит все данные.
\if :{?app_role}
\else
  \set app_role plan24_app
\endif
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB PASSWORD %L', :'app_role', :'app_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role') \gexec
-- если роль уже была — обновить пароль (повторный запуск безопасен)
SELECT format('ALTER ROLE %I PASSWORD %L', :'app_role', :'app_pw')
 WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_role') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_role') \gexec
-- таймауты роли приложения; если провайдер не разрешает менять параметры роли — не критично (таймауты есть и на стороне приложения)
\set ON_ERROR_STOP off
SELECT format('ALTER ROLE %I SET statement_timeout = ''30s''', :'app_role') \gexec
SELECT format('ALTER ROLE %I SET lock_timeout = ''15s''', :'app_role') \gexec
SELECT format('ALTER ROLE %I SET idle_in_transaction_session_timeout = ''60s''', :'app_role') \gexec
\set ON_ERROR_STOP on
