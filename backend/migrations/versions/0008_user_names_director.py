"""Имя и фамилия пользователя отдельными полями (роль «Директор» — без изменений схемы: роль хранится строкой).

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("first_name", sa.String(length=100), nullable=False, server_default=""))
        b.add_column(sa.Column("last_name", sa.String(length=100), nullable=False, server_default=""))
    # существующие пользователи: первое слово — имя, остальное — фамилия (поправить можно в «Профиле»)
    bind = op.get_bind()
    users = sa.table("users", sa.column("id", sa.Integer), sa.column("name", sa.String),
                     sa.column("first_name", sa.String), sa.column("last_name", sa.String))
    for uid, name in bind.execute(sa.select(users.c.id, users.c.name)).fetchall():
        parts = (name or "").split(None, 1)
        bind.execute(users.update().where(users.c.id == uid).values(
            first_name=(parts[0] if parts else "")[:100], last_name=(parts[1] if len(parts) > 1 else "")[:100]))


def downgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.drop_column("last_name")
        b.drop_column("first_name")
