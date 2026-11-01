"""Исполнители операций заказа, назначенные вручную (бригадиром или руководителем).

operation_assignees: позиция заказа × операция × работник; strict — «только они» или «в первую очередь они».

Revision ID: 0016
Revises: 0015
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_assignees",
        sa.Column("order_line_id", sa.Integer(), sa.ForeignKey("order_lines.id"), primary_key=True),
        sa.Column("operation_id", sa.Integer(), sa.ForeignKey("operations.id"), primary_key=True),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id"), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("strict", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index(op.f("ix_operation_assignees_tenant_id"), "operation_assignees", ["tenant_id"])
    if op.get_bind().dialect.name == "postgresql":
        app_role = os.environ.get("PLAN24_APP_ROLE", "plan24_app")
        op.execute("ALTER TABLE operation_assignees ENABLE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON operation_assignees "
                   "USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()))")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON operation_assignees TO {app_role}")


def downgrade() -> None:
    op.drop_index(op.f("ix_operation_assignees_tenant_id"), table_name="operation_assignees")
    op.drop_table("operation_assignees")
