"""Shared Entity management routes with Portal-owned profile metadata."""

from flask import g, jsonify
from flask_smorest import Blueprint
from rpt_common.models import Entity, EntityCallSign
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from portal_backend.api_sec.schemas import CreateEntitySchema, ListQuerySchema, UpdateEntitySchema
from portal_backend.auth import require_access_token
from portal_backend.database import get_session_factory
from portal_backend.models import PortalEntityMember, PortalEntityProfile, PortalUser, Site
from portal_backend.utils.authorization import (
    can_manage_entity,
    can_view_entity,
    is_system_super_admin,
    manageable_entity_ids,
    viewable_entity_ids,
)
from portal_backend.utils.http import failure
from portal_backend.utils.pagination import page_query, paginated_response

orgs_bp = Blueprint("orgs", __name__, description="Organisation profiles.")


def _normalize_website(value):
    if value is None:
        return None
    value = value.strip()
    if value and not value.lower().startswith(("http://", "https://")):
        value = f"https://{value}"
    return value or None


def _entity_dict(session, entity, claims):
    profile = session.get(PortalEntityProfile, entity.id)
    owner = session.get(PortalUser, entity.owner_user_id) if entity.owner_user_id else None
    members = session.execute(
        select(PortalEntityMember, PortalUser)
        .outerjoin(PortalUser, PortalUser.subject == PortalEntityMember.user_id)
        .where(PortalEntityMember.entity_id == entity.id)
        .order_by(PortalEntityMember.user_id)
    ).all()
    call_signs = sorted(entity.call_signs, key=lambda item: (not item.is_primary, item.call_sign))
    primary = next((item.call_sign for item in call_signs if item.is_primary), None)
    site_count = session.scalar(
        select(func.count()).select_from(Site).where(Site.owner_entity_id == entity.id)
    ) or 0
    return {
        "id": entity.id,
        "name": entity.name,
        "description": entity.description,
        "entity_type": entity.entity_type,
        "owner_user_id": entity.owner_user_id,
        "owner_display_name": owner.display_name if owner else None,
        "can_manage": can_manage_entity(session, entity.id, claims),
        "can_manage_site_limit": is_system_super_admin(claims),
        "is_system_maintained": entity.is_system_maintained,
        "primary_call_sign": primary,
        "aka_call_signs": [item.call_sign for item in call_signs if not item.is_primary],
        "website": profile.website if profile else None,
        "max_sites": profile.max_sites if profile else 5,
        "site_count": site_count,
        "members": [
            {
                "user_id": membership.user_id,
                "display_name": user.display_name if user else membership.user_id,
                "call_signs": user.call_signs if user else [],
                "role": membership.role,
            }
            for membership, user in members
        ],
    }


@orgs_bp.get("/api/v1/orgs")
@require_access_token
@orgs_bp.doc(summary="List Organisations visible to the caller", security=[{"bearerAuth": []}])
@orgs_bp.arguments(ListQuerySchema, location="query")
def list_entities(args):
    claims = g.access_token_claims
    try:
        with get_session_factory()() as session:
            statement = select(Entity)
            if is_system_super_admin(claims):
                if args["scope"] == "mine":
                    owned_ids = manageable_entity_ids(session, claims["sub"])
                    statement = statement.where(
                        or_(Entity.owner_user_id == claims["sub"], Entity.id.in_(owned_ids))
                    )
            else:
                ids = viewable_entity_ids(session, claims["sub"])
                if not ids:
                    statement = statement.where(Entity.id == -1)
                else:
                    statement = statement.where(Entity.id.in_(ids))
            term = args["q"].strip()
            if term:
                pattern = f"%{term}%"
                statement = statement.where(
                    or_(
                        Entity.name.ilike(pattern),
                        Entity.description.ilike(pattern),
                        Entity.entity_type.ilike(pattern),
                        Entity.call_signs.any(EntityCallSign.call_sign.ilike(pattern)),
                    )
                )
            statement = statement.order_by(Entity.name, Entity.id)
            entities, pagination = page_query(
                session, statement, page=args["page"], page_size=args["page_size"]
            )
            return jsonify(
                paginated_response(
                    [_entity_dict(session, entity, claims) for entity in entities], pagination
                )
            )
    except RuntimeError:
        return failure("database_unavailable", 503)


@orgs_bp.post("/api/v1/orgs")
@require_access_token
@orgs_bp.doc(summary="Create an Organisation owned by the caller", security=[{"bearerAuth": []}])
@orgs_bp.arguments(CreateEntitySchema)
def create_entity(data):
    claims = g.access_token_claims
    system_managed = data.get("system_managed", False)
    if "max_sites" in data and not is_system_super_admin(claims):
        return failure("system_super_admin_required", 403)
    if system_managed and not is_system_super_admin(claims):
        return failure("forbidden", 403)
    primary = data.get("primary_call_sign")
    akas = data.get("aka_call_signs", [])
    if akas and not primary:
        return failure("primary_call_sign_required_for_akas", 400)
    members = data.get("members", [])
    if any(member["user_id"] == claims["sub"] for member in members):
        return failure("entity_owner_is_not_a_member", 400)

    try:
        with get_session_factory()() as session:
            entity = Entity(
                name=data["name"],
                description=data.get("description"),
                entity_type=data["entity_type"],
            )
            if not system_managed:
                entity.assign_owner(claims["sub"])
            session.add(entity)
            session.flush()
            profile = PortalEntityProfile(
                entity_id=entity.id,
                website=_normalize_website(data.get("website")),
                max_sites=data.get("max_sites", 5),
            )
            session.add(profile)
            if primary:
                entity.add_primary_call_sign(primary)
                for call_sign in akas:
                    entity.add_aka_call_sign(call_sign)
            session.add_all(
                [
                    PortalEntityMember(
                        entity_id=entity.id,
                        user_id=member["user_id"],
                        role=member["role"],
                    )
                    for member in members
                ]
            )
            session.commit()
            return jsonify(_entity_dict(session, entity, claims)), 201
    except IntegrityError:
        return failure("could_not_create_entity", 409)
    except (TypeError, ValueError) as error:
        return failure(str(error), 400)
    except RuntimeError:
        return failure("database_unavailable", 503)


@orgs_bp.get("/api/v1/orgs/<int:entity_id>")
@require_access_token
@orgs_bp.doc(summary="Get an Organisation profile", security=[{"bearerAuth": []}])
def get_entity(entity_id):
    try:
        with get_session_factory()() as session:
            entity = session.get(Entity, entity_id)
            if entity is None or not can_view_entity(session, entity_id, g.access_token_claims):
                return failure("entity_not_found", 404)
            return jsonify(_entity_dict(session, entity, g.access_token_claims))
    except RuntimeError:
        return failure("database_unavailable", 503)


@orgs_bp.patch("/api/v1/orgs/<int:entity_id>")
@require_access_token
@orgs_bp.doc(summary="Edit an Organisation profile", security=[{"bearerAuth": []}])
@orgs_bp.arguments(UpdateEntitySchema)
def update_entity(data, entity_id):
    claims = g.access_token_claims
    try:
        with get_session_factory()() as session:
            entity = session.get(Entity, entity_id)
            if entity is None or not can_manage_entity(session, entity_id, claims):
                return failure("entity_not_found", 404)
            if "max_sites" in data and not is_system_super_admin(claims):
                return failure("system_super_admin_required", 403)

            profile = session.get(PortalEntityProfile, entity_id)
            if profile is None:
                profile = PortalEntityProfile(entity_id=entity_id, max_sites=5)
                session.add(profile)
            if "name" in data:
                entity.name = data["name"]
            if "description" in data:
                entity.description = data["description"]
            if "entity_type" in data:
                entity.entity_type = data["entity_type"]
            if "website" in data:
                profile.website = _normalize_website(data["website"])
            if "max_sites" in data:
                current_count = session.scalar(
                    select(func.count()).select_from(Site).where(Site.owner_entity_id == entity_id)
                )
                if data["max_sites"] < current_count:
                    return failure("max_sites_below_current_site_count", 409)
                profile.max_sites = data["max_sites"]
            if "members" in data:
                members = data["members"]
                if any(member["user_id"] == entity.owner_user_id for member in members):
                    return failure("entity_owner_is_not_a_member", 400)
                current = {
                    member.user_id: member
                    for member in session.scalars(
                        select(PortalEntityMember).where(
                            PortalEntityMember.entity_id == entity_id
                        )
                    ).all()
                }
                requested = {member["user_id"]: member["role"] for member in members}
                for user_id, member in current.items():
                    if user_id not in requested:
                        session.delete(member)
                    else:
                        member.role = requested[user_id]
                for user_id, role in requested.items():
                    if user_id not in current:
                        session.add(
                            PortalEntityMember(entity_id=entity_id, user_id=user_id, role=role)
                        )
            if "primary_call_sign" in data or "aka_call_signs" in data:
                existing_signs = list(entity.call_signs)
                old_primary = next((item.call_sign for item in existing_signs if item.is_primary), None)
                primary = data.get("primary_call_sign", old_primary)
                if "aka_call_signs" in data:
                    akas = data["aka_call_signs"]
                else:
                    akas = [item.call_sign for item in existing_signs if not item.is_primary]
                if akas and not primary:
                    return failure("primary_call_sign_required_for_akas", 400)
                if primary == old_primary:
                    requested_akas = {call_sign.strip().upper() for call_sign in akas}
                    for existing in existing_signs:
                        if not existing.is_primary and existing.call_sign not in requested_akas:
                            entity.call_signs.remove(existing)
                    session.flush()
                    existing_akas = {
                        item.call_sign for item in entity.call_signs if not item.is_primary
                    }
                    for call_sign in akas:
                        if call_sign.strip().upper() not in existing_akas:
                            entity.add_aka_call_sign(call_sign)
                else:
                    entity.call_signs.clear()
                    session.flush()
                if primary and primary != old_primary:
                    entity.add_primary_call_sign(primary)
                    for call_sign in akas:
                        entity.add_aka_call_sign(call_sign)

            session.commit()
            return jsonify(_entity_dict(session, entity, claims))
    except IntegrityError:
        return failure("could_not_update_entity", 409)
    except (TypeError, ValueError) as error:
        return failure(str(error), 400)
    except RuntimeError:
        return failure("database_unavailable", 503)
