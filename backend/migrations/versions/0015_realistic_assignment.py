"""Реалистичное назначение: постоянство исполнителя, ограничение заданий в день, без мелких кусков.

planner_settings: realistic_mode (по умолчанию включён, в том числе у существующих организаций), min_chunk_share (0,5 дня),
max_tasks_per_day (2). Выключить можно в «Организация → Планирование»: тогда план строится как раньше.

Revision ID: 0015
Revises: 0014
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("planner_settings") as b:
        b.add_column(sa.Column("realistic_mode", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("min_chunk_share", sa.Float(), nullable=False, server_default="0.5"))
        b.add_column(sa.Column("max_tasks_per_day", sa.Integer(), nullable=False, server_default="2"))


def downgrade() -> None:
    with op.batch_alter_table("planner_settings") as b:
        b.drop_column("max_tasks_per_day")
        b.drop_column("min_chunk_share")
        b.drop_column("realistic_mode")
