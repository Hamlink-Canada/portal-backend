"""Explicitly gated mock OIDC and deterministic in-memory preview data."""

import secrets
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from pathlib import Path

from alembic import command
from alembic.config import Config
from authlib.jose import JsonWebKey, JsonWebToken
from flask import current_app, jsonify, request
from flask_smorest import Blueprint as ApiBlueprint
from rpt_common.models import Entity, EntitySuperAdmin, NodeCredential
from sqlalchemy import select

from portal_backend.database import get_session_factory
from portal_backend.models import (
    PortalEntityMember,
    PortalEntityProfile,
    PortalUser,
    Repeater,
    Site,
    SiteIdAllocation,
)

dev_preview_bp = ApiBlueprint("dev-preview", __name__, description="Development preview identity.")
DEV_ISSUER = "http://portal.local/dev-oidc"
DEV_AUDIENCE = "portal-api"
USERS = (
    ("dev-user-001", "Portal Reviewer", "portal.reviewer", ["VE3DEV"], False),
    ("dev-user-002", "Morgan Example", "morgan.example", ["VE3MOR"], False),
    ("dev-user-003", "Casey Staff", "casey.staff", ["VE3CAS"], False),
    ("dev-admin-001", "Portal System Admin", "portal.system-admin", ["W1SYS"], True),
)


def _run_migrations(connection):
    common_package = find_spec("rpt_common")
    if common_package is None or common_package.origin is None:
        raise RuntimeError("The rpt_common package is required for preview migrations")
    common_migrations = Path(common_package.origin).parent / "migrations"
    application_root = Path.cwd()
    common_config = Config()
    common_config.set_main_option("script_location", str(common_migrations))
    common_config.attributes["connection"] = connection
    command.upgrade(common_config, "head")

    portal_config = Config(str(application_root / "alembic.ini"))
    portal_config.set_main_option("sqlalchemy.url", "sqlite:///:memory:")
    portal_config.attributes["connection"] = connection
    command.upgrade(portal_config, "head")


def _seed_preview_data():
    factory = get_session_factory()
    with factory() as session, session.begin():
        session.add_all(
            PortalUser(
                subject=subject,
                display_name=name,
                username=username,
                call_signs=call_signs,
            )
            for subject, name, username, call_signs, _admin in USERS
        )

        names = [
            ("Example Radio Association", "association", "dev-user-001"),
            ("Ottawa Valley Amateur Radio Club", "club", "dev-user-002"),
            ("North American Repeater Network", "union", None),
            ("Kingston Amateur Radio Society", "society", "dev-user-003"),
            ("Eastern Ontario Radio League", "league", "dev-user-002"),
            ("Québec Amateur Radio Federation", "federation", "dev-user-001"),
            ("Montréal Radio Club", "club", "dev-user-003"),
            ("Great Lakes Repeater Association", "association", "dev-user-002"),
            ("Capital Region Radio Society", "society", "dev-user-001"),
            ("Northern Link Network", "other", "dev-user-003"),
            ("St. Lawrence Radio Club", "club", "dev-user-002"),
            ("Eastern Ontario Repeater Group", "association", "dev-user-001"),
        ]
        entities = []
        for index, (name, entity_type, owner) in enumerate(names, start=1):
            prefix = chr(ord("A") + index)
            entity = Entity(
                name=name,
                description=(
                    "Development preview organisation with editable API-backed sample data."
                ),
                entity_type=entity_type,
            )
            if owner:
                entity.assign_owner(owner)
            else:
                entity.is_system_maintained = True
            entity.add_primary_call_sign(f"V{prefix}1ORG")
            entity.add_aka_call_sign(f"V{prefix}1ALT")
            session.add(entity)
            session.flush()
            entities.append(entity)
            session.add(
                PortalEntityProfile(
                    entity_id=entity.id, website=f"https://org{index}.example.invalid", max_sites=8
                )
            )
            members_by_entity = {
                1: (("dev-user-002", "Viewer"), ("dev-user-003", "Staff")),
                2: (("dev-user-003", "Staff"),),
                4: (("dev-user-001", "Viewer"),),
                6: (("dev-user-002", "Viewer"),),
                7: (("dev-user-001", "Viewer"),),
                8: (("dev-user-003", "Staff"),),
                10: (("dev-user-001", "Viewer"),),
                11: (("dev-user-003", "Staff"),),
                12: (("dev-user-002", "Viewer"),),
            }
            session.add_all(
                PortalEntityMember(entity_id=entity.id, user_id=subject, role=role)
                for subject, role in members_by_entity.get(index, ())
                if subject != owner
            )
            if index == 3:
                session.add(EntitySuperAdmin(entity_id=entity.id, user_id="dev-user-003"))

        sites = []
        for index in range(1, 16):
            entity = entities[(index - 1) % len(entities)]
            site_id = f"{31000 + index:05d}"
            site = Site(
                country_code="CA",
                site_code="31",
                site_id=site_id,
                name=("Preview Repeater Site" if index == 1 else f"Preview Site {index:02d}"),
                location=("Montréal, Québec" if index % 2 else "Ottawa, Ontario"),
                description="Sample site for exercising Portal management flows.",
                numbering_mode="extended" if index % 3 == 0 else "short",
                grid_square="FN31pr" if index % 2 else "FN25fi",
                latitude=45.5019 if index % 2 else 45.4215,
                longitude=-73.5674 if index % 2 else -75.6972,
                owner_entity_id=entity.id,
            )
            session.add(site)
            session.flush()
            sites.append(site)
            session.add(
                SiteIdAllocation(
                    site_id=site_id, country_code="CA", site_code="31", site_pk=site.id
                )
            )
            enabled = index % 3 != 0
            repeater = Repeater(
                node_id=310420 + index,
                site_id=site.id,
                call_sign=f"VE3{chr(ord('A') + index - 1)}",
                frequency="146.520 MHz" if index % 2 else "147.000 MHz",
                tone="100.0 Hz",
                enabled=enabled,
                last_registration_at=datetime.now(timezone.utc)
                - timedelta(hours=12 if index % 3 == 0 else 1),
                last_registration_ip=f"192.0.2.{index}",
                registration_source="dev-preview",
            )
            session.add_all(
                [
                    repeater,
                    NodeCredential(
                        node_id=repeater.node_id,
                        password=secrets.token_urlsafe(36),
                        enabled=enabled,
                    ),
                ]
            )

        personal_sites = (
            ("Portal Reviewer's Home Site", "dev-user-001", "31991", "VA3X"),
            ("Morgan's Ridge Site", "dev-user-002", "31992", "VA3Y"),
            ("Casey's Personal Site", "dev-user-003", "31993", "VA3Z"),
        )
        for index, (name, owner, site_id, call_sign) in enumerate(personal_sites, start=16):
            site = Site(
                country_code="CA",
                site_code="31",
                site_id=site_id,
                name=name,
                location="Montréal, Québec" if index == 16 else "Ottawa, Ontario",
                description="Personally managed preview site.",
                numbering_mode="short",
                grid_square="FN31pr" if index == 16 else "FN25fi",
                latitude=45.5019 if index == 16 else 45.4215,
                longitude=-73.5674 if index == 16 else -75.6972,
                owner_subject=owner,
            )
            session.add(site)
            session.flush()
            session.add(
                SiteIdAllocation(
                    site_id=site_id, country_code="CA", site_code="31", site_pk=site.id
                )
            )
            repeater = Repeater(
                node_id=310499 + index - 16,
                site_id=site.id,
                call_sign=call_sign,
                frequency="146.940 MHz",
                tone="103.5 Hz",
                enabled=True,
                last_registration_at=datetime.now(timezone.utc) - timedelta(minutes=20),
                last_registration_ip=f"192.0.2.{90 + index}",
                registration_source="dev-preview",
            )
            session.add_all(
                [
                    repeater,
                    NodeCredential(
                        node_id=repeater.node_id,
                        password=secrets.token_urlsafe(36),
                        enabled=True,
                    ),
                ]
            )


def initialize_dev_preview(app):
    """Migrate and seed the one-process SQLite store and mount mock OIDC routes."""
    if not app.config.get("PORTAL_DEV_MODE"):
        return
    if app.config.get("PORTAL_ENVIRONMENT") != "development" or not (
        app.config.get("DEBUG") or app.config.get("TESTING")
    ):
        raise RuntimeError(
            "Backend preview requires PORTAL_ENVIRONMENT=development and Flask debug mode"
        )

    engine = app.extensions["portal_database"]
    with engine.begin() as connection:
        _run_migrations(connection)
    with app.app_context():
        _seed_preview_data()

    signing_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    public_key = JsonWebKey.import_key(signing_key.as_dict(is_private=False))
    app.extensions["portal_dev_signing_key"] = signing_key
    app.extensions["portal_dev_jwks"] = {"keys": [public_key.as_dict(is_private=False)]}
    app.config.update(
        PORTAL_OIDC_ISSUER=DEV_ISSUER,
        PORTAL_OIDC_JWKS_URL=None,
        PORTAL_API_AUDIENCE=DEV_AUDIENCE,
        PORTAL_JWT_ALGORITHMS="RS256",
        PORTAL_SYSTEM_SUPER_ADMIN_CLAIM_PATH="realm_access.roles",
        PORTAL_SYSTEM_SUPER_ADMIN_CLAIM_VALUE="System Super Admin",
    )
    app.extensions["portal_jwks_cache"] = _StaticJwksCache(app.extensions["portal_dev_jwks"])
    app.register_blueprint(dev_preview_bp)


class _StaticJwksCache:
    def __init__(self, jwks):
        self.jwks = JsonWebKey.import_key_set(jwks)

    def get(self):
        return self.jwks


@dev_preview_bp.get("/api/v1/dev-preview/users")
@dev_preview_bp.doc(summary="List mock OIDC accounts", security=[])
def list_dev_users():
    with get_session_factory()() as session:
        users = session.scalars(select(PortalUser).order_by(PortalUser.display_name)).all()
        admins = {subject for subject, *_rest, is_admin in USERS if is_admin}
        return jsonify(
            items=[
                {
                    "id": user.subject,
                    "name": user.display_name,
                    "username": user.username,
                    "email": f"{user.username}@example.invalid",
                    "is_system_super_admin": user.subject in admins,
                }
                for user in users
            ]
        )


@dev_preview_bp.post("/api/v1/dev-preview/token")
@dev_preview_bp.doc(summary="Issue a mock OIDC access token", security=[])
def issue_dev_token():
    data = request.get_json(silent=True) or {}
    subject = data.get("user_id")
    with get_session_factory()() as session:
        user = session.get(PortalUser, subject)
        if user is None:
            return jsonify(error="unknown_preview_user"), 400
        is_admin = subject == "dev-admin-001"
        now = datetime.now(timezone.utc)
        claims = {
            "iss": DEV_ISSUER,
            "sub": user.subject,
            "aud": DEV_AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
            "preferred_username": user.username,
            "name": user.display_name,
            "email": f"{user.username}@example.invalid",
            "realm_access": {"roles": ["System Super Admin"] if is_admin else []},
        }
        token = (
            JsonWebToken(["RS256"])
            .encode(
                {
                    "alg": "RS256",
                    "typ": "JWT",
                    "kid": current_app.extensions["portal_dev_signing_key"].as_dict()["kid"],
                },
                claims,
                current_app.extensions["portal_dev_signing_key"],
            )
            .decode("ascii")
        )
        return jsonify(access_token=token, token_type="Bearer", expires_in=900)
