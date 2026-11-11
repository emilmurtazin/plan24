"""Факт «на заказ» без привязки к исполнителю: facts.worker_id необязателен, добавлен участок факта.

Revision ID: 0019
Revises: 0018
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("facts") as b:
        b.alter_column("worker_id", existing_type=sa.Integer(), nullable=True)
        b.add_column(sa.Column("work_center_id", sa.Integer(), sa.ForeignKey("work_centers.id", name="fk_facts_work_center_id"), nullable=True))
    op.create_index("ux_fact_unlinked", "facts", ["tenant_id", "fact_date", "order_line_id", "operation_id", "work_center_id"], unique=True,
                    sqlite_where=sa.text("worker_id IS NULL"), postgresql_where=sa.text("worker_id IS NULL"))


def downgrade() -> None:
    op.drop_index("ux_fact_unlinked", table_name="facts")
    op.execute("DELETE FROM facts WHERE worker_id IS NULL")
    with op.batch_alter_table("facts") as b:
        b.drop_column("work_center_id")
        b.alter_column("worker_id", existing_type=sa.Integer(), nullable=False)
