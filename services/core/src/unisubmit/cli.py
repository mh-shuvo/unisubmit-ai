"""The `unisubmit` command: schema migrations and bootstrap data.

    unisubmit migrate                             # create or upgrade the schema
    unisubmit relocate-files                      # move pre-v10 uploads into per-university folders
    unisubmit seed-demo                           # demo university, faculty, programs, supervisors
    unisubmit create-platform-admin --email ...   # first Platform Admin, password prompted

Run `migrate` before starting the web app and the worker; neither creates
tables (DEF-039, DEF-045).
"""

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.orm import Session

import unisubmit.models  # noqa: F401  registers every table on Base.metadata
from unisubmit import storage
from unisubmit.core.config import get_settings
from unisubmit.core.db import Base, create_db_engine, make_session_factory
from unisubmit.models import SubmissionVersion, TemplateVersion
from unisubmit.seed import seed_demo_data
from unisubmit.tenancy import TenancyError, create_platform_admin

# The revision that describes the schema `create_all` built before migrations.
BASELINE = "0001"


# Constraints Postgres named itself, and the names the naming convention gives
# them; reading the schema rather than the models keeps adoption valid for
# every later revision.
POSTGRES_NAMES = sa.text(
    """
    SELECT c.conrelid::regclass::text, c.conname, c.contype,
           array_agg(a.attname ORDER BY k.ord), max(c.confrelid::regclass::text)
    FROM pg_constraint c
    JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
    WHERE c.connamespace = current_schema()::regnamespace AND c.contype IN ('p', 'u', 'f')
    GROUP BY c.conrelid, c.conname, c.contype
    """
)


def _convention_name(table: str, name: str, kind: str, columns: list[str], ref: str) -> str:
    joined = "_".join(columns)
    if kind == "p" and name == f"{table}_pkey":
        return f"pk_{table}"
    if kind == "u" and name == f"{table}_{joined}_key":
        return f"uq_{table}_{joined}"
    if kind == "f" and name == f"{table}_{joined}_fkey":
        return f"fk_{table}_{columns[0]}_{ref}"
    return name  # named explicitly in the models, e.g. uq_program_submission_type


def _adopt_constraint_names(connection: sa.Connection) -> None:
    """Rename `teachers_email_key` and the like to `uq_teachers_email`, the name
    the baseline revision gives it, so later migrations can refer to it."""
    quote = connection.dialect.identifier_preparer.quote
    for table, name, kind, columns, ref in connection.execute(POSTGRES_NAMES).all():
        new = _convention_name(table, name, kind, columns, ref or "")
        if new != name:
            connection.execute(
                sa.text(
                    f"ALTER TABLE {quote(table)} RENAME CONSTRAINT {quote(name)} TO {quote(new)}"
                )
            )


def migrate(engine: sa.Engine) -> None:
    """Upgrade to the latest revision.

    A database created by `create_all` before migrations existed (v9.14 and
    earlier) is adopted in the same transaction: its constraints get the
    baseline's names, it is stamped at the baseline and upgraded, and only if
    it then matches the models exactly is the transaction committed. Otherwise
    nothing changes and the differences are listed.
    """
    config = Config()
    config.set_main_option("script_location", "unisubmit:migrations")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        tables = set(sa.inspect(connection).get_table_names())
        adopting = bool(tables) and "alembic_version" not in tables
        if adopting:
            _adopt_constraint_names(connection)
            command.stamp(config, BASELINE)
        command.upgrade(config, "head")
        if adopting:
            differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            if differences:
                listed = "\n".join(f"  {difference}" for difference in differences)
                raise SystemExit(
                    "This database was created without migrations and does not match the "
                    f"models after upgrading, so it was left unchanged:\n{listed}"
                )
            print(f"Adopted the existing schema at revision {BASELINE} and upgraded it.")
    print("The database schema is up to date.")


def relocate_files(db: Session) -> tuple[int, int]:
    """Move files stored under absolute pre-v10 paths into `<root>/<university>/<kind>/`
    and store relative paths (P2.9, DEF-042). Returns (moved, missing); safe to rerun."""
    moved = missing = 0
    rows: list[tuple[object, str, int, storage.Kind]] = []
    for guideline in db.scalars(select(TemplateVersion)):
        program = guideline.template.program
        unit = program.department.faculty if program.department else program.institute
        rows += [
            (guideline, "pdf_path", unit.university_id, "guidelines"),
            (guideline, "additional_rules_pdf_path", unit.university_id, "guidelines"),
        ]
    for upload in db.scalars(select(SubmissionVersion)):
        rows += [
            (upload, "pdf_path", upload.submission.university_id, "submissions"),
            (upload, "report_path", upload.submission.university_id, "reports"),
        ]
    for row, column, university_id, kind in rows:
        stored = getattr(row, column)
        if not stored or not Path(stored).is_absolute():
            continue
        new = storage.relocate(stored, university_id, kind)
        if new is None:
            missing += 1
        else:
            setattr(row, column, new)
            moved += 1
    db.commit()
    return moved, missing


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unisubmit", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="create or upgrade the database schema")
    commands.add_parser("relocate-files", help="move pre-v10 files into per-university folders")
    commands.add_parser("seed-demo", help="create a demo university with sample data")
    admin = commands.add_parser("create-platform-admin", help="create a Platform Admin account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", default="Platform Admin")
    args = parser.parse_args(argv)

    engine = create_db_engine(str(get_settings().database_url))
    try:
        if args.command == "migrate":
            migrate(engine)
            return 0
        with make_session_factory(engine)() as db:
            if args.command == "relocate-files":
                moved, missing = relocate_files(db)
                print(f"Relocated {moved} files; {missing} referenced files were not found.")
                return 0
            if args.command == "seed-demo":
                seed_demo_data(db)
                print("Demo academic data is in place.")
                return 0
            # UNISUBMIT_ADMIN_PASSWORD allows a non-interactive run, e.g. in Docker.
            password = os.environ.get("UNISUBMIT_ADMIN_PASSWORD") or getpass.getpass("Password: ")
            if (
                not os.environ.get("UNISUBMIT_ADMIN_PASSWORD")
                and getpass.getpass("Repeat password: ") != password
            ):
                print("The passwords do not match.", file=sys.stderr)
                return 1
            try:
                create_platform_admin(db, args.name, args.email, password)
            except TenancyError as exc:
                print(exc, file=sys.stderr)
                return 1
            print(f"Platform Admin {args.email.strip().lower()} created.")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
