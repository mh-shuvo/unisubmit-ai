"""Test environment and database fixtures for the core service.

Database tests need `TEST_DATABASE_URL`: a disposable PostgreSQL database whose
name contains "test". Its `public` schema is dropped and rebuilt with
`unisubmit migrate` once per run, which also tests the migrations. Each test
then runs in a transaction that is rolled back.

    docker compose up -d db
    docker compose exec db createdb -U unisubmit unisubmit_test
    TEST_DATABASE_URL=postgresql+psycopg://unisubmit:PASSWORD@127.0.0.1:5433/unisubmit_test \\
        uv run pytest
"""

import os
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

# Before any application import: tests never read the developer's `.env`.
TEST_SECRET = "test-app-secret-" + "x" * 40
TEST_AI_TOKEN = "test-ai-token-" + "y" * 40
os.environ["UNISUBMIT_SECRET_KEY"] = TEST_SECRET
os.environ["UNISUBMIT_AI_SERVICE_TOKEN"] = TEST_AI_TOKEN
# Never connected to: database tests bind their own engine.
os.environ["UNISUBMIT_DATABASE_URL"] = "postgresql://unused@127.0.0.1:1/unused"

from unisubmit.cli import migrate  # noqa: E402
from unisubmit.core.db import Base, create_db_engine, make_session_factory  # noqa: E402


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to a disposable PostgreSQL database")
    if "test" not in (sa.make_url(url).database or ""):
        pytest.exit("TEST_DATABASE_URL must name a database containing 'test'; it is wiped")
    return url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[sa.Engine]:
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA public CASCADE"))
        connection.execute(sa.text("CREATE SCHEMA public"))
    migrate(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def connection(engine: sa.Engine) -> Iterator[sa.Connection]:
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture
def session_factory(connection: sa.Connection) -> sessionmaker[Session]:
    """Sessions whose commits are savepoints inside the test's transaction."""
    return sessionmaker(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )


@pytest.fixture
def committed_session_factory(engine: sa.Engine) -> Iterator[sessionmaker[Session]]:
    """Real commits, for tests that need several connections (row locks).

    Every table is emptied afterwards.
    """
    yield make_session_factory(engine)
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
