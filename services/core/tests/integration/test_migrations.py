"""DEF-039: the schema comes from migrations, and older databases are adopted.

The `engine` fixture already built the test database with `unisubmit migrate`.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from unisubmit.cli import migrate
from unisubmit.core.db import Base

SCHEMA_V9_14 = Path(__file__).parents[1] / "fixtures" / "schema_v9_14.sql"
CHECKS = sa.text(
    "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE contype = 'c' AND connamespace = current_schema()::regnamespace ORDER BY 2"
)


def test_models_match_the_migrations(engine: sa.Engine) -> None:
    """A model change without a migration fails here (and in `alembic check`)."""
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


@pytest.fixture
def scratch_engine(engine: sa.Engine, database_url: str) -> Iterator[sa.Engine]:
    """An empty schema `scratch`, first on the search path of its own engine."""
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA IF EXISTS scratch CASCADE"))
        connection.execute(sa.text("CREATE SCHEMA scratch"))
    scratch = sa.create_engine(database_url, connect_args={"options": "-c search_path=scratch"})
    yield scratch
    scratch.dispose()
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA scratch CASCADE"))


def test_check_constraints_match_the_models(engine: sa.Engine, scratch_engine: sa.Engine) -> None:
    """`alembic check` ignores CHECK constraints, so compare them directly."""
    Base.metadata.create_all(scratch_engine)
    with engine.connect() as migrated, scratch_engine.connect() as created:
        assert migrated.execute(CHECKS).all() == created.execute(CHECKS).all()


def load_v9_14(engine: sa.Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(SCHEMA_V9_14.read_text())


def schema_state(engine: sa.Engine) -> tuple[str | None, list[str]]:
    with engine.connect() as connection:
        version = (
            connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
            if sa.inspect(connection).has_table("alembic_version")
            else None
        )
        names = connection.execute(
            sa.text(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'scratch.teachers'::regclass "
                "ORDER BY conname"
            )
        ).scalars()
        return version, list(names)


def test_v9_14_database_is_adopted_and_upgraded(scratch_engine: sa.Engine) -> None:
    load_v9_14(scratch_engine)

    migrate(scratch_engine)

    assert schema_state(scratch_engine) == (
        "0002",
        [
            "fk_teachers_department_id_departments",
            "fk_teachers_institute_id_institutes",
            "fk_teachers_university_id_universities",
            "pk_teachers",
            "uq_teachers_university_id_email",
        ],
    )


def test_database_that_differs_from_the_models_is_left_unchanged(
    scratch_engine: sa.Engine,
) -> None:
    load_v9_14(scratch_engine)
    with scratch_engine.begin() as connection:
        connection.execute(sa.text("ALTER TABLE submissions DROP COLUMN title"))

    with pytest.raises(SystemExit, match="left unchanged"):
        migrate(scratch_engine)

    assert schema_state(scratch_engine) == (
        None,
        [
            "teachers_department_id_fkey",
            "teachers_email_key",
            "teachers_institute_id_fkey",
            "teachers_pkey",
        ],
    )
