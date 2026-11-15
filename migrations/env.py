"""Alembic environment for Portal-owned schema revisions."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from portal_backend.config import load_settings
from portal_backend.models import Base

config = context.config
target_metadata = Base.metadata
database_url = config.get_main_option("sqlalchemy.url") or load_settings().database_url
if not database_url and config.attributes.get("connection") is None:
    raise RuntimeError("PORTAL_DATABASE_URL must be set to run Portal migrations")


def run_migrations_offline():
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="portal_alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="portal_alembic_version",
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(database_url, poolclass=NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="portal_alembic_version",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
