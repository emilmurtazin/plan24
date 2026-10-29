"""Настройки планирования по участкам, партии на операциях, удаление «жёсткости по уровням».

- work_centers: efficiency, max_workers_per_op, today_load_threshold, min_start_share (пусто — общее значение);
- operations.start_after_pct: старт следующей операции, когда предыдущая выполнена на N % (пусто — после полного окончания);
- planner_settings.strictness_by_level удалена: отдельная жёсткость по уровням приоритета больше не используется.

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("work_centers") as b:
        b.add_column(sa.Column("efficiency", sa.Float(), nullable=True))
        b.add_column(sa.Column("max_workers_per_op", sa.Integer(), nullable=True))
        b.add_column(sa.Column("today_load_threshold", sa.Float(), nullable=True))
        b.add_column(sa.Column("min_start_share", sa.Float(), nullable=True))
    with op.batch_alter_table("operations") as b:
        b.add_column(sa.Column("start_after_pct", sa.Float(), nullable=True))
    with op.batch_alter_table("planner_settings") as b:
        b.drop_column("strictness_by_level")


def downgrade() -> None:
    with op.batch_alter_table("planner_settings") as b:
        b.add_column(sa.Column("strictness_by_level", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("operations") as b:
        b.drop_column("start_after_pct")
    with op.batch_alter_table("work_centers") as b:
        b.drop_column("min_start_share")
        b.drop_column("today_load_threshold")
        b.drop_column("max_workers_per_op")
        b.drop_column("efficiency")
