"""Marshmallow schemas for validating Portal API request bodies."""

from marshmallow import RAISE, Schema, ValidationError, fields, validate, validates_schema
from rpt_common.models.entities import ENTITY_TYPES, valid_call_sign
from rpt_common.utils.regex import GRID_SQUARE_RE, SITE_CODE_RE, USER_ID_RE

# Keep the shared legacy type accepted at the API boundary while new Portal
# records use the amateur-radio organisation types.
PORTAL_ENTITY_TYPES = tuple(sorted(ENTITY_TYPES))


class StrictBoolean(fields.Boolean):
    """Accept JSON booleans only, without coercing strings or numbers."""

    def _deserialize(self, value, attr, data, **kwargs):
        if type(value) is not bool:
            raise ValidationError("Not a valid boolean.")
        return super()._deserialize(value, attr, data, **kwargs)


class StrictSchema(Schema):
    """Reject misspelled and unexpected request properties."""

    class Meta:
        unknown = RAISE


class UserSearchArgsSchema(Schema):
    q = fields.String(required=True, validate=validate.Length(min=2, max=128))


class ListQuerySchema(Schema):
    """Common server-side search, scope, filter, and pagination arguments."""

    q = fields.String(load_default="", validate=validate.Length(max=128))
    page = fields.Integer(load_default=1, strict=False, validate=validate.Range(min=1))
    page_size = fields.Integer(
        load_default=20, strict=False, validate=validate.Range(min=1, max=100)
    )
    scope = fields.String(load_default="all", validate=validate.OneOf(("mine", "all")))
    entity_id = fields.Integer(load_default=None, strict=False, validate=validate.Range(min=1))
    site_id = fields.Integer(load_default=None, strict=False, validate=validate.Range(min=1))
    personal = fields.Boolean(load_default=False)


GRID_SQUARE = validate.Regexp(GRID_SQUARE_RE)


class SiteLocationResolveSchema(StrictSchema):
    grid_square = fields.String(required=True, validate=GRID_SQUARE)
    latitude = fields.Float(allow_none=True, validate=validate.Range(min=-90, max=90))
    longitude = fields.Float(allow_none=True, validate=validate.Range(min=-180, max=180))

    @validates_schema
    def validate_coordinate_pair(self, data, **kwargs):
        if ("latitude" in data) != ("longitude" in data) or (
            (data.get("latitude") is None) != (data.get("longitude") is None)
        ):
            raise ValidationError("latitude and longitude must be provided together")


class CreateSiteSchema(StrictSchema):
    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    location = fields.String(allow_none=True, validate=validate.Length(max=512))
    grid_square = fields.String(required=True, validate=GRID_SQUARE)
    latitude = fields.Float(allow_none=True, validate=validate.Range(min=-90, max=90))
    longitude = fields.Float(allow_none=True, validate=validate.Range(min=-180, max=180))
    site_code = fields.String(allow_none=True, validate=validate.Regexp(SITE_CODE_RE))
    description = fields.String(allow_none=True, validate=validate.Length(max=2000))
    numbering_mode = fields.String(
        load_default="short", validate=validate.OneOf(("short", "extended"))
    )
    owner_entity_id = fields.Integer(allow_none=True, strict=True, validate=validate.Range(min=1))
    system_managed = StrictBoolean(load_default=False)

    @validates_schema
    def validate_coordinate_pair(self, data, **kwargs):
        if ("latitude" in data) != ("longitude" in data) or (
            (data.get("latitude") is None) != (data.get("longitude") is None)
        ):
            raise ValidationError("latitude and longitude must be provided together")


def _validate_call_sign(value):
    if not valid_call_sign(value):
        raise ValidationError("Not a valid amateur radio call sign.")


class CreateRepeaterSchema(StrictSchema):
    call_sign = fields.String(
        allow_none=True,
        validate=[validate.Length(max=32), _validate_call_sign],
    )
    frequency = fields.String(allow_none=True, validate=validate.Length(max=64))
    tone = fields.String(allow_none=True, validate=validate.Length(max=64))


class UpdateRepeaterSchema(StrictSchema):
    call_sign = fields.String(
        allow_none=True,
        validate=[validate.Length(max=32), _validate_call_sign],
    )
    frequency = fields.String(allow_none=True, validate=validate.Length(max=64))
    tone = fields.String(allow_none=True, validate=validate.Length(max=64))
    enabled = StrictBoolean()

    @validates_schema
    def require_update_field(self, data, **kwargs):
        if not data:
            raise ValidationError("At least one repeater property must be supplied.")


class UpdateSiteSchema(StrictSchema):
    name = fields.String(validate=validate.Length(min=1, max=200))
    location = fields.String(allow_none=True, validate=validate.Length(max=512))
    description = fields.String(allow_none=True, validate=validate.Length(max=2000))
    grid_square = fields.String(validate=GRID_SQUARE)
    latitude = fields.Float(allow_none=True, validate=validate.Range(min=-90, max=90))
    longitude = fields.Float(allow_none=True, validate=validate.Range(min=-180, max=180))
    numbering_mode = fields.String(validate=validate.OneOf(("short", "extended")))
    site_code = fields.String(validate=validate.Regexp(SITE_CODE_RE))

    @validates_schema
    def validate_site_update(self, data, **kwargs):
        if not data:
            raise ValidationError("At least one Site property must be supplied.")
        if ("latitude" in data) != ("longitude" in data) or (
            (data.get("latitude") is None) != (data.get("longitude") is None)
        ):
            raise ValidationError("latitude and longitude must be provided together")


def _validate_entity_call_signs(data, **kwargs):
    primary = data.get("primary_call_sign")
    akas = data.get("aka_call_signs") or []
    if any(not valid_call_sign(value) for value in ([primary] if primary else []) + akas):
        raise ValidationError("Entity call signs must be valid amateur radio call signs.")
    normalized = [value.strip().upper() for value in ([primary] if primary else []) + akas]
    if len(set(normalized)) != len(normalized):
        raise ValidationError("Entity call signs must be unique.")


class EntityMemberSchema(StrictSchema):
    user_id = fields.String(
        required=True,
        validate=[validate.Length(min=1, max=255), validate.Regexp(USER_ID_RE)],
    )
    role = fields.String(required=True, validate=validate.OneOf(("Staff", "Viewer")))


class CreateEntitySchema(StrictSchema):
    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    description = fields.String(allow_none=True, validate=validate.Length(max=2000))
    entity_type = fields.String(required=True, validate=validate.OneOf(PORTAL_ENTITY_TYPES))
    website = fields.String(allow_none=True, validate=validate.Length(max=512))
    max_sites = fields.Integer(strict=True, validate=validate.Range(min=0))
    system_managed = StrictBoolean(load_default=False)
    primary_call_sign = fields.String(allow_none=True, validate=validate.Length(max=32))
    aka_call_signs = fields.List(
        fields.String(validate=validate.Length(min=1, max=32)), load_default=list
    )
    members = fields.List(fields.Nested(EntityMemberSchema), load_default=list)

    @validates_schema
    def validate_call_signs(self, data, **kwargs):
        _validate_entity_call_signs(data)
        member_ids = [member["user_id"] for member in data.get("members", [])]
        if len(member_ids) != len(set(member_ids)):
            raise ValidationError("Entity members must be unique.")


class UpdateEntitySchema(StrictSchema):
    name = fields.String(validate=validate.Length(min=1, max=200))
    description = fields.String(allow_none=True, validate=validate.Length(max=2000))
    entity_type = fields.String(validate=validate.OneOf(PORTAL_ENTITY_TYPES))
    website = fields.String(allow_none=True, validate=validate.Length(max=512))
    max_sites = fields.Integer(strict=True, validate=validate.Range(min=0))
    primary_call_sign = fields.String(allow_none=True, validate=validate.Length(max=32))
    aka_call_signs = fields.List(fields.String(validate=validate.Length(min=1, max=32)))
    members = fields.List(fields.Nested(EntityMemberSchema))

    @validates_schema
    def validate_entity_update(self, data, **kwargs):
        if not data:
            raise ValidationError("At least one Entity property must be supplied.")
        if "primary_call_sign" in data or "aka_call_signs" in data:
            _validate_entity_call_signs(data)
        if "members" in data:
            member_ids = [member["user_id"] for member in data["members"]]
            if len(member_ids) != len(set(member_ids)):
                raise ValidationError("Entity members must be unique.")
