"""jobs: one queue for every kind of background work (P3.1, P3.7)

Copies the validation history from `ai_jobs`, keeping job ids, so the queue
page still shows it. `ai_jobs` stays, unread, until a later release drops it.

A job that was running when the worker stopped is queued again. If an old
database holds two active jobs for one submission (possible before DEF-026),
only the newest stays active and the others are recorded as failed.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_TYPES = "type IN ('validate_submission', 'parse_guideline', 'index_guideline', 'send_email')"
JOB_STATUSES = "status IN ('queued', 'running', 'succeeded', 'failed')"

COPY_AI_JOBS = """
WITH old AS (
    SELECT j.*, v.submission_id, s.university_id,
           CASE upper(j.status)
               WHEN 'COMPLETED' THEN 'succeeded'
               WHEN 'FAILED' THEN 'failed'
               ELSE 'queued'
           END AS new_status
    FROM ai_jobs j
    JOIN submission_versions v ON v.id = j.submission_version_id
    JOIN submissions s ON s.id = v.submission_id
), ranked AS (
    SELECT old.*,
           row_number() OVER (
               PARTITION BY submission_id, new_status = 'queued' ORDER BY id DESC
           ) AS newest
    FROM old
)
INSERT INTO jobs (id, university_id, type, status, subject_key, payload, priority, attempts,
                  run_after, locked_by, locked_at, last_error, created_at, finished_at)
SELECT id, university_id, 'validate_submission',
       CASE WHEN new_status = 'queued' AND newest > 1 THEN 'failed' ELSE new_status END,
       'submission:' || submission_id,
       jsonb_build_object('submission_version_id', submission_version_id),
       priority, attempt_count, now(), NULL,
       started_at AT TIME ZONE 'UTC',
       CASE WHEN new_status = 'queued' AND newest > 1
            THEN 'Superseded by a later upload when the queue moved to jobs.'
            ELSE nullif(error_message, '') END,
       created_at AT TIME ZONE 'UTC',
       CASE WHEN new_status = 'queued' AND newest > 1 THEN now()
            ELSE completed_at AT TIME ZONE 'UTC' END
    FROM ranked
"""


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("university_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("subject_key", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(JOB_TYPES, name=op.f("ck_jobs_jobtype")),
        sa.CheckConstraint(JOB_STATUSES, name=op.f("ck_jobs_jobstatus")),
        sa.ForeignKeyConstraint(
            ["university_id"], ["universities.id"], name=op.f("fk_jobs_university_id_universities")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(
        "ix_jobs_claimable",
        "jobs",
        ["priority", "id"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index("ix_jobs_university_locked", "jobs", ["university_id", "locked_at"])
    op.create_index(
        "uq_jobs_one_active_per_subject",
        "jobs",
        ["subject_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.execute(COPY_AI_JOBS)
    op.execute(
        "SELECT setval(pg_get_serial_sequence('jobs', 'id'), coalesce(max(id), 0) + 1, false) "
        "FROM jobs"
    )


def downgrade() -> None:
    # Validation jobs created since the upgrade are not copied back.
    op.drop_index("uq_jobs_one_active_per_subject", table_name="jobs")
    op.drop_index("ix_jobs_university_locked", table_name="jobs")
    op.drop_index("ix_jobs_claimable", table_name="jobs")
    op.drop_table("jobs")
