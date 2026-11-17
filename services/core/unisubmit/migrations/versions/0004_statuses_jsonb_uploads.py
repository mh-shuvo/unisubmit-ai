"""statuses become enums, stored results JSONB, uploads get a fingerprint (P3.8-P3.10)

Existing free-text statuses are matched without regard to case or spaces.
A status that matches nothing is mapped to the safe value: a version or
submission to VALIDATION FAILED (so the student can upload again), parsed
guideline rules to "Parsed - Review Required" (an admin must activate them
again), anything else to its starting value. An old email status of
"Failed / SMTP not configured" only ever meant that SMTP was not configured.

Versions uploaded before this revision have no sha256, size or page count.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUBMISSION = ("QUEUED", "PROCESSING", "PASSED", "REVISION REQUIRED", "VALIDATION FAILED")
# (table, column, enum name, allowed values in model order, old length,
#  {lower-case old value: new value}, value for anything else)
COLUMNS: tuple[tuple[str, str, str, tuple[str, ...], int, dict[str, str], str], ...] = (
    (
        "submissions",
        "status",
        "submissionstatus",
        SUBMISSION,
        50,
        {"retrying": "QUEUED"},
        "VALIDATION FAILED",
    ),
    (
        "submission_versions",
        "status",
        "submissionstatus",
        SUBMISSION,
        50,
        {"retrying": "QUEUED"},
        "VALIDATION FAILED",
    ),
    (
        "submissions",
        "email_status",
        "emailstatus",
        ("Not Sent", "Queued", "Sent", "SMTP Not Configured", "Failed"),
        50,
        {"failed / smtp not configured": "SMTP Not Configured"},
        "Not Sent",
    ),
    (
        "template_versions",
        "status",
        "guidelinestatus",
        ("Draft", "Active", "Archived"),
        30,
        {},
        "Draft",
    ),
    (
        "template_versions",
        "rag_status",
        "ragstatus",
        ("Not Indexed", "Indexed", "Index Failed"),
        40,
        {},
        "Not Indexed",
    ),
    (
        "template_versions",
        "format_profile_status",
        "profilestatus",
        (
            "Not Extracted",
            "Parsing Failed",
            "Parsed - Review Required",
            "Parsed - No Explicit Rules Found",
            "Active",
        ),
        40,
        {},
        "Parsed - Review Required",
    ),
    ("ai_profiles", "status", "aiprofilestatus", ("Draft", "Active"), 30, {}, "Draft"),
)
JSON_COLUMNS = (
    ("submission_versions", "validation_json", "[]"),
    ("template_versions", "format_profile_json", "{}"),
)


def _quoted(values: Sequence[str]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


def upgrade() -> None:
    for table, column, name, allowed, _, renamed, fallback in COLUMNS:
        cases = {value.lower(): value for value in allowed} | renamed
        when = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in cases.items())
        op.execute(
            f"UPDATE {table} SET {column} = CASE lower(btrim({column})) {when} "  # noqa: S608
            f"ELSE '{fallback}' END"
        )
        op.alter_column(table, column, type_=sa.String(length=40))
        op.create_check_constraint(
            op.f(f"ck_{table}_{name}"), table, f"{column} IN ({_quoted(allowed)})"
        )
    # Unparsable profiles were already unusable; empty strings meant "none".
    op.execute(
        "UPDATE template_versions SET format_profile_status = 'Not Extracted' "
        "WHERE format_profile_status = 'Parsed - Review Required' "
        "AND btrim(format_profile_json) IN ('', '{}')"
    )
    for table, column, empty in JSON_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=postgresql.JSONB(astext_type=sa.Text()),
            postgresql_using=f"(CASE WHEN btrim({column}) = '' THEN '{empty}' "
            f"ELSE {column} END)::jsonb",
        )
    for column in ("sha256", "size_bytes", "page_count"):
        op.add_column(
            "submission_versions",
            sa.Column(
                column, sa.String(length=64) if column == "sha256" else sa.Integer(), nullable=True
            ),
        )


def downgrade() -> None:
    for column in ("page_count", "size_bytes", "sha256"):
        op.drop_column("submission_versions", column)
    for table, column, _ in JSON_COLUMNS:
        op.alter_column(table, column, type_=sa.Text(), postgresql_using=f"{column}::text")
    for table, column, name, _, length, _, _ in COLUMNS:
        op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
        op.alter_column(table, column, type_=sa.String(length=length))
