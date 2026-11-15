"""auth epochs, login throttling and the audit log (P4.3, P4.4, P4.8)

Existing accounts start at epoch 0 with no failed logins. Their PBKDF2
password hashes are replaced with Argon2id at their next login, by the app.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNTS = ("admin_users", "student_users")


def upgrade() -> None:
    for table in ACCOUNTS:
        op.add_column(
            table, sa.Column("auth_epoch", sa.Integer(), server_default="0", nullable=False)
        )
        op.add_column(
            table, sa.Column("failed_logins", sa.Integer(), server_default="0", nullable=False)
        )
        op.add_column(table, sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("university_id", sa.Integer(), nullable=True),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("subject", sa.String(length=100), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admin_users.id"], name=op.f("fk_audit_events_admin_id_admin_users")
        ),
        sa.ForeignKeyConstraint(
            ["university_id"],
            ["universities.id"],
            name=op.f("fk_audit_events_university_id_universities"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_university_created", "audit_events", ["university_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_university_created", table_name="audit_events")
    op.drop_table("audit_events")
    for table in ACCOUNTS:
        for column in ("locked_until", "failed_logins", "auth_epoch"):
            op.drop_column(table, column)
