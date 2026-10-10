from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from alembic.util import CommandError
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from api.config import get_settings
from api.models import Base

config = context.config
# ConfigParser treats "%" as interpolation syntax, so URL-encoded passwords
# (p%40ss) must be escaped before they go into the config.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

# Callers that already configured logging (tests, scripts) can opt out with
# Config.attributes["configure_logger"] = False.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    # The revisions inspect the live schema (tables, columns, indexes, constraints)
    # to stay idempotent, which needs a database connection.
    raise CommandError(
        "Offline mode (alembic --sql) is not supported: the migrations inspect the live "
        "schema to stay idempotent. Run them online against the target database."
    )


def do_run_migrations(connection) -> None:
    is_sqlite = connection.dialect.name == "sqlite"

    def include_object(obj, name, type_, reflected, compare_to) -> bool:
        # Foreign keys are created only on non-SQLite databases (SQLite cannot add
        # constraints to existing tables), so autogenerate must not report them there.
        return not (is_sqlite and type_ == "foreign_key_constraint")

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async def run() -> None:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
        await connectable.dispose()

    import asyncio

    asyncio.run(run())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
