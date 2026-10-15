"""Alembic environment: URL comes from app settings, metadata from the ORM models."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.db.models  # noqa: F401  (registers tables on Base.metadata)
from app.config import get_settings
from app.db.base import Base

config = context.config
settings = get_settings()
# The schema owner's URL when configured (Neon direct host), else the app's. configparser treats
# `%` as interpolation, and URL-encoded passwords contain it.
url = settings.migration_database_url or settings.database_url
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",  # SQLite ALTER limitations
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
