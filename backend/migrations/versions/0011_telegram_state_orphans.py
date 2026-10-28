"""Telegram: убрать «осиротевшие» состояния диалога после отвязки чата.

Раньше при отвязке удалялась только привязка, а состояние диалога (telegram_state, ключ — chat_id) оставалось в старой
организации. Из-за этого чат нельзя было привязать к другой организации. Состояние — только временные данные диалога
(ожидаемое число, «отменить»), его безопасно удалить.

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM telegram_state WHERE chat_id NOT IN (SELECT chat_id FROM telegram_links)"))


def downgrade() -> None:
    pass
