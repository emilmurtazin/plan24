"""Распределение операций позиций заказов по участкам (этап 3 доработок).

Revision ID: 0005
Revises: 0004

Существующие позиции получают «все операции на все участки»: поведение расчёта не меняется,
дальше руководитель уточняет распределение вручную.
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_line_operations",
        sa.Column("order_line_id", sa.Integer(), sa.ForeignKey("order_lines.id"), primary_key=True),
        sa.Column("operation_id", sa.Integer(), sa.ForeignKey("operations.id"), primary_key=True),
        sa.Column("work_center_id", sa.Integer(), sa.ForeignKey("work_centers.id"), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
    )
    op.create_index(op.f("ix_order_line_operations_tenant_id"), "order_line_operations", ["tenant_id"])
    if op.get_bind().dialect.name == "postgresql":
        app_role = os.environ.get("PLAN24_APP_ROLE", "plan24_app")
        op.execute("ALTER TABLE order_line_operations ENABLE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON order_line_operations "
                   "USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()))")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON order_line_operations TO {app_role}")
    op.execute("""INSERT INTO order_line_operations (tenant_id, order_line_id, operation_id, work_center_id)
                  SELECT ol.tenant_id, ol.id, o.id, wc.id
                  FROM order_lines ol
                  JOIN operations o ON o.routing_id = ol.routing_id
                  JOIN work_centers wc ON wc.tenant_id = ol.tenant_id""")


def downgrade() -> None:
    op.drop_index(op.f("ix_order_line_operations_tenant_id"), table_name="order_line_operations")
    op.drop_table("order_line_operations")
