"""SQLAlchemy engine and session construction helpers."""

from flask import current_app
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def create_database_engine(database_url: str, *, in_memory=False) -> Engine:
    """Create an engine without creating or migrating database tables."""
    options = {"pool_pre_ping": True}
    if in_memory:
        options.update(connect_args={"check_same_thread": False}, poolclass=StaticPool)
    engine = create_engine(database_url, **options)
    if database_url.startswith("sqlite:"):
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create sessions bound to the supplied engine."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def initialize_database(app) -> None:
    """Attach the configured engine and session factory to the Flask app."""
    database_url = app.config.get("PORTAL_DATABASE_URL")
    in_memory = bool(app.config.get("PORTAL_DEV_MODE"))
    if in_memory:
        database_url = "sqlite:///:memory:"
    if not database_url:
        app.extensions["portal_database"] = None
        app.extensions["portal_session_factory"] = None
        return

    engine = create_database_engine(database_url, in_memory=in_memory)
    app.extensions["portal_database"] = engine
    app.extensions["portal_session_factory"] = create_session_factory(engine)


def get_session_factory() -> sessionmaker[Session]:
    """Return the application's configured SQLAlchemy session factory."""
    factory = current_app.extensions.get("portal_session_factory")
    if factory is None:
        raise RuntimeError("Portal database is not configured")
    return factory
