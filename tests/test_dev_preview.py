"""Backend-backed development preview isolation and token tests."""

import pytest
from rpt_common.models import NodeCredential
from sqlalchemy import select

from portal_backend.app import create_app
from portal_backend.models import PortalUser


def preview_app():
    return create_app(
        {
            "TESTING": True,
            "PORTAL_DEV_MODE": True,
            "PORTAL_ENVIRONMENT": "development",
            "PORTAL_DATABASE_URL": "sqlite:///must-be-ignored.db",
        }
    )


def test_preview_requires_explicit_development_environment():
    with pytest.raises(ValueError, match="PORTAL_ENVIRONMENT=development"):
        create_app({"PORTAL_DEV_MODE": True, "PORTAL_ENVIRONMENT": "production"})


def test_preview_routes_are_not_registered_when_disabled(client):
    assert client.get("/api/v1/dev-preview/users").status_code == 404


def test_preview_refuses_non_debug_production_app():
    with pytest.raises(RuntimeError, match="debug or test Flask app"):
        create_app({"PORTAL_DEV_MODE": True, "PORTAL_ENVIRONMENT": "development"})


def test_mock_oidc_selects_seeded_user_and_api_validates_signed_token():
    app = preview_app()
    client = app.test_client()
    assert app.config["PORTAL_DATABASE_URL"] == "sqlite:///:memory:"

    users = client.get("/api/v1/dev-preview/users")
    assert users.status_code == 200
    assert len(users.json["items"]) == 4
    admin = next(item for item in users.json["items"] if item["is_system_super_admin"])

    token_result = client.post("/api/v1/dev-preview/token", json={"user_id": admin["id"]})
    assert token_result.status_code == 200
    headers = {"Authorization": f"Bearer {token_result.json['access_token']}"}
    identity = client.get("/api/v1/session", headers=headers)
    assert identity.status_code == 200
    assert identity.json["user"]["id"] == admin["id"]
    assert identity.json["user"]["is_system_super_admin"] is True

    entities = client.get("/api/v1/orgs?page=1&page_size=10", headers=headers)
    assert entities.status_code == 200
    assert entities.json["pagination"]["total"] == 12


def test_preview_repeaters_have_unique_credentials_that_can_rotate():
    app = preview_app()
    client = app.test_client()
    token = client.post("/api/v1/dev-preview/token", json={"user_id": "dev-admin-001"}).json[
        "access_token"
    ]
    headers = {"Authorization": f"Bearer {token}"}

    with app.app_context():
        with app.extensions["portal_session_factory"]() as session:
            credentials = session.scalars(select(NodeCredential)).all()
            assert len(credentials) == 18
            assert len({credential.password for credential in credentials}) == 18
            original = {credential.node_id: credential.password for credential in credentials}

    repeaters = client.get("/api/v1/repeaters?page=1&page_size=50", headers=headers)
    assert repeaters.status_code == 200
    node_id = repeaters.json["items"][0]["node_id"]
    rotated = client.post(f"/api/v1/repeaters/{node_id}/credential/rotate", headers=headers)
    assert rotated.status_code == 200
    assert rotated.headers["Cache-Control"] == "no-store"
    assert rotated.json["registration_secret"] != original[node_id]

    with app.app_context():
        with app.extensions["portal_session_factory"]() as session:
            credential = session.get(NodeCredential, node_id)
            assert credential.password == rotated.json["registration_secret"]
    app.extensions["portal_database"].dispose()


def test_preview_changes_are_in_memory_and_reset_on_restart():
    first_app = preview_app()
    client = first_app.test_client()
    token = client.post("/api/v1/dev-preview/token", json={"user_id": "dev-admin-001"}).json[
        "access_token"
    ]
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/v1/orgs",
        headers=headers,
        json={"name": "Created Until Restart", "entity_type": "club"},
    )
    assert created.status_code == 201
    entity_id = created.json["id"]
    updated = client.patch(
        f"/api/v1/orgs/{entity_id}",
        headers=headers,
        json={"description": "An in-memory edit."},
    )
    assert updated.status_code == 200
    assert updated.json["description"] == "An in-memory edit."

    with first_app.app_context():
        with first_app.extensions["portal_session_factory"]() as session:
            user = session.get(PortalUser, "dev-user-001")
            assert user.display_name == "Portal Reviewer"

    second_app = preview_app()
    with second_app.app_context():
        with second_app.extensions["portal_session_factory"]() as session:
            user = session.scalar(select(PortalUser).where(PortalUser.subject == "dev-user-001"))
            assert user.display_name == "Portal Reviewer"
            from rpt_common.models import Entity

            assert session.get(Entity, entity_id) is None
    first_app.extensions["portal_database"].dispose()
    second_app.extensions["portal_database"].dispose()


def test_preview_users_have_distinct_entity_and_site_visibility():
    app = preview_app()
    client = app.test_client()
    visible_organizations = {}
    visible_sites = {}
    visible_repeaters = {}
    for user_id in ("dev-user-001", "dev-user-002", "dev-user-003", "dev-admin-001"):
        token = client.post("/api/v1/dev-preview/token", json={"user_id": user_id}).json[
            "access_token"
        ]
        headers = {"Authorization": f"Bearer {token}"}
        entities = client.get("/api/v1/orgs?page=1&page_size=50", headers=headers).json["items"]
        sites = client.get("/api/v1/sites?page=1&page_size=50", headers=headers).json["items"]
        repeaters = client.get("/api/v1/repeaters?page=1&page_size=50", headers=headers).json[
            "items"
        ]
        visible_organizations[user_id] = {item["name"] for item in entities}
        visible_sites[user_id] = {item["name"] for item in sites}
        visible_repeaters[user_id] = {item["site_name"] for item in repeaters}

    assert "Example Radio Association" in visible_organizations["dev-user-001"]
    assert "Ottawa Valley Amateur Radio Club" not in visible_organizations["dev-user-001"]
    assert "Ottawa Valley Amateur Radio Club" in visible_organizations["dev-user-002"]
    assert "North American Repeater Network" in visible_organizations["dev-user-003"]
    assert len(visible_organizations["dev-admin-001"]) == 12
    assert "Morgan's Ridge Site" in visible_sites["dev-user-002"]
    assert "Casey's Personal Site" not in visible_sites["dev-user-002"]
    assert "Casey's Personal Site" in visible_repeaters["dev-user-003"]
    assert "Casey's Personal Site" not in visible_repeaters["dev-user-002"]
    assert len(visible_sites["dev-admin-001"]) == 18
    assert len(visible_organizations["dev-admin-001"]) > len(visible_organizations["dev-user-001"])
    app.extensions["portal_database"].dispose()
