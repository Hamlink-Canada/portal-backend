"""Persistence constraints and the local Site ID maintenance operation."""

from types import SimpleNamespace

import pytest
from rpt_common.models import Entity, NodeCredential
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from portal_backend.admin_cli import clear_site_id, main
from portal_backend.models import (
    PortalEntityMember,
    PortalEntityProfile,
    Repeater,
    Site,
    SiteIdAllocation,
)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"country_code": "CAN"}, "invalid country"),
        ({"site_code": "99"}, "invalid site code"),
        ({"site_id": "12"}, "invalid Site ID"),
        ({"site_id": "00000"}, "invalid Site ID"),
        ({"name": "   "}, "invalid site name"),
        ({"numbering_mode": "unknown"}, "invalid site numbering mode"),
    ],
)
def test_site_model_validators(kwargs, message):
    values = {
        "country_code": "CA",
        "site_code": "25",
        "name": "Valid Site",
        **kwargs,
    }
    with pytest.raises(ValueError, match=message):
        Site(**values)


def test_repeater_model_validates_node_id_and_normalizes_call_sign():
    repeater = Repeater(node_id=1, site_id=1, call_sign="ve3abc")
    assert repeater.call_sign == "VE3ABC"
    with pytest.raises(ValueError, match="invalid Node ID"):
        Repeater(node_id=0, site_id=1)
    with pytest.raises(ValueError, match="invalid call sign"):
        Repeater(node_id=2, site_id=1, call_sign="bad")


def test_shared_and_portal_models_enforce_values(session_factory):
    with session_factory() as session, session.begin():
        with pytest.raises(ValueError, match="invalid registration password"):
            NodeCredential(node_id=1, password="space is not allowed")
        entity = Entity(name="Constraint Entity", entity_type="club")
        entity.assign_owner("owner")
        session.add(entity)
        session.flush()
        session.add(PortalEntityProfile(entity_id=entity.id, max_sites=1))

    with session_factory() as session, pytest.raises(IntegrityError), session.begin():
        session.add(PortalEntityMember(entity_id=entity.id, user_id="member", role="Owner"))

    with session_factory() as session, session.begin():
        session.delete(session.get(PortalEntityProfile, entity.id))
        session.flush()
    with session_factory() as session, pytest.raises(IntegrityError), session.begin():
        session.add(PortalEntityProfile(entity_id=entity.id, max_sites=-1))


def test_clear_site_id_releases_reservation_only_by_explicit_action(
    session_factory,
):
    with session_factory() as session, session.begin():
        site = Site(
            country_code="CA", site_code="25", site_id="25001", name="Reserved Site"
        )
        session.add(site)
        session.flush()
        site_id = site.id
        session.add(
            SiteIdAllocation(
                site_id="25001", country_code="CA", site_code="25", site_pk=site_id
            )
        )

    clear_site_id("25001", session_factory=session_factory)
    with session_factory() as session:
        assert session.get(Site, site_id).site_id is None
        assert session.get(SiteIdAllocation, "25001") is None


def test_clear_site_id_refuses_sites_with_repeaters_and_rolls_back(session_factory):
    with session_factory() as session, session.begin():
        site = Site(
            country_code="CA", site_code="25", site_id="25002", name="Active Site"
        )
        session.add(site)
        session.flush()
        site_id = site.id
        session.add_all(
            [
                SiteIdAllocation(
                    site_id="25002", country_code="CA", site_code="25", site_pk=site_id
                ),
                Repeater(node_id=25002, site_id=site_id),
            ]
        )

    with pytest.raises(ValueError, match="repeaters"):
        clear_site_id("25002", session_factory=session_factory)
    with session_factory() as session:
        assert session.get(Site, site_id).site_id == "25002"
        assert session.get(SiteIdAllocation, "25002") is not None


def test_clear_site_id_rejects_invalid_values_and_unknown_ids(session_factory):
    for invalid in ("1", "12A45", "１２３４５", 25000):
        with pytest.raises(ValueError, match="five digits"):
            clear_site_id(invalid, session_factory=session_factory)
    clear_site_id("25000", session_factory=session_factory)
    with session_factory() as session:
        assert session.scalar(select(Site.site_id)) is None


def test_portal_admin_cli_uses_dynaconf_database_url(app, monkeypatch, capsys):
    database_url = str(app.extensions["portal_database"].url)
    monkeypatch.setattr(
        "portal_backend.admin_cli.load_settings",
        lambda: SimpleNamespace(database_url=database_url),
    )
    with app.extensions["portal_session_factory"]() as session, session.begin():
        site = Site(country_code="CA", site_code="25", site_id="25003", name="CLI Site")
        session.add(site)
        session.flush()
        site_pk = site.id
        session.add(
            SiteIdAllocation(
                site_id="25003", country_code="CA", site_code="25", site_pk=site_pk
            )
        )
    main(["clear-site-id", "25003"])
    assert "Cleared Site ID 25003" in capsys.readouterr().out
