"""HTTP integration tests for the authenticated management API."""

import json

import pytest
from rpt_common.models import Entity, EntitySuperAdmin, NodeCredential
from sqlalchemy import select

from portal_backend.models import PortalEntityMember, PortalEntityProfile, Repeater, Site


def auth(user="alice"):
    return {"Authorization": f"Bearer {user}"}


def test_health_is_public_and_api_routes_require_bearer_token(client):
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json == {"status": "ok"}

    missing = client.get("/api/v1/session")
    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"].startswith("Bearer")
    assert client.get("/api/v1/session", headers=auth("invalid")).status_code == 401
    assert client.get("/api/v1/session", headers=auth("unavailable")).status_code == 503
    assert client.get("/api/v1/orgs").status_code == 401
    assert client.get("/api/v1/entities", headers=auth()).status_code == 404


def test_management_routes_report_database_unavailable(app):
    app.extensions["portal_session_factory"] = None
    response = app.test_client().get("/api/v1/sites", headers=auth())
    assert response.status_code == 503
    assert response.json["error"] == "database_unavailable"


def test_session_returns_only_public_identity_claims_and_disables_caching(client):
    response = client.get("/api/v1/session", headers=auth())
    assert response.status_code == 200
    assert response.json["user"] == {
        "id": "alice",
        "username": "alice",
        "name": "Alice",
        "email": None,
        "is_system_super_admin": False,
    }
    assert response.headers["Cache-Control"] == "no-store"
    root = client.get("/api/v1/session", headers=auth("root"))
    assert root.status_code == 200
    assert root.json["user"]["is_system_super_admin"] is True


def test_staff_can_manage_repeaters_but_cannot_edit_site(client, session_factory):
    with session_factory() as session, session.begin():
        entity = Entity(name="Staff Entity", entity_type="club")
        entity.assign_owner("owner")
        session.add(entity)
        session.flush()
        entity_id = entity.id
        site = Site(
            country_code="CA", site_code="25", site_id="25111", name="Staff Site",
            owner_entity_id=entity_id,
        )
        session.add(site)
        session.flush()
        site_id = site.id
        session.add_all([
            PortalEntityMember(entity_id=entity_id, user_id="staff", role="Staff"),
            PortalEntityMember(entity_id=entity_id, user_id="viewer", role="Viewer"),
            Repeater(node_id=25111, site_id=site_id, call_sign="VE3ABC"),
            NodeCredential(node_id=25111, password="test-secret", enabled=False),
        ])

    updated = client.patch(
        "/api/v1/repeaters/25111", headers=auth("staff"), json={"frequency": "146.520 MHz"}
    )
    assert updated.status_code == 200, updated.json
    assert updated.json["frequency"] == "146.520 MHz"
    denied_site_edit = client.patch(
        f"/api/v1/sites/{site_id}", headers=auth("staff"), json={"name": "Changed"}
    )
    assert denied_site_edit.status_code == 404
    denied_viewer_edit = client.patch(
        "/api/v1/repeaters/25111", headers=auth("viewer"), json={"frequency": "145 MHz"}
    )
    assert denied_viewer_edit.status_code == 404


def test_user_search_matches_name_or_call_sign(client, session_factory):
    from portal_backend.models import PortalUser

    with session_factory() as session, session.begin():
        session.add_all([
            PortalUser(subject="u1", display_name="Morgan Example", call_signs=["VE3MOR", "VA3M"]),
            PortalUser(subject="u2", display_name="Other Operator", call_signs=["VE3OTH"]),
        ])
    by_name = client.get("/api/v1/users?q=Morgan", headers=auth())
    by_call_sign = client.get("/api/v1/users?q=VE3MOR", headers=auth())
    assert [item["subject"] for item in by_name.json["items"]] == ["u1"]
    assert [item["subject"] for item in by_call_sign.json["items"]] == ["u1"]


def test_entity_create_read_update_and_membership_visibility(client, session_factory):
    created = client.post(
        "/api/v1/orgs",
        headers=auth(),
        json={
            "name": "Toronto Radio Club",
            "description": "A local club",
            "entity_type": "club",
            "website": "club.example",
            "primary_call_sign": "ve3abc",
            "aka_call_signs": ["VE3DEF"],
            "members": [{"user_id": "member", "role": "Viewer"}],
        },
    )
    assert created.status_code == 201
    entity = created.json
    entity_id = entity["id"]
    assert entity["owner_user_id"] == "alice"
    assert entity["max_sites"] == 5
    assert entity["can_manage_site_limit"] is False
    assert entity["primary_call_sign"] == "VE3ABC"
    assert entity["aka_call_signs"] == ["VE3DEF"]
    assert entity["website"] == "https://club.example"
    assert entity["members"] == [{
        "user_id": "member", "display_name": "member", "call_signs": [], "role": "Viewer"
    }]

    as_member = client.get(f"/api/v1/orgs/{entity_id}", headers=auth("member"))
    assert as_member.status_code == 200
    assert as_member.json["can_manage"] is False
    member_list = client.get("/api/v1/orgs", headers=auth("member"))
    assert member_list.json["items"][0]["id"] == entity_id

    updated = client.patch(
        f"/api/v1/orgs/{entity_id}",
        headers=auth(),
        json={
            "name": "Updated Club",
            "members": [{"user_id": "member", "role": "Staff"}],
            "aka_call_signs": [],
        },
    )
    assert updated.status_code == 200, updated.json
    assert updated.json["name"] == "Updated Club"
    assert updated.json["aka_call_signs"] == []
    assert updated.json["members"] == [{
        "user_id": "member", "display_name": "member", "call_signs": [], "role": "Staff"
    }]

    with session_factory() as session:
        persisted = session.get(Entity, entity_id)
        assert persisted.owner_user_id == "alice"
        assert persisted.call_signs[0].call_sign == "VE3ABC"


def test_entity_authorization_and_input_errors(client):
    denied_system_entity = client.post(
        "/api/v1/orgs",
        headers=auth(),
        json={"name": "System", "entity_type": "organization", "system_managed": True},
    )
    assert denied_system_entity.status_code == 403

    invalid_sign = client.post(
        "/api/v1/orgs",
        headers=auth(),
        json={"name": "Bad Club", "entity_type": "club", "primary_call_sign": "bad"},
    )
    assert invalid_sign.status_code == 422

    owner_as_member = client.post(
        "/api/v1/orgs",
        headers=auth(),
        json={
            "name": "Bad Membership",
            "entity_type": "club",
            "members": [{"user_id": "alice", "role": "Staff"}],
        },
    )
    assert owner_as_member.status_code == 400
    assert owner_as_member.json["error"] == "entity_owner_is_not_a_member"

    regular_limit = client.post(
        "/api/v1/orgs",
        headers=auth(),
        json={"name": "Limited Club", "entity_type": "club", "max_sites": 2},
    )
    assert regular_limit.status_code == 403
    assert regular_limit.json["error"] == "system_super_admin_required"

    root_created = client.post(
        "/api/v1/orgs",
        headers=auth("root"),
        json={
            "name": "System Entity",
            "entity_type": "organization",
            "system_managed": True,
            "max_sites": 2,
        },
    )
    assert root_created.status_code == 201
    assert root_created.json["is_system_maintained"] is True
    assert root_created.json["max_sites"] == 2
    assert root_created.json["can_manage_site_limit"] is True


def test_only_system_super_admin_can_edit_entity_site_limit(client, session_factory):
    created = client.post(
        "/api/v1/orgs",
        headers=auth(),
        json={"name": "Owner Managed Club", "entity_type": "club"},
    )
    entity_id = created.json["id"]

    denied = client.patch(
        f"/api/v1/orgs/{entity_id}", headers=auth(), json={"max_sites": 8}
    )
    assert denied.status_code == 403
    assert denied.json["error"] == "system_super_admin_required"

    changed = client.patch(
        f"/api/v1/orgs/{entity_id}", headers=auth("root"), json={"max_sites": 8}
    )
    assert changed.status_code == 200
    assert changed.json["max_sites"] == 8
    assert changed.json["can_manage_site_limit"] is True
    with session_factory() as session:
        assert session.get(PortalEntityProfile, entity_id).max_sites == 8


def test_entities_are_hidden_from_non_members(client, session_factory):
    with session_factory() as session, session.begin():
        entity = Entity(name="Private Entity", entity_type="club")
        entity.assign_owner("alice")
        session.add(entity)
        session.flush()
        entity_id = entity.id

    assert client.get(f"/api/v1/orgs/{entity_id}", headers=auth("bob")).status_code == 404
    empty_entities = client.get("/api/v1/orgs", headers=auth("bob")).json
    assert empty_entities["items"] == []
    assert empty_entities["pagination"]["total"] == 0


def test_site_location_resolution_endpoint_returns_country_and_site_codes(
    client, monkeypatch
):
    monkeypatch.setattr(
        "portal_backend.routes.site_locations.resolve_site_location",
        lambda grid, latitude=None, longitude=None: ("FN03", 43.5, -79.0, "CA"),
    )
    response = client.post(
        "/api/v1/site-locations/resolve", headers=auth(), json={"grid_square": "FN03"}
    )
    assert response.status_code == 200
    assert response.json["country_code"] == "CA"
    assert response.json["site_codes"] == [
        {"site_code": "25", "available_numbers": 1000},
        {"site_code": "26", "available_numbers": 1000},
    ]


def test_site_create_validation_allocation_and_owner_policy(client, monkeypatch, session_factory):
    monkeypatch.setattr(
        "portal_backend.routes.sites.resolve_site_location",
        lambda grid, latitude=None, longitude=None: ("FN03", 43.5, -79.0, "CA"),
    )
    monkeypatch.setattr("portal_backend.utils.site_ids.secrets.randbelow", lambda limit: 15)

    missing_code = client.post(
        "/api/v1/sites", headers=auth(), json={"name": "New Site", "grid_square": "FN03"}
    )
    assert missing_code.status_code == 400
    assert missing_code.json["error"] == "site_code_selection_required"

    invalid_body = client.post(
        "/api/v1/sites", headers=auth(), json={"name": "New Site", "grid_square": "BAD"}
    )
    assert invalid_body.status_code == 422

    created = client.post(
        "/api/v1/sites",
        headers=auth(),
        json={
            "name": "New Site",
            "grid_square": "FN03",
            "site_code": "25",
            "latitude": 43.5,
            "longitude": -79.0,
        },
    )
    assert created.status_code == 201
    assert created.json["site_id"] == "25015"
    assert created.json["owner_type"] == "user"
    assert created.json["owner_subject"] == "alice"

    site_id = created.json["id"]
    assert client.get(f"/api/v1/sites/{site_id}", headers=auth("bob")).status_code == 404
    assert client.patch(
        f"/api/v1/sites/{site_id}", headers=auth("bob"), json={"name": "No Access"}
    ).status_code == 404
    assert client.patch(
        f"/api/v1/sites/{site_id}", headers=auth(), json={"name": "Renamed Site"}
    ).json["name"] == "Renamed Site"
    with session_factory() as session:
        assert session.scalar(select(Site.site_id).where(Site.id == site_id)) == "25015"


def test_site_id_edits_are_system_admin_only_and_keep_allocations_consistent(
    client, session_factory, site_factory
):
    from portal_backend.models import SiteIdAllocation

    site_pk = site_factory(owner="alice", site_id="25001")
    other_site_pk = site_factory(owner="bob", site_id="25002")

    denied = client.patch(
        f"/api/v1/sites/{site_pk}", headers=auth(), json={"site_id": "25003"}
    )
    assert denied.status_code == 403

    updated = client.patch(
        f"/api/v1/sites/{site_pk}", headers=auth("root"), json={"site_id": "25003"}
    )
    assert updated.status_code == 200
    assert updated.json["site_id"] == "25003"

    duplicate = client.patch(
        f"/api/v1/sites/{site_pk}", headers=auth("root"), json={"site_id": "25002"}
    )
    assert duplicate.status_code == 409
    with session_factory() as session:
        assert session.get(SiteIdAllocation, "25003").site_pk == site_pk
        assert session.get(SiteIdAllocation, "25002") is None
        assert session.get(Site, other_site_pk).site_id == "25002"
        assert session.get(Site, site_pk).site_id == "25003"


def test_system_admin_cannot_change_site_identity_after_repeater_creation(
    client, session_factory, site_factory
):
    from portal_backend.models import Repeater

    site_pk = site_factory(owner="alice", site_id="25050")
    with session_factory() as session, session.begin():
        session.add(Repeater(node_id=250500, site_id=site_pk, call_sign="VE3ABC"))

    response = client.patch(
        f"/api/v1/sites/{site_pk}", headers=auth("root"), json={"site_id": "26050"}
    )
    assert response.status_code == 409
    assert response.json["error"] == "site_id_locked_after_repeater_creation"
    with session_factory() as session:
        assert session.get(Site, site_pk).site_id == "25050"


def test_system_admin_can_reassign_a_previously_reserved_unassigned_site_id(
    client, session_factory, site_factory
):
    from portal_backend.models import SiteIdAllocation

    site_pk = site_factory(owner="alice", site_id="25010")
    with session_factory() as session, session.begin():
        session.add_all(
            [
                SiteIdAllocation(
                    site_id="25010", country_code="CA", site_code="25", site_pk=site_pk
                ),
                SiteIdAllocation(
                    site_id="26999", country_code="CA", site_code="26", site_pk=None
                ),
            ]
        )

    updated = client.patch(
        f"/api/v1/sites/{site_pk}", headers=auth("root"), json={"site_id": "26999"}
    )
    assert updated.status_code == 200
    assert updated.json["site_id"] == "26999"

    with session_factory() as session:
        assert session.get(Site, site_pk).site_id == "26999"
        assert session.get(SiteIdAllocation, "25010").site_pk is None
        assert session.get(SiteIdAllocation, "26999").site_pk == site_pk
        assert session.get(SiteIdAllocation, "26999").site_code == "26"


def test_site_code_and_explicit_id_change_does_not_allocate_intermediate_id(
    client, session_factory, site_factory, monkeypatch
):
    from portal_backend.models import SiteIdAllocation

    monkeypatch.setattr("portal_backend.utils.site_ids.secrets.randbelow", lambda limit: 42)
    site_pk = site_factory(owner="alice", site_id="25010")
    response = client.patch(
        f"/api/v1/sites/{site_pk}",
        headers=auth("root"),
        json={"site_code": "26", "site_id": "26999"},
    )
    assert response.status_code == 200
    with session_factory() as session:
        assert session.get(SiteIdAllocation, "26042") is None
        assert session.get(SiteIdAllocation, "26999").site_pk == site_pk


def test_explicit_id_reuses_reservation_when_site_code_capacity_is_exhausted(
    client, session_factory, site_factory
):
    from portal_backend.models import SiteIdAllocation

    site_pk = site_factory(owner="alice", site_id="25010")
    with session_factory() as session, session.begin():
        session.add_all(
            [
                SiteIdAllocation(
                    site_id=f"26{suffix:03d}",
                    country_code="CA",
                    site_code="26",
                    site_pk=None,
                )
                for suffix in range(1000)
            ]
        )

    response = client.patch(
        f"/api/v1/sites/{site_pk}",
        headers=auth("root"),
        json={"site_code": "26", "site_id": "26999"},
    )
    assert response.status_code == 200
    with session_factory() as session:
        assert session.get(SiteIdAllocation, "26999").site_pk == site_pk


def test_site_id_zero_is_rejected_without_changing_reservation(client, session_factory, site_factory):
    from portal_backend.models import SiteIdAllocation

    site_pk = site_factory(owner="alice", site_id="25020")
    with session_factory() as session, session.begin():
        session.add(
            SiteIdAllocation(
                site_id="25020", country_code="CA", site_code="25", site_pk=site_pk
            )
        )
    response = client.patch(
        f"/api/v1/sites/{site_pk}", headers=auth("root"), json={"site_id": "00000"}
    )
    assert response.status_code == 422
    with session_factory() as session:
        assert session.get(Site, site_pk).site_id == "25020"
        assert session.get(SiteIdAllocation, "25020").site_pk == site_pk


@pytest.mark.parametrize("site_id", ["1234", "12A45", "１２３４５"])
def test_site_id_edit_rejects_invalid_values(client, site_factory, site_id):
    site_pk = site_factory(owner="alice", site_id="25020")
    response = client.patch(
        f"/api/v1/sites/{site_pk}", headers=auth("root"), json={"site_id": site_id}
    )
    assert response.status_code == 422


def test_site_repeaters_and_repeaters_list_are_owner_scoped(client, site_factory):
    own_site_id = site_factory(owner="alice")
    other_site_id = site_factory(owner="bob", site_id="26000")
    own = client.get(f"/api/v1/sites/{own_site_id}/repeaters", headers=auth())
    hidden = client.get(f"/api/v1/sites/{other_site_id}/repeaters", headers=auth())
    assert own.status_code == 200 and own.json["items"] == []
    assert own.json["pagination"]["total"] == 0
    assert hidden.status_code == 404


def test_repeater_create_toggle_and_rotate_keeps_secrets_private(
    client, site_factory, session_factory
):
    site_id = site_factory(numbering_mode="extended")
    first = client.post(
        f"/api/v1/sites/{site_id}/repeaters",
        headers=auth(),
        json={"call_sign": "ve3abc", "frequency": "146.520", "tone": "100.0"},
    )
    second = client.post(
        f"/api/v1/sites/{site_id}/repeaters", headers=auth(), json={"call_sign": "VE3DEF"}
    )
    assert first.status_code == second.status_code == 201
    first_id = first.json["repeater"]["node_id"]
    assert first.json["repeater"]["call_sign"] == "VE3ABC"
    assert first.json["repeater"]["enabled"] is False
    assert first.json["registration_secret"] != second.json["registration_secret"]
    assert first.headers["Cache-Control"] == "no-store"

    updated = client.patch(
        f"/api/v1/repeaters/{first_id}", headers=auth(), json={"enabled": True}
    )
    assert updated.status_code == 200
    assert updated.json["enabled"] is True
    with session_factory() as session:
        assert session.get(NodeCredential, first_id).enabled is True

    rotated = client.post(
        f"/api/v1/repeaters/{first_id}/credential/rotate", headers=auth()
    )
    assert rotated.status_code == 200
    assert rotated.headers["Cache-Control"] == "no-store"
    assert rotated.json["registration_secret"] != first.json["registration_secret"]
    listed = client.get("/api/v1/repeaters", headers=auth())
    assert listed.status_code == 200
    assert "registration_secret" not in json.dumps(listed.json)
    empty_repeaters = client.get("/api/v1/repeaters", headers=auth("bob")).json
    assert empty_repeaters["items"] == []
    assert empty_repeaters["pagination"]["total"] == 0


def test_extended_repeater_skips_node_ids_occupied_by_other_sites(
    client, site_factory, session_factory
):
    site_id = site_factory(numbering_mode="extended", site_id="02502")
    other_site_id = site_factory(site_id="26000")
    with session_factory() as session, session.begin():
        session.add(Repeater(node_id=25020, site_id=other_site_id, call_sign="VE3XYZ"))
        session.add(NodeCredential(node_id=25020, password="existing-secret", enabled=True))

    response = client.post(
        f"/api/v1/sites/{site_id}/repeaters", headers=auth(), json={"call_sign": "VE3ABC"}
    )

    assert response.status_code == 201
    assert response.json["repeater"]["node_id"] == 25021


def test_resource_lists_search_and_paginate_on_the_server(client, session_factory, site_factory):
    with session_factory() as session, session.begin():
        for name in ("Searchable Club North", "Searchable Club South"):
            entity = Entity(name=name, entity_type="club")
            entity.assign_owner("alice")
            session.add(entity)
    entity_page = client.get(
        "/api/v1/orgs?q=Searchable&page=1&page_size=1", headers=auth()
    )
    assert len(entity_page.json["items"]) == 1
    assert entity_page.json["pagination"] == {"page": 1, "page_size": 1, "total": 2, "pages": 2}

    site_factory(owner="alice", site_id="27000")
    site_factory(owner="alice", site_id="27001")
    site_page = client.get("/api/v1/sites?q=Test&page_size=1", headers=auth())
    assert len(site_page.json["items"]) == 1
    assert site_page.json["pagination"]["total"] == 2

    repeater_site = site_factory(owner="alice", site_id="27002", numbering_mode="extended")
    client.post(f"/api/v1/sites/{repeater_site}/repeaters", headers=auth(), json={})
    repeater_site = site_factory(owner="alice", site_id="27003", numbering_mode="extended")
    client.post(f"/api/v1/sites/{repeater_site}/repeaters", headers=auth(), json={})
    repeater_page = client.get("/api/v1/repeaters?q=27&page_size=1", headers=auth())
    assert len(repeater_page.json["items"]) == 1
    assert repeater_page.json["pagination"]["total"] == 2


def test_repeater_rejects_invalid_call_sign_and_missing_site_id(client, site_factory):
    site_id = site_factory(site_id=None)
    invalid = client.post(
        f"/api/v1/sites/{site_id}/repeaters", headers=auth(), json={"call_sign": "bad"}
    )
    assert invalid.status_code == 422
    missing_id = client.post(
        f"/api/v1/sites/{site_id}/repeaters", headers=auth(), json={}
    )
    assert missing_id.status_code == 409
    assert missing_id.json["error"] == "site_id_required"


def test_repeater_updates_are_hidden_from_non_owner(client, site_factory):
    site_id = site_factory(owner="alice")
    create = client.post(
        f"/api/v1/sites/{site_id}/repeaters", headers=auth(), json={}
    )
    node_id = create.json["repeater"]["node_id"]
    hidden = client.patch(
        f"/api/v1/repeaters/{node_id}", headers=auth("bob"), json={"enabled": True}
    )
    assert hidden.status_code == 404


def test_entity_super_admin_can_manage_entity_owned_site(client, session_factory, monkeypatch):
    monkeypatch.setattr(
        "portal_backend.routes.sites.resolve_site_location",
        lambda grid, latitude=None, longitude=None: ("FN03", 43.5, -79.0, "CA"),
    )
    with session_factory() as session, session.begin():
        entity = Entity(name="Administered Entity", entity_type="club")
        entity.assign_owner("owner")
        session.add(entity)
        session.flush()
        entity_id = entity.id
        session.add(EntitySuperAdmin(entity_id=entity_id, user_id="delegate"))
        session.add(PortalEntityProfile(entity_id=entity_id, max_sites=3))

    response = client.post(
        "/api/v1/sites",
        headers=auth("delegate"),
        json={
            "name": "Entity Site",
            "grid_square": "FN03",
            "site_code": "25",
            "owner_entity_id": entity_id,
        },
    )
    assert response.status_code == 201
    assert response.json["owner_entity_id"] == entity_id
    assert response.json["owner_type"] == "entity"

    viewer = client.get(f"/api/v1/sites/{response.json['id']}", headers=auth("viewer"))
    assert viewer.status_code == 404
