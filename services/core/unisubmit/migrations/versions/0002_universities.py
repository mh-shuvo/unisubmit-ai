"""universities: tenants, per-university uniqueness and admin roles (P2.1-P2.4)

Existing data is assigned to one new university, "Default University" (slug
`default`), which a Platform Admin can rename. A database without data gets
no university. Super Admins become University Admins of that university; the
first Platform Admin comes from `unisubmit create-platform-admin`.

Downgrade fails if two universities hold units or supervisors with the same
name, code or email, because the old constraints are installation-wide.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_NAME = "Default University"
DEFAULT_SLUG = "default"
# Admins are last: only Platform Admins may keep a NULL university.
TENANT_TABLES = ("faculties", "institutes", "teachers", "student_users", "submissions")
ROLES = "role IN ('PLATFORM_ADMIN', 'UNIVERSITY_ADMIN', 'UNIT_ADMIN')"
ROLE_MATCHES_UNIVERSITY = "(role = 'PLATFORM_ADMIN') = (university_id IS NULL)"
# (table, columns) of the installation-wide constraints that become scoped.
GLOBAL_UNIQUES = (
    ("faculties", "name"),
    ("faculties", "code"),
    ("institutes", "name"),
    ("institutes", "code"),
    ("departments", "name"),
    ("departments", "code"),
    ("teachers", "email"),
)
SCOPE = {
    "faculties": "university_id",
    "institutes": "university_id",
    "departments": "faculty_id",
    "teachers": "university_id",
}


def upgrade() -> None:
    op.create_table(
        "universities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_universities")),
        sa.UniqueConstraint("name", name=op.f("uq_universities_name")),
        sa.UniqueConstraint("slug", name=op.f("uq_universities_slug")),
    )
    connection = op.get_bind()
    has_data = any(
        connection.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar()  # noqa: S608
        for table in (*TENANT_TABLES, "admin_users")
    )
    default_id = (
        connection.execute(
            sa.text(
                "INSERT INTO universities (name, slug, is_active) "
                "VALUES (:name, :slug, true) RETURNING id"
            ),
            {"name": DEFAULT_NAME, "slug": DEFAULT_SLUG},
        ).scalar_one()
        if has_data
        else None
    )

    for table in (*TENANT_TABLES, "admin_users"):
        op.add_column(table, sa.Column("university_id", sa.Integer(), nullable=True))
        if default_id is not None:
            connection.execute(
                sa.text(f"UPDATE {table} SET university_id = :id"),  # noqa: S608
                {"id": default_id},
            )
        if table != "admin_users":
            op.alter_column(table, "university_id", nullable=False)
        op.create_index(op.f(f"ix_{table}_university_id"), table, ["university_id"])
        op.create_foreign_key(
            op.f(f"fk_{table}_university_id_universities"),
            table,
            "universities",
            ["university_id"],
            ["id"],
        )

    op.execute("UPDATE admin_users SET role = 'UNIVERSITY_ADMIN' WHERE role = 'SUPER_ADMIN'")
    op.create_check_constraint(op.f("ck_admin_users_adminrole"), "admin_users", ROLES)
    op.create_check_constraint(
        op.f("ck_admin_users_university_matches_role"), "admin_users", ROLE_MATCHES_UNIVERSITY
    )

    for table, column in GLOBAL_UNIQUES:
        op.drop_constraint(op.f(f"uq_{table}_{column}"), table, type_="unique")
        op.create_unique_constraint(
            op.f(f"uq_{table}_{SCOPE[table]}_{column}"), table, [SCOPE[table], column]
        )


def downgrade() -> None:
    for table, column in GLOBAL_UNIQUES:
        op.drop_constraint(op.f(f"uq_{table}_{SCOPE[table]}_{column}"), table, type_="unique")
        op.create_unique_constraint(op.f(f"uq_{table}_{column}"), table, [column])

    op.drop_constraint(op.f("ck_admin_users_university_matches_role"), "admin_users", type_="check")
    op.drop_constraint(op.f("ck_admin_users_adminrole"), "admin_users", type_="check")
    op.execute("DELETE FROM admin_users WHERE role = 'PLATFORM_ADMIN'")
    op.execute("UPDATE admin_users SET role = 'SUPER_ADMIN' WHERE role = 'UNIVERSITY_ADMIN'")

    for table in (*TENANT_TABLES, "admin_users"):
        op.drop_constraint(
            op.f(f"fk_{table}_university_id_universities"), table, type_="foreignkey"
        )
        op.drop_index(op.f(f"ix_{table}_university_id"), table_name=table)
        op.drop_column(table, "university_id")
    op.drop_table("universities")
