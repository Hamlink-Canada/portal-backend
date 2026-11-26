"""Apply Portal schema migrations explicitly with a writable database URL."""

import argparse
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


def _database_url(url_file=None, environ=None):
    """Resolve explicit migration credentials, then the application URL."""
    environment = os.environ if environ is None else environ
    if url_file:
        value = Path(url_file).read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError("The migration database URL file is empty")
        return value

    migration_url = environment.get("PORTAL_MIGRATION_DATABASE_URL", "").strip()
    if migration_url:
        return migration_url

    application_url = environment.get("PORTAL_DATABASE_URL", "").strip()
    if application_url:
        return application_url

    raise ValueError(
        "Set PORTAL_MIGRATION_DATABASE_URL or PORTAL_DATABASE_URL, "
        "or provide --url-file"
    )


def upgrade(database_url, revision="head"):
    """Run one Alembic upgrade with the supplied URL without logging secrets."""
    migrations = Path.cwd() / "migrations"
    if not migrations.is_dir():
        raise RuntimeError("Run portal-migrate from the Portal backend project or image")

    config = Config()
    config.set_main_option("script_location", str(migrations))
    engine = create_engine(database_url, poolclass=NullPool, hide_parameters=True)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, revision)
    finally:
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply Portal schema migrations explicitly")
    parser.add_argument("action", choices=["upgrade"])
    parser.add_argument("revision", nargs="?", default="head")
    parser.add_argument("--url-file", help="Read the writable database URL from a mounted secret")
    args = parser.parse_args(argv)
    try:
        upgrade(_database_url(args.url_file), args.revision)
    except Exception:
        parser.exit(1, "Migration failed; check the writable connection and schema revision.\n")


if __name__ == "__main__":
    main()
