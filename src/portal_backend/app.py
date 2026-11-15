"""Flask application factory for the Portal backend."""

from flask import Flask
from flask_smorest import Api
from marshmallow import fields

from portal_backend.api_sec.schemas import StrictBoolean
from portal_backend.auth import initialize_jwks_cache
from portal_backend.config import flask_config, load_settings
from portal_backend.database import initialize_database
from portal_backend.routes import (
    health_bp,
    orgs_bp,
    repeaters_bp,
    site_locations_bp,
    sites_bp,
    users_bp,
)
from portal_backend.session import session_bp


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(flask_config(load_settings()), JSON_SORT_KEYS=False)
    if config:
        app.config.update(config)

    if app.config.get("PORTAL_DEV_MODE"):
        if app.config.get("PORTAL_ENVIRONMENT") != "development":
            raise ValueError("PORTAL_DEV_MODE is only allowed with PORTAL_ENVIRONMENT=development")
        if not (app.config.get("DEBUG") or app.config.get("TESTING")):
            raise RuntimeError(
                "Backend preview cannot start outside a debug or test Flask app in development"
            )
        app.config["PORTAL_DATABASE_URL"] = "sqlite:///:memory:"

    app.config.setdefault("API_TITLE", f"{app.config['PORTAL_DISPLAY_NAME']} API")
    app.config.setdefault("API_VERSION", "v1")
    app.config.setdefault("OPENAPI_VERSION", "3.0.3")
    app.config.setdefault("OPENAPI_URL_PREFIX", None)
    api = Api(app)
    api.spec.components.security_scheme(
        "bearerAuth",
        {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
    )
    api.register_field(StrictBoolean, fields.Boolean)
    initialize_database(app)
    initialize_jwks_cache(app)
    if app.config.get("PORTAL_DEV_MODE"):
        from portal_backend.dev_preview import initialize_dev_preview

        initialize_dev_preview(app)
    api.register_blueprint(health_bp)
    api.register_blueprint(session_bp)
    api.register_blueprint(site_locations_bp)
    api.register_blueprint(sites_bp)
    api.register_blueprint(repeaters_bp)
    api.register_blueprint(orgs_bp)
    api.register_blueprint(users_bp)
    return app
