from __future__ import annotations

import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from api.config import get_settings
from api.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# RV-DB-01: fileConfig() disables every logger created before it. The
# Postgres migration tests run alembic inside the pytest process, which
# silenced the app loggers for every test after them (caplog went empty);
# they opt out via ``configure_logger``. The CLI default stays True.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# RV-DB-01: monthly + DEFAULT partitions of ai_audit_log (20260514_0016,
# created ahead of time by the collector). They are database-only by
# design; without this filter every ``--autogenerate`` proposed dropping
# them. Triggers and plpgsql functions need no filter — autogenerate does
# not reflect them.
_AI_AUDIT_PARTITION_RE = re.compile(r"^ai_audit_log_(?:\d{4}_\d{2}|default)$")

# RV-DB-01: the same options drive ``--autogenerate`` and the schema drift
# test (tests/test_migrations.py), so both see exactly the same diff.
_COMPARE_OPTIONS = {"compare_type": True, "compare_server_default": True}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    if not reflected or compare_to is not None:
        return True
    table = obj if type_ == "table" else getattr(obj, "table", None)
    table_name = getattr(table, "name", None)
    return not (table_name and _AI_AUDIT_PARTITION_RE.match(table_name))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        **_COMPARE_OPTIONS,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        **_COMPARE_OPTIONS,
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
