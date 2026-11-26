"""HTTP route blueprints, split by API resource."""

from portal_backend.routes.health import health_bp
from portal_backend.routes.orgs import orgs_bp
from portal_backend.routes.repeaters import repeaters_bp
from portal_backend.routes.site_locations import site_locations_bp
from portal_backend.routes.sites import sites_bp
from portal_backend.routes.users import users_bp

__all__ = ["orgs_bp", "health_bp", "repeaters_bp", "site_locations_bp", "sites_bp", "users_bp"]
