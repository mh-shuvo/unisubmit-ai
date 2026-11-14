"""Alembic environment. The database URL comes from Settings, never alembic.ini.

`unisubmit migrate` passes its own connection in `config.attributes`, so the
adoption check, the stamp and the upgrade share one transaction.
"""

from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context

import unisubmit.models  # noqa: F401  registers every table on Base.metadata
from unisubmit.core.config import get_settings
from unisubmit.core.db import Base, create_db_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations(connection: sa.Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) migrations are not supported.")

shared: sa.Connection | None = config.attributes.get("connection")
if shared is not None:
    run_migrations(shared)
else:
    engine = create_db_engine(str(get_settings().database_url))
    try:
        with engine.connect() as connection:
            run_migrations(connection)
    finally:
        engine.dispose()
