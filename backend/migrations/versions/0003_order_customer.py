"""Заказчик в позиции заказа (этап 1 доработок).

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("order_lines") as b:
        b.add_column(sa.Column("customer", sa.String(length=160), nullable=False, server_default=""))
    # у существующих позиций заказчик берётся из проекта
    op.execute("UPDATE order_lines SET customer = COALESCE((SELECT p.customer FROM projects p WHERE p.id = order_lines.project_id), '')")


def downgrade() -> None:
    with op.batch_alter_table("order_lines") as b:
        b.drop_column("customer")
