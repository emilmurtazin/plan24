"""Защита на уровне строк (RLS) и права роли приложения.

Revision ID: 0002
Revises: 0001

Модель доступа (только PostgreSQL; в SQLite миграция ничего не делает):
  * app_tenant()      — организация текущей транзакции (приложение выставляет app.tenant_id после проверки токена);
  * app_auth_scope()  — режим «поиск учётной записи до входа»: ТОЛЬКО ЧТЕНИЕ users, токенов, привязок Telegram;
  * строки таблиц организации видны и изменяемы только при tenant_id = app_tenant(); без настройки — ни одной строки;
  * audit_log: роли приложения доступны только SELECT и INSERT (журнал неизменяем);
  * владелец схемы (миграции) RLS не подчиняется, роль приложения подчиняется. Подключение приложения под
    суперпользователем/владельцем/BYPASSRLS блокируется при старте (db.assert_restricted_role).
"""
import os

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# таблицы с колонкой tenant_id
TENANT_TABLES = [
    "users", "tenant_modules", "work_centers", "workers", "worker_absences", "holidays", "planner_settings",
    "fact_reasons", "products", "routings", "operations", "projects", "order_lines", "worker_order_lines", "facts",
    "idempotency_keys", "plans", "plan_items", "telegram_links", "telegram_codes", "telegram_state", "notification_log",
]
# таблицы без tenant_id, привязанные к пользователю
USER_TABLES = ["refresh_tokens", "email_tokens"]
# читаются до входа (поиск учётной записи): только SELECT
AUTH_READ_TABLES = ["users", "refresh_tokens", "email_tokens", "telegram_links", "telegram_codes"]
# сознательно без RLS: нет данных организаций
NO_RLS = ["telegram_updates", "rate_events", "alembic_version"]

T = "(SELECT app_tenant())"


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_pg():
        return
    app_role = os.environ.get("PLAN24_APP_ROLE", "plan24_app")
    admin_role = os.environ.get("PLAN24_ADMIN_ROLE", "plan24_admin")
    bind = op.get_bind()
    if not bind.exec_driver_sql("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_role,)).scalar():
        raise RuntimeError(f"Роль приложения {app_role!r} не создана. Выполните deploy/postgres/init-roles.sql")

    op.execute("""CREATE FUNCTION app_tenant() RETURNS integer LANGUAGE sql STABLE AS
                  $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::integer $$""")
    op.execute("""CREATE FUNCTION app_auth_scope() RETURNS boolean LANGUAGE sql STABLE AS
                  $$ SELECT COALESCE(current_setting('app.auth_scope', true), '') = 'on' $$""")

    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING (tenant_id = {T}) WITH CHECK (tenant_id = {T})")
    # организация видит только саму себя; создаётся с заранее выделенным id (см. регистрацию)
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_self ON tenants USING (id = {T}) WITH CHECK (id = {T})")
    for t in USER_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        cond = f"EXISTS (SELECT 1 FROM users u WHERE u.id = {t}.user_id AND u.tenant_id = {T})"
        op.execute(f"CREATE POLICY via_user ON {t} USING ({cond}) WITH CHECK ({cond})")
    for t in AUTH_READ_TABLES:
        op.execute(f"CREATE POLICY auth_read ON {t} FOR SELECT USING ((SELECT app_auth_scope()))")

    # журнал аудита: только добавление и чтение (политика ALL заменена двумя)
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY audit_read ON audit_log FOR SELECT USING (tenant_id = {T})")
    op.execute(f"CREATE POLICY audit_insert ON audit_log FOR INSERT WITH CHECK (tenant_id = {T})")

    # права роли приложения
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_role}")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM {app_role}")
    op.execute(f"REVOKE DELETE ON tenants FROM {app_role}")
    op.execute(f"REVOKE ALL ON alembic_version FROM {app_role}")
    op.execute(f"GRANT SELECT ON alembic_version TO {app_role}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}")
    # будущие таблицы получают обычные права автоматически; RLS для них обязан включить автор миграции
    # (это проверяет тест test_every_table_declares_rls_posture)
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_role}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {app_role}")
    if bind.exec_driver_sql("SELECT 1 FROM pg_roles WHERE rolname = %s", (admin_role,)).scalar():
        op.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {admin_role}")
        op.execute(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {admin_role}")
        op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {admin_role}")
        op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {admin_role}")


def downgrade() -> None:
    if not _is_pg():
        return
    for t in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    for t in AUTH_READ_TABLES:
        op.execute(f"DROP POLICY IF EXISTS auth_read ON {t}")
    for t in USER_TABLES:
        op.execute(f"DROP POLICY IF EXISTS via_user ON {t}")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_self ON tenants")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS audit_read ON audit_log")
    op.execute("DROP POLICY IF EXISTS audit_insert ON audit_log")
    op.execute("ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS app_auth_scope()")
    op.execute("DROP FUNCTION IF EXISTS app_tenant()")
