"""The `unisubmit` command: schema migrations and bootstrap data.

    unisubmit migrate                          # create or upgrade the schema
    unisubmit seed-demo                        # sample faculty, programs, supervisors
    unisubmit create-super-admin --email ...   # first Super Admin, password prompted

Run `migrate` before starting the web app and the worker; neither creates
tables (DEF-039, DEF-045).
"""

import argparse
import getpass
import os
import sys
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext

import unisubmit.models  # noqa: F401  registers every table on Base.metadata
from unisubmit.core.config import get_settings
from unisubmit.core.db import Base, create_db_engine, make_session_factory
from unisubmit.seed import create_super_admin, seed_demo_data

# The revision that describes the schema `create_all` built before migrations.
BASELINE = "0001"


def _constraint_key(constraint: sa.Constraint) -> tuple[str, ...] | None:
    if isinstance(constraint, sa.PrimaryKeyConstraint):
        return ("p",)
    if isinstance(constraint, sa.ForeignKeyConstraint):
        return ("f", *constraint.column_keys, "->", constraint.referred_table.name)
    if isinstance(constraint, sa.UniqueConstraint):
        return ("u", *sorted(column.name for column in constraint.columns))
    return None


def _adopt_constraint_names(connection: sa.Connection) -> None:
    """Rename constraints Postgres named itself (`teachers_email_key`) to the
    names the baseline gives them (`uq_teachers_email`), so later migrations
    can refer to them by name."""
    inspector = sa.inspect(connection)
    quote = connection.dialect.identifier_preparer.quote
    for table in Base.metadata.sorted_tables:
        existing: dict[tuple[str, ...], str | None] = {
            ("p",): inspector.get_pk_constraint(table.name)["name"]
        }
        for fk in inspector.get_foreign_keys(table.name):
            existing[("f", *fk["constrained_columns"], "->", fk["referred_table"])] = fk["name"]
        for unique in inspector.get_unique_constraints(table.name):
            existing[("u", *sorted(unique["column_names"]))] = unique["name"]
        for constraint in table.constraints:
            key = _constraint_key(constraint)
            old = existing.get(key) if key else None
            if old and isinstance(constraint.name, str) and old != constraint.name:
                connection.execute(
                    sa.text(
                        f"ALTER TABLE {quote(table.name)} "
                        f"RENAME CONSTRAINT {quote(old)} TO {quote(constraint.name)}"
                    )
                )


def migrate(engine: sa.Engine) -> None:
    """Upgrade to the latest revision.

    A database created by `create_all` before migrations existed is adopted:
    its constraints are renamed to the baseline's names and it is stamped at
    the baseline if it then matches the models exactly. Otherwise it is
    refused, since its differences would make later migrations fail; the
    renames are rolled back with it.
    """
    config = Config()
    config.set_main_option("script_location", "unisubmit:migrations")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        tables = set(sa.inspect(connection).get_table_names())
        if tables and "alembic_version" not in tables:
            _adopt_constraint_names(connection)
            differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            if differences:
                listed = "\n".join(f"  {difference}" for difference in differences)
                raise SystemExit(
                    "This database was created without migrations and differs from the "
                    f"baseline schema, so it cannot be adopted:\n{listed}"
                )
            command.stamp(config, BASELINE)
            print(f"Adopted the existing schema at revision {BASELINE}.")
        command.upgrade(config, "head")
    print("The database schema is up to date.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unisubmit", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="create or upgrade the database schema")
    commands.add_parser("seed-demo", help="create the sample faculty, programs and supervisors")
    admin = commands.add_parser("create-super-admin", help="create a Super Admin account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", default="System Super Admin")
    args = parser.parse_args(argv)

    engine = create_db_engine(str(get_settings().database_url))
    try:
        if args.command == "migrate":
            migrate(engine)
            return 0
        with make_session_factory(engine)() as db:
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
                create_super_admin(db, args.name, args.email, password)
            except ValueError as exc:
                print(exc, file=sys.stderr)
                return 1
            print(f"Super Admin {args.email.strip().lower()} created.")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
