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


# One department's submissions as v9.14 left them: free-text statuses, the
# old queue and its history.
DATA_V9_14 = """
INSERT INTO faculties VALUES (1, 'Faculty of Science', 'SCI', true);
INSERT INTO departments VALUES (1, 'Computer Science', 'CSE', 1, true);
INSERT INTO programs VALUES (1, 'M.Sc. in CSE', 'MSC-CSE', 'Postgraduate', 1, NULL, true);
INSERT INTO student_users VALUES
    (1, 'Student', 'S-1', 's@example.edu', 'x', 1, NULL, true, true, '2026-11-01');
INSERT INTO teachers VALUES (1, 'Supervisor', 'Professor', 't@example.edu', true, 1, NULL);
INSERT INTO templates VALUES (1, 'Thesis', 'M.Sc. Thesis', 1);
INSERT INTO template_versions VALUES (1, 1, '1.0', 'g.pdf', '', '', '', '', 'Active', '',
    'tv_1', 'Indexed', '{"semantic_requirements": []}', 'Active', '2026-11-01');
INSERT INTO submissions VALUES
    (1, 1, 'First', 1, 1, 1, NULL, 'PASSED', 'Failed / SMTP not configured', '2026-11-01'),
    (2, 1, 'Second', 1, 1, 1, NULL, 'QUEUED', 'Not Sent', '2026-11-02');
INSERT INTO submission_versions VALUES
    (1, 1, 1, 'a.pdf', '', 'PASSED', '[]', '', '', '', NULL, '2026-11-01 10:00'),
    (2, 2, 1, 'b.pdf', '', 'Validation Failed', '[]', '', '', '', NULL, '2026-11-02 09:00'),
    (3, 2, 2, 'c.pdf', '', 'QUEUED', '[]', '', '', '', NULL, '2026-11-02 10:00');
INSERT INTO ai_jobs VALUES
    (1, 1, 'COMPLETED', 100, 1, '', '2026-11-01 10:00', '2026-11-01 10:01', '2026-11-01 10:07'),
    (2, 2, 'FAILED', 100, 3, 'AI down', '2026-11-02 09:00', '2026-11-02 09:20', '2026-11-02 09:30'),
    (3, 3, 'PROCESSING', 100, 1, '', '2026-11-02 10:00', '2026-11-02 10:01', NULL);
"""


def load_v9_14(engine: sa.Engine, data: str = "") -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(SCHEMA_V9_14.read_text())
        if data:
            connection.exec_driver_sql(data)


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
        "0005",
        [
            "fk_teachers_department_id_departments",
            "fk_teachers_institute_id_institutes",
            "fk_teachers_university_id_universities",
            "pk_teachers",
            "uq_teachers_university_id_email",
        ],
    )


def test_v9_14_queue_history_is_copied_into_jobs(scratch_engine: sa.Engine) -> None:
    """P3.7: old jobs keep their ids; one stopped mid-run is queued again."""
    load_v9_14(scratch_engine, DATA_V9_14)

    migrate(scratch_engine)

    with scratch_engine.connect() as connection:
        jobs = connection.execute(
            sa.text(
                "SELECT id, type, status, subject_key, payload, attempts, last_error, "
                "nextval(pg_get_serial_sequence('jobs', 'id')) > 3 FROM jobs ORDER BY id"
            )
        ).all()
    assert [tuple(job) for job in jobs] == [
        (
            1,
            "validate_submission",
            "succeeded",
            "submission:1",
            {"submission_version_id": 1},
            1,
            None,
            True,
        ),
        (
            2,
            "validate_submission",
            "failed",
            "submission:2",
            {"submission_version_id": 2},
            3,
            "AI down",
            True,
        ),
        (
            3,
            "validate_submission",
            "queued",
            "submission:2",
            {"submission_version_id": 3},
            1,
            None,
            True,
        ),
    ]


def test_v9_14_statuses_and_stored_json_are_converted(scratch_engine: sa.Engine) -> None:
    """P3.9 and P3.10: free-text statuses map onto the enums, text onto JSONB."""
    load_v9_14(scratch_engine, DATA_V9_14)

    migrate(scratch_engine)

    with scratch_engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT s.status, s.email_status, v.status, jsonb_typeof(v.validation_json) "
                "FROM submission_versions v JOIN submissions s ON s.id = v.submission_id "
                "UNION ALL SELECT status, rag_status, format_profile_status, "
                "jsonb_typeof(format_profile_json) FROM template_versions ORDER BY 1, 3"
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("Active", "Indexed", "Active", "object"),
        ("PASSED", "SMTP Not Configured", "PASSED", "array"),
        ("QUEUED", "Not Sent", "QUEUED", "array"),
        ("QUEUED", "Not Sent", "VALIDATION FAILED", "array"),
    ]


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
