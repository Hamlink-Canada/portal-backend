"""Repeater management routes."""

import secrets

from flask import current_app, g, jsonify
from flask_smorest import Blueprint
from rpt_common.models import NodeCredential
from sqlalchemy import String, cast, or_, select
from sqlalchemy.exc import IntegrityError

from portal_backend.api_sec.schemas import (
    CreateRepeaterSchema,
    ListQuerySchema,
    UpdateRepeaterSchema,
)
from portal_backend.auth import require_access_token
from portal_backend.database import get_session_factory
from portal_backend.models import Repeater, Site
from portal_backend.utils.authorization import (
    can_manage_repeater,
    can_manage_site,
    can_view_site,
    is_system_super_admin,
    manageable_entity_ids,
    viewable_entity_ids,
)
from portal_backend.utils.http import failure, no_store
from portal_backend.utils.pagination import page_query, paginated_response

repeaters_bp = Blueprint("repeaters", __name__, description="Repeater management.")


@repeaters_bp.get("/api/v1/repeaters")
@require_access_token
@repeaters_bp.doc(summary="List Repeaters accessible to the caller", security=[{"bearerAuth": []}])
@repeaters_bp.arguments(ListQuerySchema, location="query")
def list_accessible_repeaters(args):
    claims = g.access_token_claims
    try:
        with get_session_factory()() as session:
            stmt = select(Repeater).join(Site, Repeater.site_id == Site.id)
            if is_system_super_admin(claims):
                if args["scope"] == "mine":
                    entity_ids = manageable_entity_ids(session, claims["sub"])
                    filters = [Site.owner_subject == claims["sub"]]
                    if entity_ids:
                        filters.append(Site.owner_entity_id.in_(entity_ids))
                    stmt = stmt.where(or_(*filters))
            else:
                subject = claims["sub"]
                entity_ids = viewable_entity_ids(session, subject)
                site_filters = [Site.owner_subject == subject]
                if entity_ids:
                    site_filters.append(Site.owner_entity_id.in_(entity_ids))
                stmt = stmt.where(or_(*site_filters))
            if args["site_id"] is not None:
                stmt = stmt.where(Repeater.site_id == args["site_id"])
            if args["entity_id"] is not None:
                stmt = stmt.where(Site.owner_entity_id == args["entity_id"])
            term = args["q"].strip()
            if term:
                pattern = f"%{term}%"
                stmt = stmt.where(
                    or_(
                        cast(Repeater.node_id, String).ilike(pattern),
                        Repeater.call_sign.ilike(pattern),
                        Repeater.frequency.ilike(pattern),
                        Repeater.tone.ilike(pattern),
                        Site.name.ilike(pattern),
                        Site.site_id.ilike(pattern),
                    )
                )
            stmt = stmt.order_by(Repeater.node_id)
            repeaters, pagination = page_query(
                session, stmt, page=args["page"], page_size=args["page_size"]
            )
            lifetime = current_app.config.get("PORTAL_REGISTRATION_LIFETIME_SECONDS")
            response_items = []
            for item in repeaters:
                serialized = item.to_dict(lifetime_seconds=lifetime)
                serialized["can_manage"] = can_manage_repeater(session, item, claims)
                response_items.append(serialized)
            return jsonify(paginated_response(response_items, pagination))
    except RuntimeError:
        return failure("database_unavailable", 503)


@repeaters_bp.get("/api/v1/repeaters/<int:node_id>")
@require_access_token
@repeaters_bp.doc(summary="Get an accessible Repeater", security=[{"bearerAuth": []}])
def get_repeater(node_id):
    try:
        with get_session_factory()() as session:
            repeater = session.get(Repeater, node_id)
            if repeater is None or not can_view_site(session, repeater.site, g.access_token_claims):
                return failure("repeater_not_found", 404)
            serialized = repeater.to_dict(
                lifetime_seconds=current_app.config.get("PORTAL_REGISTRATION_LIFETIME_SECONDS")
            )
            serialized["can_manage"] = can_manage_repeater(session, repeater, g.access_token_claims)
            return jsonify(serialized)
    except RuntimeError:
        return failure("database_unavailable", 503)


@repeaters_bp.post("/api/v1/sites/<int:site_pk>/repeaters")
@require_access_token
@repeaters_bp.doc(
    summary="Create a Repeater and generate its registration secret",
    security=[{"bearerAuth": []}],
)
@repeaters_bp.arguments(CreateRepeaterSchema)
def create_repeater(data, site_pk):
    try:
        with get_session_factory()() as session:
            site = session.get(Site, site_pk)
            if site is None or not can_manage_site(session, site, g.access_token_claims):
                return failure("site_not_found", 404)
            site = session.scalar(
                select(Site)
                .where(Site.id == site_pk)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if site is None or not can_manage_site(session, site, g.access_token_claims):
                return failure("site_not_found", 404)
            if not site.site_id:
                return failure("site_id_required", 409)

            if site.numbering_mode == "short":
                if session.scalar(
                    select(Repeater.node_id).where(Repeater.site_id == site_pk).limit(1)
                ):
                    return failure("site_numbering_capacity_reached", 409)
                node_id = int(site.site_id)
                if session.get(Repeater, node_id) is not None:
                    return failure("node_id_already_exists", 409)
                if session.get(NodeCredential, node_id) is not None:
                    return failure("node_id_credential_already_exists", 409)
            else:
                suffix = next(
                    (
                        value
                        for value in range(10)
                        if session.get(Repeater, int(f"{site.site_id}{value}")) is None
                        and session.get(NodeCredential, int(f"{site.site_id}{value}")) is None
                    ),
                    None,
                )
                if suffix is None:
                    return failure("site_numbering_capacity_reached", 409)
                node_id = int(f"{site.site_id}{suffix}")

            if session.get(NodeCredential, node_id) is not None:
                return failure("node_id_credential_already_exists", 409)
            secret = secrets.token_urlsafe(36)
            repeater = Repeater(
                node_id=node_id,
                site_id=site_pk,
                call_sign=data.get("call_sign"),
                frequency=data.get("frequency"),
                tone=data.get("tone"),
            )
            credential = NodeCredential(node_id=node_id, password=secret, enabled=False)
            session.add_all([repeater, credential])
            session.commit()
            response = jsonify(
                repeater=repeater.to_dict(
                    lifetime_seconds=current_app.config.get("PORTAL_REGISTRATION_LIFETIME_SECONDS")
                ),
                registration_secret=secret,
            )
            response.status_code = 201
            return no_store(response)
    except (ValueError, TypeError) as error:
        return failure(str(error), 400)
    except IntegrityError:
        return failure("node_id_already_exists", 409)
    except RuntimeError:
        return failure("database_unavailable", 503)


@repeaters_bp.patch("/api/v1/repeaters/<int:node_id>")
@require_access_token
@repeaters_bp.doc(
    summary="Update a Repeater's call sign or administrative enabled state",
    security=[{"bearerAuth": []}],
)
@repeaters_bp.arguments(UpdateRepeaterSchema)
def update_repeater(data, node_id):
    try:
        with get_session_factory()() as session:
            repeater = session.get(Repeater, node_id)
            if repeater is None or not can_manage_repeater(session, repeater, g.access_token_claims):
                return failure("repeater_not_found", 404)
            credential = session.get(NodeCredential, node_id)
            if credential is None:
                return failure("repeater_credential_not_found", 409)
            if "call_sign" in data:
                repeater.call_sign = data["call_sign"]
            if "frequency" in data:
                repeater.frequency = data["frequency"]
            if "tone" in data:
                repeater.tone = data["tone"]
            if "enabled" in data:
                repeater.enabled = data["enabled"]
                credential.enabled = data["enabled"]
            session.commit()
            return jsonify(
                repeater.to_dict(
                    lifetime_seconds=current_app.config.get("PORTAL_REGISTRATION_LIFETIME_SECONDS")
                )
            )
    except (ValueError, TypeError) as error:
        return failure(str(error), 400)
    except RuntimeError:
        return failure("database_unavailable", 503)


@repeaters_bp.post("/api/v1/repeaters/<int:node_id>/credential/rotate")
@require_access_token
@repeaters_bp.doc(summary="Replace a Repeater's registration secret", security=[{"bearerAuth": []}])
def rotate_repeater_credential(node_id):
    try:
        with get_session_factory()() as session:
            repeater = session.get(Repeater, node_id)
            if repeater is None or not can_manage_repeater(session, repeater, g.access_token_claims):
                return failure("repeater_not_found", 404)
            credential = session.get(NodeCredential, node_id)
            if credential is None:
                return failure("repeater_credential_not_found", 409)
            secret = secrets.token_urlsafe(36)
            credential.password = secret
            session.commit()
            response = jsonify(node_id=node_id, registration_secret=secret)
            return no_store(response)
    except RuntimeError:
        return failure("database_unavailable", 503)
