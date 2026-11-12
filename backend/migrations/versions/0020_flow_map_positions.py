"""Положение карточек операций на «Карте потока» (operations.map_x, map_y).

Revision ID: 0020
Revises: 0019
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("map_x", sa.Float(), nullable=True))
    op.add_column("operations", sa.Column("map_y", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("operations") as b:
        b.drop_column("map_y")
        b.drop_column("map_x")
