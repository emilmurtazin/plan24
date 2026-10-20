"""Личные настройки уведомлений Telegram: telegram_links.settings.

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("telegram_links") as b:
        b.add_column(sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("telegram_links") as b:
        b.drop_column("settings")
