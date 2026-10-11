"""Название версии маршрута и уровни квалификации организации (этап 2 доработок).

Revision ID: 0004
Revises: 0003
"""
import math

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("routings") as b:
        b.add_column(sa.Column("name", sa.String(length=120), nullable=False, server_default=""))
    with op.batch_alter_table("planner_settings") as b:
        b.add_column(sa.Column("qualifications", sa.JSON(), nullable=False, server_default="[]"))
    # число уровней = не меньше 3 и не меньше самого высокого из используемых у работников и операций
    bind = op.get_bind()
    for (tid,) in bind.execute(sa.text("SELECT tenant_id FROM planner_settings")).fetchall():
        w = bind.execute(sa.text("SELECT MAX(skill) FROM workers WHERE tenant_id = :t"), {"t": tid}).scalar() or 0
        o = bind.execute(sa.text("SELECT MAX(min_skill) FROM operations WHERE tenant_id = :t"), {"t": tid}).scalar() or 0
        n = min(10, max(3, math.ceil(max(w, o))))
        bind.execute(sa.text("UPDATE planner_settings SET qualifications = :q WHERE tenant_id = :t")
                     .bindparams(sa.bindparam("q", type_=sa.JSON)), {"q": [f"Уровень {i}" for i in range(1, n + 1)], "t": tid})


def downgrade() -> None:
    with op.batch_alter_table("planner_settings") as b:
        b.drop_column("qualifications")
    with op.batch_alter_table("routings") as b:
        b.drop_column("name")
