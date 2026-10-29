"""Панель суперадмина: блокировка организаций и режим платформы в RLS.

- tenants.blocked_at / blocked_reason: организация заблокирована суперадмином;
- PostgreSQL: функция app_platform_scope() и политики для режима суперадмина (приложение включает его только после проверки
  токена суперадмина). Режим ЧИТАЕТ организации и сводные данные (tenants, users, workers, order_lines, plans, audit_log,
  telegram_links) и точечно ПИШЕТ: tenants (блокировка), users (отключение), refresh_tokens и email_tokens (закрыть сессии,
  выдать ссылку сброса пароля), audit_log (только добавление записи о действии суперадмина).

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

READ = ["tenants", "users", "workers", "order_lines", "plans", "audit_log", "telegram_links"]
WRITE = ["tenants", "users", "refresh_tokens", "email_tokens"]


def upgrade() -> None:
    with op.batch_alter_table("tenants") as b:
        b.add_column(sa.Column("blocked_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("blocked_reason", sa.String(300), nullable=True))
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""CREATE FUNCTION app_platform_scope() RETURNS boolean LANGUAGE sql STABLE AS
                  $$ SELECT COALESCE(current_setting('app.platform_scope', true), '') = 'on' $$""")
    for t in READ:
        op.execute(f"CREATE POLICY platform_read ON {t} FOR SELECT USING ((SELECT app_platform_scope()))")
    for t in WRITE:
        op.execute(f"CREATE POLICY platform_write ON {t} FOR UPDATE USING ((SELECT app_platform_scope())) "
                   f"WITH CHECK ((SELECT app_platform_scope()))")
    op.execute("CREATE POLICY platform_insert ON email_tokens FOR INSERT WITH CHECK ((SELECT app_platform_scope()))")
    op.execute("CREATE POLICY platform_read ON email_tokens FOR SELECT USING ((SELECT app_platform_scope()))")
    op.execute("CREATE POLICY platform_read ON refresh_tokens FOR SELECT USING ((SELECT app_platform_scope()))")
    op.execute("CREATE POLICY platform_insert ON audit_log FOR INSERT WITH CHECK ((SELECT app_platform_scope()))")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for t, p in [("audit_log", "platform_insert"), ("refresh_tokens", "platform_read"), ("email_tokens", "platform_read"),
                     ("email_tokens", "platform_insert")] + [(t, "platform_write") for t in WRITE] + [(t, "platform_read") for t in READ]:
            op.execute(f"DROP POLICY IF EXISTS {p} ON {t}")
        op.execute("DROP FUNCTION IF EXISTS app_platform_scope()")
    with op.batch_alter_table("tenants") as b:
        b.drop_column("blocked_reason")
        b.drop_column("blocked_at")
