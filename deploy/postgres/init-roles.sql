-- Первичная настройка PostgreSQL для Plan 24. Выполняется ОДИН раз суперпользователем:
--   psql -v owner_pw="'...'" -v app_pw="'...'" -v admin_pw="'...'" -f init-roles.sql
-- Три роли с разными правами (принцип наименьших привилегий):
--   plan24_owner — владелец схемы; только им выполняются миграции (alembic). Приложение под ним НЕ работает.
--   plan24_app   — под ней работает приложение. Не владелец, не суперпользователь, подчиняется RLS (NOBYPASSRLS).
--   plan24_admin — аварийный доступ для администратора БД (обходит RLS). В приложении и скриптах не используется.
SELECT 'CREATE ROLE plan24_owner LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB PASSWORD ' || quote_literal(:owner_pw)
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'plan24_owner') \gexec
SELECT 'CREATE ROLE plan24_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB PASSWORD ' || quote_literal(:app_pw)
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'plan24_app') \gexec
SELECT 'CREATE ROLE plan24_admin LOGIN NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB PASSWORD ' || quote_literal(:admin_pw)
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'plan24_admin') \gexec

SELECT 'CREATE DATABASE plan24 OWNER plan24_owner ENCODING ''UTF8'' TEMPLATE template0'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'plan24') \gexec

\connect plan24
-- схема public принадлежит владельцу; остальным по умолчанию ничего не доступно
ALTER SCHEMA public OWNER TO plan24_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO plan24_app, plan24_admin;
ALTER ROLE plan24_app SET statement_timeout = '30s';
ALTER ROLE plan24_app SET lock_timeout = '15s';
ALTER ROLE plan24_app SET idle_in_transaction_session_timeout = '60s';
