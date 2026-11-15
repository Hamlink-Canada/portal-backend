"""Shared fixtures for Portal API and persistence tests."""

import json

import pytest
from rpt_common.models import Base
from sqlalchemy import event

from portal_backend.app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Create an isolated SQLite-backed Flask app for each test."""
    mapping_path = tmp_path / "sitecodes.json"
    mapping_path.write_text(
        json.dumps({"CA": {"sitecodes": ["25", "26"]}, "US": {"sitecodes": ["27"]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PORTAL_CONFIG_DIR", str(tmp_path))
    database_path = tmp_path / "portal.sqlite"
    application = create_app(
        {
            "TESTING": True,
            "PORTAL_DATABASE_URL": f"sqlite:///{database_path}",
            "PORTAL_SITE_CODES_FILE": str(mapping_path),
            "PORTAL_SYSTEM_SUPER_ADMIN_CLAIM_PATH": "realm_access.roles",
            "PORTAL_SYSTEM_SUPER_ADMIN_CLAIM_VALUE": "System Super Admin",
            "PORTAL_REGISTRATION_LIFETIME_SECONDS": 120,
        }
    )
    engine = application.extensions["portal_database"]

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(connection, record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)

    def mock_validate(token):
        if token == "invalid":
            raise ValueError("invalid token")
        if token == "unavailable":
            from portal_backend.auth import JwksUnavailable

            raise JwksUnavailable("mock provider unavailable")
        claims = {"sub": token, "preferred_username": token, "name": token.title()}
        if token == "root":
            claims["realm_access"] = {"roles": ["System Super Admin"]}
        return claims

    monkeypatch.setattr("portal_backend.auth.validate_access_token", mock_validate)
    yield application
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def session_factory(app):
    return app.extensions["portal_session_factory"]


@pytest.fixture
def site_factory(session_factory):
    """Create a Site directly for tests that focus on another resource."""
    from portal_backend.models import Site

    def create_site(*, owner="alice", numbering_mode="short", site_id="25000"):
        with session_factory() as session, session.begin():
            site = Site(
                country_code="CA",
                site_code="25",
                site_id=site_id,
                name=f"Test Site {site_id}",
                grid_square="FN03",
                latitude=43.5,
                longitude=-79.0,
                owner_subject=owner,
                numbering_mode=numbering_mode,
            )
            session.add(site)
            session.flush()
            return site.id

    return create_site
