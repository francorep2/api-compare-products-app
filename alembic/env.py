"""Alembic: la URL sale de DATABASE_URL (misma que usa la app)."""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Importar todos los modelos para que queden en Base.metadata
from app.db import Base, normalize_db_url  # noqa: E402
import app.auth.models  # noqa: E402,F401
import app.models  # noqa: E402,F401
import app.models.comparison  # noqa: E402,F401
import app.models.support  # noqa: E402,F401

target_metadata = Base.metadata


def _url() -> str:
    url = normalize_db_url(os.getenv("DATABASE_URL"))
    if not url:
        raise RuntimeError("DATABASE_URL no está definido")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
