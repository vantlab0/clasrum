"""Alembic environment — async engine, app settings as the URL source.

Works with both drivers used by this project:
* ``sqlite+aiosqlite:///...`` for tests / local development
* ``postgresql+asyncpg://...`` for production
"""
from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Make ``app`` importable no matter where alembic is invoked from.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: E402,F401  (register every table on Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Prefer ``-x url=...`` (useful in CI) over the app settings."""
    cli_url = context.get_x_argument(as_dictionary=True).get("url")
    if cli_url:
        return cli_url
    return get_settings().database_url


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    options = {
        "target_metadata": target_metadata,
        "compare_type": True,
        "compare_server_default": True,
        "include_schemas": False,
        "render_as_batch": (url or get_database_url()).startswith("sqlite"),
    }
    if connection is not None:
        options["connection"] = connection
    else:
        options["url"] = url or get_database_url()
        options["literal_binds"] = True
        options["dialect_opts"] = {"paramstyle": "named"}
    context.configure(**options)


def run_migrations_offline() -> None:
    _configure(url=get_database_url())
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(get_database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
