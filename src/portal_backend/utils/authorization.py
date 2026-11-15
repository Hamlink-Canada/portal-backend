"""Backend authorization based on Site and shared Entity ownership."""

from flask import current_app
from rpt_common.models import Entity, EntitySuperAdmin
from sqlalchemy import select

from portal_backend.models import PortalEntityMember


def _claim_at_path(claims, path):
    value = claims
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def is_system_super_admin(claims):
    """Check the configured OIDC claim reserved for cross-product System Admins."""
    claim_path = current_app.config.get("PORTAL_SYSTEM_SUPER_ADMIN_CLAIM_PATH", "")
    expected = current_app.config.get("PORTAL_SYSTEM_SUPER_ADMIN_CLAIM_VALUE", "")
    if not claim_path or not expected:
        return False
    actual = _claim_at_path(claims, claim_path)
    if isinstance(actual, (list, tuple, set)):
        return expected in actual
    return str(actual).lower() == str(expected).lower()


def manageable_entity_ids(session, subject):
    owned = select(Entity.id).where(Entity.owner_user_id == subject)
    administered = select(EntitySuperAdmin.entity_id).where(EntitySuperAdmin.user_id == subject)
    return set(session.scalars(owned.union(administered)).all())


def can_manage_entity(session, entity_id, claims):
    subject = claims.get("sub")
    if is_system_super_admin(claims):
        return True
    if not subject:
        return False
    entity = session.get(Entity, entity_id)
    if entity is None:
        return False
    return entity.owner_user_id == subject or session.get(
        EntitySuperAdmin, (entity_id, subject)
    ) is not None


def viewable_entity_ids(session, subject):
    owned_or_administered = manageable_entity_ids(session, subject)
    memberships = session.scalars(
        select(PortalEntityMember.entity_id).where(PortalEntityMember.user_id == subject)
    ).all()
    return owned_or_administered.union(memberships)


def can_view_entity(session, entity_id, claims):
    subject = claims.get("sub")
    if not subject:
        return False
    return can_manage_entity(session, entity_id, claims) or session.get(
        PortalEntityMember, (entity_id, subject)
    ) is not None


def can_view_site(session, site, claims):
    if is_system_super_admin(claims):
        return True
    if site.owner_subject and site.owner_subject == claims.get("sub"):
        return True
    if site.owner_entity_id is None:
        return False
    return can_view_entity(session, site.owner_entity_id, claims)


def can_manage_site(session, site, claims):
    subject = claims.get("sub")
    if is_system_super_admin(claims):
        return True
    if not subject:
        return False
    if site.owner_subject == subject:
        return True
    if site.owner_entity_id is None:
        return False
    entity = session.get(Entity, site.owner_entity_id)
    if entity is None:
        return False
    return entity.owner_user_id == subject or session.get(
        EntitySuperAdmin, (site.owner_entity_id, subject)
    ) is not None


def can_manage_repeater(session, repeater, claims):
    """Allow Site managers and Entity Staff to manage an existing Repeater."""
    if can_manage_site(session, repeater.site, claims):
        return True
    subject = claims.get("sub")
    entity_id = repeater.site.owner_entity_id
    if not subject or entity_id is None:
        return False
    member = session.get(PortalEntityMember, (entity_id, subject))
    return member is not None and member.role == "Staff"
