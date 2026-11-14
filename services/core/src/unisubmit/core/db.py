"""Database engine, sessions and the declarative base.

Sync SQLAlchemy on psycopg 3: one GPU works serially, so async database calls
would gain nothing, and templates rely on lazy loading.
"""

from collections.abc import Iterator
from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, Request
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Deterministic constraint names, so migrations can refer to them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


def create_db_engine(url: str) -> sa.Engine:
    """Opens no connection until the first query."""
    return sa.create_engine(
        url,
        pool_size=10,
        max_overflow=10,
        pool_timeout=10,
        pool_pre_ping=True,
        connect_args={"options": "-c statement_timeout=30000"},  # 30 s cap per statement
    )


def make_session_factory(engine: sa.Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, autoflush=False, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:  # closing rolls back anything not committed
        yield session


DbSession = Annotated[Session, Depends(get_session)]
