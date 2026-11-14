"""DEF-039: the schema comes from migrations, and older databases are adopted.

The `engine` fixture already built the test database with `unisubmit migrate`.
"""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy.sql.elements import conv

from unisubmit.cli import BASELINE, migrate
from unisubmit.core.db import Base


def test_models_match_the_migrations(engine: sa.Engine) -> None:
    """A model change without a migration fails here (and in `alembic check`)."""
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


@pytest.fixture
def legacy_engine(engine: sa.Engine, database_url: str) -> Iterator[sa.Engine]:
    """An empty schema in which to build a database the way v9.14 did."""
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA IF EXISTS legacy CASCADE"))
        connection.execute(sa.text("CREATE SCHEMA legacy"))
    legacy = sa.create_engine(database_url, connect_args={"options": "-c search_path=legacy"})
    yield legacy
    legacy.dispose()
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA legacy CASCADE"))


def create_all_as_before_migrations(engine: sa.Engine) -> None:
    """`create_all` without the naming convention: Postgres names constraints."""
    metadata = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        for constraint in table.to_metadata(metadata).constraints:
            if isinstance(constraint.name, conv):
                constraint.name = None
    metadata.create_all(engine)


def schema_state(engine: sa.Engine) -> tuple[str | None, list[str]]:
    with engine.connect() as connection:
        version = (
            connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
            if sa.inspect(connection).has_table("alembic_version")
            else None
        )
        names = connection.execute(
            sa.text(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'legacy.teachers'::regclass "
                "ORDER BY conname"
            )
        ).scalars()
        return version, list(names)


def test_database_created_without_migrations_is_adopted(legacy_engine: sa.Engine) -> None:
    create_all_as_before_migrations(legacy_engine)

    migrate(legacy_engine)

    assert schema_state(legacy_engine) == (
        BASELINE,
        [
            "fk_teachers_department_id_departments",
            "fk_teachers_institute_id_institutes",
            "pk_teachers",
            "uq_teachers_email",
        ],
    )


def test_database_that_differs_from_the_baseline_is_refused(legacy_engine: sa.Engine) -> None:
    create_all_as_before_migrations(legacy_engine)
    with legacy_engine.begin() as connection:
        connection.execute(sa.text("ALTER TABLE submissions DROP COLUMN title"))

    with pytest.raises(SystemExit, match="cannot be adopted"):
        migrate(legacy_engine)

    assert schema_state(legacy_engine) == (
        None,
        [
            "teachers_department_id_fkey",
            "teachers_email_key",
            "teachers_institute_id_fkey",
            "teachers_pkey",
        ],
    )
