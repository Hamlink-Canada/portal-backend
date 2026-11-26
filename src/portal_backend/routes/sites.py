"""Site management routes."""

from flask import current_app, g, jsonify
from flask_smorest import Blueprint
from rpt_common.models import Entity
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError

from portal_backend.api_sec.schemas import CreateSiteSchema, ListQuerySchema, UpdateSiteSchema
from portal_backend.auth import require_access_token
from portal_backend.database import get_session_factory
from portal_backend.models import PortalEntityProfile, PortalUser, Repeater, Site, SiteIdAllocation
from portal_backend.utils.authorization import (
    can_manage_entity,
    can_manage_repeater,
    can_manage_site,
    can_view_site,
    is_system_super_admin,
    manageable_entity_ids,
    viewable_entity_ids,
)
from portal_backend.utils.geography import resolve_site_location
from portal_backend.utils.http import failure
from portal_backend.utils.pagination import page_query, paginated_response
from portal_backend.utils.site_ids import allocate_site_id, site_code_options

sites_bp = Blueprint("sites", __name__, description="Site management.")


def _site_dict(session, site):
    serialized = site.to_dict()
    owner = session.get(PortalUser, site.owner_subject) if site.owner_subject else None
    serialized["owner_display_name"] = owner.display_name if owner else None
    return serialized


@sites_bp.get("/api/v1/sites")
@require_access_token
@sites_bp.doc(summary="List Sites accessible to the caller", security=[{"bearerAuth": []}])
@sites_bp.arguments(ListQuerySchema, location="query")
def list_sites(args):
    claims = g.access_token_claims
    try:
        with get_session_factory()() as session:
            statement = select(Site)
            if is_system_super_admin(claims):
                if args["scope"] == "mine":
                    entity_ids = manageable_entity_ids(session, claims["sub"])
                    filters = [Site.owner_subject == claims["sub"]]
                    if entity_ids:
                        filters.append(Site.owner_entity_id.in_(entity_ids))
                    statement = statement.where(or_(*filters))
            else:
                subject = claims["sub"]
                entity_ids = viewable_entity_ids(session, subject)
                filters = [Site.owner_subject == subject]
                if entity_ids:
                    filters.append(Site.owner_entity_id.in_(entity_ids))
                statement = statement.where(or_(*filters))
            if args["entity_id"] is not None:
                statement = statement.where(Site.owner_entity_id == args["entity_id"])
            if args["personal"]:
                statement = statement.where(
                    Site.owner_entity_id.is_(None), Site.owner_subject == claims["sub"]
                )
            term = args["q"].strip()
            if term:
                pattern = f"%{term}%"
                statement = statement.where(
                    or_(
                        Site.name.ilike(pattern),
                        Site.site_id.ilike(pattern),
                        Site.site_code.ilike(pattern),
                        Site.country_code.ilike(pattern),
                        Site.grid_square.ilike(pattern),
                        Site.location.ilike(pattern),
                        Site.description.ilike(pattern),
                    )
                )
            statement = statement.order_by(Site.name, Site.id)
            sites, pagination = page_query(
                session, statement, page=args["page"], page_size=args["page_size"]
            )
            items = []
            for site in sites:
                serialized = _site_dict(session, site)
                serialized["can_manage"] = can_manage_site(session, site, claims)
                serialized["can_edit_identity"] = is_system_super_admin(claims)
                items.append(serialized)
            return jsonify(paginated_response(items, pagination))
    except RuntimeError:
        return failure("database_unavailable", 503)


@sites_bp.post("/api/v1/sites")
@require_access_token
@sites_bp.doc(summary="Create a Site and allocate its Site ID", security=[{"bearerAuth": []}])
@sites_bp.arguments(CreateSiteSchema)
def create_site(data):
    claims = g.access_token_claims
    try:
        grid, latitude, longitude, country_code = resolve_site_location(
            data.get("grid_square"), data.get("latitude"), data.get("longitude")
        )
    except ValueError as error:
        return failure(str(error), 400)

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return failure("name_required", 400)
    numbering_mode = data.get("numbering_mode", "short")
    if numbering_mode not in {"short", "extended"}:
        return failure("invalid_numbering_mode", 400)

    owner_entity_id = data.get("owner_entity_id")
    system_managed = data.get("system_managed", False)
    if type(system_managed) is not bool:
        return failure("system_managed_must_be_boolean", 400)
    if system_managed and not is_system_super_admin(claims):
        return failure("forbidden", 403)
    if system_managed and owner_entity_id is not None:
        return failure("system_managed_site_cannot_have_entity_owner", 400)
    if owner_entity_id is not None and type(owner_entity_id) is not int:
        return failure("invalid_owner_entity_id", 400)
    owner_subject = claims["sub"] if owner_entity_id is None and not system_managed else None

    try:
        with get_session_factory()() as session:
            if owner_entity_id is not None:
                # Lock the Entity row while enforcing its Site quota.
                entity = session.scalar(
                    select(Entity).where(Entity.id == owner_entity_id).with_for_update()
                )
                if entity is None or not can_manage_entity(session, owner_entity_id, claims):
                    return failure("forbidden", 403)
                profile = session.get(PortalEntityProfile, owner_entity_id)
                max_sites = profile.max_sites if profile else 5
                current_count = session.scalar(
                    select(func.count())
                    .select_from(Site)
                    .where(Site.owner_entity_id == owner_entity_id)
                )
                if current_count >= max_sites:
                    return failure("entity_max_sites_exceeded", 409)
            options = site_code_options(
                session, country_code, current_app.config.get("PORTAL_SITE_CODES_FILE")
            )
            eligible = [option for option in options if option["available_numbers"] > 0]
            selected_code = data.get("site_code")
            if selected_code is None and len(eligible) == 1:
                selected_code = eligible[0]["site_code"]
            if selected_code is None and len(eligible) > 1:
                return failure("site_code_selection_required", 400)

            site = Site(
                country_code=country_code,
                site_code=selected_code or "25",
                name=name,
                location=data.get("location"),
                description=data.get("description"),
                numbering_mode=numbering_mode,
                grid_square=grid,
                latitude=latitude,
                longitude=longitude,
                owner_subject=owner_subject if owner_entity_id is None else None,
                owner_entity_id=owner_entity_id,
            )
            session.add(site)
            session.flush()
            allocate_site_id(
                session,
                site,
                selected_site_code=selected_code,
                mapping_path=current_app.config.get("PORTAL_SITE_CODES_FILE"),
            )
            session.commit()
            return jsonify(_site_dict(session, site)), 201
    except ValueError as error:
        return failure(str(error), 400)
    except IntegrityError:
        return failure("could_not_create_site", 409)
    except RuntimeError:
        return failure("database_unavailable", 503)
    except OSError:
        return failure("site_code_mapping_unavailable", 503)


@sites_bp.get("/api/v1/sites/<int:site_pk>")
@require_access_token
@sites_bp.doc(summary="Get a Site", security=[{"bearerAuth": []}])
def get_site(site_pk):
    try:
        with get_session_factory()() as session:
            # Serialize identity allocation and authorization against concurrent
            # edits to the same Site (PostgreSQL honors this row lock).
            site = session.get(Site, site_pk, with_for_update=True)
            if site is None or not can_view_site(session, site, g.access_token_claims):
                return failure("site_not_found", 404)
            serialized = _site_dict(session, site)
            serialized["can_manage"] = can_manage_site(session, site, g.access_token_claims)
            serialized["can_edit_identity"] = is_system_super_admin(g.access_token_claims)
            return jsonify(serialized)
    except RuntimeError:
        return failure("database_unavailable", 503)


@sites_bp.get("/api/v1/sites/<int:site_pk>/repeaters")
@require_access_token
@sites_bp.doc(summary="List Repeaters at a Site", security=[{"bearerAuth": []}])
@sites_bp.arguments(ListQuerySchema, location="query")
def list_repeaters(args, site_pk):
    try:
        with get_session_factory()() as session:
            site = session.get(Site, site_pk)
            if site is None or not can_view_site(session, site, g.access_token_claims):
                return failure("site_not_found", 404)
            lifetime = current_app.config.get("PORTAL_REGISTRATION_LIFETIME_SECONDS")
            statement = select(Repeater).where(Repeater.site_id == site_pk)
            term = args["q"].strip()
            if term:
                pattern = f"%{term}%"
                statement = statement.where(
                    or_(
                        cast(Repeater.node_id, String).ilike(pattern),
                        Repeater.call_sign.ilike(pattern),
                        Repeater.frequency.ilike(pattern),
                        Repeater.tone.ilike(pattern),
                    )
                )
            statement = statement.order_by(Repeater.node_id)
            items, pagination = page_query(
                session, statement, page=args["page"], page_size=args["page_size"]
            )
            response_items = []
            for item in items:
                serialized = item.to_dict(lifetime_seconds=lifetime)
                serialized["can_manage"] = can_manage_repeater(session, item, g.access_token_claims)
                response_items.append(serialized)
            return jsonify(paginated_response(response_items, pagination))
    except RuntimeError:
        return failure("database_unavailable", 503)


@sites_bp.patch("/api/v1/sites/<int:site_pk>")
@require_access_token
@sites_bp.doc(summary="Edit an owned Site", security=[{"bearerAuth": []}])
@sites_bp.arguments(UpdateSiteSchema)
def update_site(data, site_pk):
    claims = g.access_token_claims
    try:
        with get_session_factory()() as session:
            site = session.scalar(select(Site).where(Site.id == site_pk).with_for_update())
            if site is None or not can_manage_site(session, site, claims):
                return failure("site_not_found", 404)

            if "site_id" in data and not is_system_super_admin(claims):
                return failure("site_identity_system_admin_required", 403)

            if "name" in data:
                name = data["name"].strip()
                if not name:
                    return failure("name_required", 400)
                duplicate = session.scalar(
                    select(Site.id).where(Site.name == name, Site.id != site.id)
                )
                if duplicate is not None:
                    return failure("site_exists", 409)
                site.name = name
            if "location" in data:
                site.location = data["location"].strip() if data["location"] else None
            if "description" in data:
                site.description = data["description"]

            repeaters_exist = (
                session.scalar(select(Repeater.node_id).where(Repeater.site_id == site.id).limit(1))
                is not None
            )
            if "numbering_mode" in data and data["numbering_mode"] != site.numbering_mode:
                if not is_system_super_admin(claims):
                    return failure("site_identity_system_admin_required", 403)
                if repeaters_exist:
                    return failure("numbering_mode_locked_after_repeater_creation", 409)
                site.numbering_mode = data["numbering_mode"]

            location_changed = any(key in data for key in ("grid_square", "latitude", "longitude"))
            grid, latitude, longitude = site.grid_square, site.latitude, site.longitude
            new_country = site.country_code
            if location_changed:
                grid = data.get("grid_square", site.grid_square)
                latitude = data.get("latitude", site.latitude)
                longitude = data.get("longitude", site.longitude)
                if not grid:
                    return failure("grid_square_required", 400)
                try:
                    grid, latitude, longitude, new_country = resolve_site_location(
                        grid, latitude, longitude
                    )
                except ValueError as error:
                    return failure(str(error), 400)

                moved = (grid, latitude, longitude) != (
                    site.grid_square,
                    site.latitude,
                    site.longitude,
                )
                if moved and not is_system_super_admin(claims):
                    return failure("site_location_system_admin_required", 403)

            selected_code = data.get("site_code")
            identity_change = new_country != site.country_code or (
                selected_code is not None and selected_code != site.site_code
            )
            if identity_change:
                if not is_system_super_admin(claims):
                    return failure("site_identity_system_admin_required", 403)
                if repeaters_exist:
                    return failure("site_code_locked_after_repeater_creation", 409)
                options = site_code_options(
                    session, new_country, current_app.config.get("PORTAL_SITE_CODES_FILE")
                )
                eligible = [item for item in options if item["available_numbers"] > 0]
                eligible_codes = {item["site_code"] for item in eligible}
                if selected_code is None:
                    if site.site_code in eligible_codes and new_country == site.country_code:
                        selected_code = site.site_code
                    elif len(eligible) == 1:
                        selected_code = eligible[0]["site_code"]
                    else:
                        return failure("site_code_selection_required", 400)
                if selected_code not in eligible_codes:
                    return failure("invalid_site_code", 400)

                previous_allocation = (
                    session.get(SiteIdAllocation, site.site_id) if site.site_id else None
                )
                if previous_allocation is not None:
                    previous_allocation.site_pk = None
                site.site_id = None
                site.country_code = new_country
                site.site_code = selected_code
                site.grid_square = grid
                site.latitude = latitude
                site.longitude = longitude
                session.flush()
                if "site_id" not in data:
                    allocate_site_id(
                        session,
                        site,
                        selected_site_code=selected_code,
                        mapping_path=current_app.config.get("PORTAL_SITE_CODES_FILE"),
                    )
            elif location_changed:
                site.grid_square = grid
                site.latitude = latitude
                site.longitude = longitude

            if "site_id" in data:
                requested_site_id = data["site_id"]
                if requested_site_id != site.site_id:
                    if repeaters_exist:
                        return failure("site_id_locked_after_repeater_creation", 409)
                    assigned_site = session.scalar(
                        select(Site.id).where(
                            Site.site_id == requested_site_id,
                            Site.id != site.id,
                        )
                    )
                    if assigned_site is not None:
                        return failure("site_id_already_assigned", 409)

                    previous_allocation = (
                        session.get(SiteIdAllocation, site.site_id) if site.site_id else None
                    )
                    if previous_allocation is not None:
                        previous_allocation.site_pk = None

                    allocation = session.get(SiteIdAllocation, requested_site_id)
                    if allocation is None:
                        allocation = SiteIdAllocation(
                            site_id=requested_site_id,
                            country_code=site.country_code,
                            site_code=requested_site_id[:2],
                            site_pk=site.id,
                        )
                        session.add(allocation)
                    else:
                        allocation.country_code = site.country_code
                        allocation.site_code = requested_site_id[:2]
                        allocation.site_pk = site.id
                    site.site_id = requested_site_id

            session.commit()
            return jsonify(_site_dict(session, site))
    except IntegrityError:
        return failure("could_not_update_site", 409)
    except (OSError, ValueError) as error:
        return failure(str(error), 400)
    except RuntimeError:
        return failure("database_unavailable", 503)
