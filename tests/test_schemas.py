"""Request schema validation and strict type handling."""

import pytest
from marshmallow import ValidationError

from portal_backend.api_sec.schemas import (
    CreateEntitySchema,
    CreateRepeaterSchema,
    CreateSiteSchema,
    SiteLocationResolveSchema,
    StrictBoolean,
    UpdateEntitySchema,
    UpdateRepeaterSchema,
    UpdateSiteSchema,
)


@pytest.mark.parametrize("grid", ["FN", "FN0", "FN03!", "ZZ99", "FN03AA1", "ＦＮ０３"])
def test_site_schemas_reject_invalid_grid_squares(grid):
    with pytest.raises(ValidationError):
        SiteLocationResolveSchema().load({"grid_square": grid})


@pytest.mark.parametrize(
    "payload",
    [
        {"grid_square": "FN03", "latitude": 45.5},
        {"grid_square": "FN03", "longitude": -73.0},
        {"grid_square": "FN03", "latitude": None, "longitude": -73.0},
    ],
)
def test_location_schema_requires_a_coordinate_pair(payload):
    with pytest.raises(ValidationError, match="provided together"):
        SiteLocationResolveSchema().load(payload)


@pytest.mark.parametrize("coords", [(90.1, 0), (0, 180.1), (True, 0)])
def test_location_schema_rejects_out_of_range_coordinates(coords):
    with pytest.raises(ValidationError):
        SiteLocationResolveSchema().load(
            {"grid_square": "FN03", "latitude": coords[0], "longitude": coords[1]}
        )


def test_site_schema_accepts_valid_create_and_owner_fields():
    data = CreateSiteSchema().load(
        {"name": "Club Site", "grid_square": "FN03", "owner_entity_id": 7}
    )
    assert data["numbering_mode"] == "short"
    assert data["owner_entity_id"] == 7
    assert data["system_managed"] is False


@pytest.mark.parametrize("site_code", ["15", "99", "２５", "2A"])
def test_site_schema_rejects_invalid_site_code(site_code):
    with pytest.raises(ValidationError):
        CreateSiteSchema().load(
            {"name": "Club Site", "grid_square": "FN03", "site_code": site_code}
        )


def test_update_site_rejects_empty_and_incomplete_coordinate_updates():
    with pytest.raises(ValidationError, match="At least one"):
        UpdateSiteSchema().load({})
    with pytest.raises(ValidationError, match="provided together"):
        UpdateSiteSchema().load({"latitude": 45.5})


@pytest.mark.parametrize("value", ["true", "false", 0, 1, None])
def test_strict_boolean_rejects_coercible_non_boolean_values(value):
    with pytest.raises(ValidationError):
        StrictBoolean().deserialize(value)


def test_repeater_schema_validates_call_sign_and_enabled_boolean():
    assert CreateRepeaterSchema().load({"call_sign": "ve3abc"})["call_sign"] == "ve3abc"
    with pytest.raises(ValidationError):
        CreateRepeaterSchema().load({"call_sign": "not-a-call"})
    with pytest.raises(ValidationError):
        UpdateRepeaterSchema().load({"enabled": "true"})
    with pytest.raises(ValidationError, match="At least one"):
        UpdateRepeaterSchema().load({})


def test_entity_schema_checks_call_signs_members_and_unknown_fields():
    schema = CreateEntitySchema()
    valid = schema.load(
        {
            "name": "Radio Club",
            "entity_type": "club",
            "primary_call_sign": "VE3ABC",
            "aka_call_signs": ["VE3DEF"],
            "members": [{"user_id": "member-1", "role": "Viewer"}],
        }
    )
    assert "max_sites" not in valid
    with pytest.raises(ValidationError, match="valid amateur radio"):
        schema.load({"name": "Radio Club", "entity_type": "club", "primary_call_sign": "bad"})
    with pytest.raises(ValidationError, match="unique"):
        schema.load(
            {
                "name": "Radio Club",
                "entity_type": "club",
                "primary_call_sign": "VE3ABC",
                "aka_call_signs": ["ve3abc"],
            }
        )
    with pytest.raises(ValidationError):
        schema.load({"name": "Radio Club", "entity_type": "club", "unknown": True})


def test_update_entity_requires_a_non_empty_patch_and_unique_members():
    schema = UpdateEntitySchema()
    with pytest.raises(ValidationError, match="At least one"):
        schema.load({})
    with pytest.raises(ValidationError, match="unique"):
        schema.load(
            {
                "members": [
                    {"user_id": "member-1", "role": "Staff"},
                    {"user_id": "member-1", "role": "Viewer"},
                ]
            }
        )
