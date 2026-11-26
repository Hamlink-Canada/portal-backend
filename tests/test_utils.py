"""Tests for Portal's geography, site allocation, auth policy, and freshness helpers."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from rpt_common.models import Entity, EntitySuperAdmin

from portal_backend.models import PortalEntityMember, Site, SiteIdAllocation
from portal_backend.registration import registration_freshness
from portal_backend.utils import geography
from portal_backend.utils.authorization import (
    can_manage_entity,
    can_manage_site,
    can_view_entity,
    can_view_site,
    is_system_super_admin,
    viewable_entity_ids,
)
from portal_backend.utils.site_codes import site_codes_for_country
from portal_backend.utils.site_ids import allocate_site_id, site_code_options


def test_grid_square_center_and_bounds():
    assert geography.grid_square_center("fn03") == ("FN03", 43.5, -79.0)
    grid, south, west, north, east = geography.grid_square_bounds("FN03")
    assert grid == "FN03"
    assert (south, west, north, east) == (43.0, -80.0, 44.0, -78.0)
    assert geography.grid_square_center("FN03AA")[0] == "FN03AA"


@pytest.mark.parametrize("grid", [None, "", "FN0", "ZZ99", "FN03AA1"])
def test_grid_square_center_rejects_invalid_locators(grid):
    with pytest.raises(ValueError):
        geography.grid_square_center(grid)


def test_location_resolution_uses_country_lookup_and_checks_grid_bounds(monkeypatch):
    class Resolver:
        def country_code(self, latitude, longitude):
            assert (latitude, longitude) == (43.5, -79.0)
            return "ca"

    monkeypatch.setattr(geography, "_country_resolver", lambda: Resolver())
    assert geography.resolve_site_location("FN03") == ("FN03", 43.5, -79.0, "CA")
    with pytest.raises(ValueError, match="inside the supplied grid"):
        geography.resolve_site_location("FN03", 47, -73)
    with pytest.raises(ValueError, match="provided together"):
        geography.resolve_site_location("FN03", 45.5, None)


def test_location_resolution_rejects_country_lookup_failure(monkeypatch):
    class Resolver:
        def country_code(self, latitude, longitude):
            return None

    monkeypatch.setattr(geography, "_country_resolver", lambda: Resolver())
    with pytest.raises(ValueError, match="could not determine a country"):
        geography.resolve_site_location("FN03")


def test_site_code_mapping_validation_and_fallback(tmp_path):
    mapping_path = tmp_path / "codes.json"
    mapping_path.write_text(json.dumps({"ca": {"sitecodes": ["25", "26"]}}), encoding="utf-8")
    assert site_codes_for_country("CA", mapping_path) == ("25", "26")
    assert site_codes_for_country("US", mapping_path) == ("25",)
    assert site_codes_for_country("CA", mapping_path) == ("25", "26")

    bad_mapping_path = tmp_path / "bad-codes.json"
    bad_mapping_path.write_text(
        json.dumps({"CA": {"sitecodes": ["25", "25"]}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="invalid"):
        site_codes_for_country("CA", bad_mapping_path)
    with pytest.raises(ValueError, match="invalid country"):
        site_codes_for_country("CAN", bad_mapping_path)


def test_allocation_reserves_random_id_and_does_not_reuse_deleted_id(
    session_factory, tmp_path, monkeypatch
):
    mapping_path = tmp_path / "codes.json"
    mapping_path.write_text(json.dumps({"CA": {"sitecodes": ["25"]}}), encoding="utf-8")
    monkeypatch.setattr("portal_backend.utils.site_ids.secrets.randbelow", lambda limit: 0)

    with session_factory() as session, session.begin():
        site = Site(country_code="CA", site_code="25", name="First", grid_square="FN03")
        session.add(site)
        session.flush()
        assert allocate_site_id(session, site, mapping_path=mapping_path) == "25000"
        first_id = site.id

    with session_factory() as session, session.begin():
        first = session.get(Site, first_id)
        session.delete(first)

    with session_factory() as session, session.begin():
        second = Site(country_code="CA", site_code="25", name="Second", grid_square="FN03")
        session.add(second)
        session.flush()
        assert allocate_site_id(session, second, mapping_path=mapping_path) == "25001"
        options = site_code_options(session, "CA", mapping_path)
        assert options == [{"site_code": "25", "available_numbers": 998}]
        assert session.get(SiteIdAllocation, "25000").site_pk is None


def test_authorization_uses_entity_ownership_memberships_and_super_admin(app, session_factory):
    with session_factory() as session, session.begin():
        owned = Entity(name="Owned", entity_type="club")
        owned.assign_owner("owner")
        member_entity = Entity(name="Member", entity_type="club")
        member_entity.assign_owner("other-owner")
        session.add_all([owned, member_entity])
        session.flush()
        owned_id, member_id = owned.id, member_entity.id
        session.add_all(
            [
                EntitySuperAdmin(entity_id=member_id, user_id="admin"),
                PortalEntityMember(entity_id=member_id, user_id="viewer", role="Viewer"),
            ]
        )
        owned_site = Site(
            country_code="CA", site_code="25", name="Owned Site", owner_subject="owner"
        )
        member_site = Site(
            country_code="CA", site_code="25", name="Member Site", owner_entity_id=member_id
        )
        session.add_all([owned_site, member_site])
        session.flush()
        owned_site_id, member_site_id = owned_site.id, member_site.id

    with app.app_context(), session_factory() as session:
        owner = {"sub": "owner"}
        admin = {"sub": "admin"}
        viewer = {"sub": "viewer"}
        root = {"sub": "root", "realm_access": {"roles": ["System Super Admin"]}}
        assert can_manage_entity(session, owned_id, owner)
        assert can_manage_entity(session, member_id, admin)
        assert can_view_entity(session, member_id, viewer)
        assert not can_manage_entity(session, member_id, viewer)
        assert viewable_entity_ids(session, "viewer") == {member_id}
        assert can_manage_site(session, session.get(Site, owned_site_id), owner)
        assert can_view_site(session, session.get(Site, member_site_id), viewer)
        assert not can_manage_site(session, session.get(Site, member_site_id), viewer)
        assert is_system_super_admin(root)
        assert can_manage_site(session, session.get(Site, member_site_id), root)


def test_system_super_admin_claim_matching_is_configurable(app):
    with app.app_context():
        assert is_system_super_admin({"realm_access": {"roles": ["System Super Admin"]}})
        assert not is_system_super_admin({"realm_access": {"roles": ["Other"]}})
        app.config["PORTAL_SYSTEM_SUPER_ADMIN_CLAIM_PATH"] = "groups.admin"
        app.config["PORTAL_SYSTEM_SUPER_ADMIN_CLAIM_VALUE"] = "yes"
        assert is_system_super_admin({"groups": {"admin": "YES"}})


@pytest.mark.parametrize(
    ("observed", "lifetime", "expected"),
    [
        (None, 120, "unknown"),
        (datetime(2025, 1, 1, tzinfo=timezone.utc), None, "unknown"),
        (datetime(2025, 1, 1, tzinfo=timezone.utc), 0, "unknown"),
        (datetime(2025, 1, 1, tzinfo=timezone.utc), 120, "current"),
        (datetime(2025, 1, 1, tzinfo=timezone.utc) - timedelta(seconds=120), 120, "stale"),
    ],
)
def test_registration_freshness(observed, lifetime, expected):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert registration_freshness(observed, lifetime_seconds=lifetime, now=now) == expected
