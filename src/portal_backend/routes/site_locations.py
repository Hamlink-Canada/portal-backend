"""Site location lookup routes."""

from flask import current_app, jsonify
from flask_smorest import Blueprint

from portal_backend.api_sec.schemas import SiteLocationResolveSchema
from portal_backend.auth import require_access_token
from portal_backend.database import get_session_factory
from portal_backend.utils.geography import resolve_site_location
from portal_backend.utils.http import failure
from portal_backend.utils.site_ids import site_code_options

site_locations_bp = Blueprint("site_locations", __name__, description="Site location lookup.")


@site_locations_bp.post("/api/v1/site-locations/resolve")
@require_access_token
@site_locations_bp.doc(
    summary="Resolve country, coordinates, and Site Codes from a Maidenhead locator",
    security=[{"bearerAuth": []}],
)
@site_locations_bp.arguments(SiteLocationResolveSchema)
def resolve_site_location_route(data):
    try:
        grid, latitude, longitude, country_code = resolve_site_location(
            data.get("grid_square"), data.get("latitude"), data.get("longitude")
        )
    except ValueError as error:
        return failure(str(error), 400)

    try:
        with get_session_factory()() as session:
            codes = site_code_options(
                session, country_code, current_app.config.get("PORTAL_SITE_CODES_FILE")
            )
    except (OSError, ValueError):
        return failure("site_code_mapping_unavailable", 503)

    response = jsonify(
        grid_square=grid,
        country_code=country_code,
        latitude=latitude,
        longitude=longitude,
        site_codes=codes,
    )
    return response
