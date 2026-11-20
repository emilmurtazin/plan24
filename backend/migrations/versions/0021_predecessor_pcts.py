"""Партия по каждому предшественнику (operations.predecessor_pcts).

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("predecessor_pcts", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("operations") as b:
        b.drop_column("predecessor_pcts")
