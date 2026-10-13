"""Снимки плана по дням — основа графика недовыполнения плана (этап 6 доработок).

Revision ID: 0007
Revises: 0006
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("planner_settings") as b:
        b.add_column(sa.Column("snapshot_on", sa.Date()))
    op.create_table(
        "day_plan_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("order_line_id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Integer(), nullable=False),
        sa.Column("work_center_id", sa.Integer()),
        sa.Column("qty", sa.Integer(), nullable=False),
    )
    op.create_index(op.f("ix_day_plan_snapshots_tenant_id"), "day_plan_snapshots", ["tenant_id"])
    op.create_index(op.f("ix_day_plan_snapshots_plan_date"), "day_plan_snapshots", ["plan_date"])
    if op.get_bind().dialect.name == "postgresql":
        app_role = os.environ.get("PLAN24_APP_ROLE", "plan24_app")
        op.execute("ALTER TABLE day_plan_snapshots ENABLE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON day_plan_snapshots "
                   "USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()))")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON day_plan_snapshots TO {app_role}")
        op.execute(f"GRANT USAGE, SELECT ON SEQUENCE day_plan_snapshots_id_seq TO {app_role}")


def downgrade() -> None:
    op.drop_index(op.f("ix_day_plan_snapshots_plan_date"), table_name="day_plan_snapshots")
    op.drop_index(op.f("ix_day_plan_snapshots_tenant_id"), table_name="day_plan_snapshots")
    op.drop_table("day_plan_snapshots")
    with op.batch_alter_table("planner_settings") as b:
        b.drop_column("snapshot_on")
