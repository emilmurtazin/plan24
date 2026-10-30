"""Предшественники операций: старт зависит от выбранных операций, а не от всего предыдущего порядка.

operations.predecessors (JSON, NULL): список имён операций того же маршрута. NULL — как раньше (после всего предыдущего порядка),
[] — без предшественников, иначе — ждёт только перечисленные.

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operations") as b:
        b.add_column(sa.Column("predecessors", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("operations") as b:
        b.drop_column("predecessors")
