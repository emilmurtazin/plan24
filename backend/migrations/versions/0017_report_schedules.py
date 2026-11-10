"""Регулярные отчёты в группы Telegram (report_schedules).

Администратор организации настраивает: группу Telegram, время публикации (по Москве), дни недели, набор метрик,
участки (пусто — вся организация). Отчётов может быть несколько.

Revision ID: 0017
Revises: 0016
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("chat_ref", sa.String(64), nullable=False),
        sa.Column("send_time", sa.String(5), nullable=False),
        sa.Column("weekdays", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("section_ids", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(8), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.UniqueConstraint("tenant_id", "name"),
    )
    op.create_index(op.f("ix_report_schedules_tenant_id"), "report_schedules", ["tenant_id"])
    if op.get_bind().dialect.name == "postgresql":
        app_role = os.environ.get("PLAN24_APP_ROLE", "plan24_app")
        op.execute("ALTER TABLE report_schedules ENABLE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON report_schedules "
                   "USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()))")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON report_schedules TO {app_role}")


def downgrade() -> None:
    op.drop_index(op.f("ix_report_schedules_tenant_id"), table_name="report_schedules")
    op.drop_table("report_schedules")
