"""Правка «Плана на день» (этап 4 доработок): закреплённые дни работников, работы вне перечня, временные работники.

Revision ID: 0006
Revises: 0005
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workers") as b:
        b.add_column(sa.Column("auto_plan", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("is_temporary", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "day_pins",
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id"), primary_key=True),
        sa.Column("plan_date", sa.Date(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
    )
    op.create_index(op.f("ix_day_pins_tenant_id"), "day_pins", ["tenant_id"])
    op.create_table(
        "extra_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id"), nullable=False),
        sa.Column("order_line_id", sa.Integer(), sa.ForeignKey("order_lines.id")),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("hours", sa.Float(), nullable=False),
        sa.Column("fact_hours", sa.Float()),
        sa.Column("reason_id", sa.Integer(), sa.ForeignKey("fact_reasons.id")),
        sa.Column("note", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_extra_tasks_tenant_id"), "extra_tasks", ["tenant_id"])
    op.create_index(op.f("ix_extra_tasks_plan_date"), "extra_tasks", ["plan_date"])
    if op.get_bind().dialect.name == "postgresql":
        app_role = os.environ.get("PLAN24_APP_ROLE", "plan24_app")
        for t in ("day_pins", "extra_tasks"):
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY tenant_isolation ON {t} "
                       "USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()))")
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {app_role}")
        op.execute(f"GRANT USAGE, SELECT ON SEQUENCE extra_tasks_id_seq TO {app_role}")


def downgrade() -> None:
    op.drop_index(op.f("ix_extra_tasks_plan_date"), table_name="extra_tasks")
    op.drop_index(op.f("ix_extra_tasks_tenant_id"), table_name="extra_tasks")
    op.drop_table("extra_tasks")
    op.drop_index(op.f("ix_day_pins_tenant_id"), table_name="day_pins")
    op.drop_table("day_pins")
    with op.batch_alter_table("workers") as b:
        b.drop_column("is_temporary")
        b.drop_column("auto_plan")
