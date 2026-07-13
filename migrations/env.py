import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Models live in apps/api-gateway/app — this is the single owner of the
# SQLAlchemy schema; migrations/ only orchestrates Alembic against it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api-gateway"))

from app.db import Base  # noqa: E402
from app.models import *  # noqa: E402,F403

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_database_url() -> str:
    url = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://ielts:ielts@localhost:5432/ielts_speaking"
    )
    # Alembic runs synchronously; the app uses asyncpg, migrations use psycopg2.
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _sync_database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
