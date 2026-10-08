"""Окружение Alembic. Метаданные — модели приложения; URL — из настроек (см. plan24/config.py)."""
from alembic import context
from sqlalchemy import create_engine, pool

from plan24 import config
from plan24.models import Base

target_metadata = Base.metadata


def _url() -> str:
    return context.get_x_argument(as_dictionary=True).get("url") or config.migrate_db_url()


def run_migrations_online():
    url = _url()
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True,
                          render_as_batch=url.startswith("sqlite"))
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
