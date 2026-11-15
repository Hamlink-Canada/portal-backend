"""Dynaconf settings for the Portal backend.

Configuration files are read from ``PORTAL_CONFIG_DIR`` (or the current
directory). Environment variables use the ``PORTAL_`` prefix.
"""

import os
from pathlib import Path

from dynaconf import Dynaconf
from dynaconf.loaders import env_loader

DEFAULTS = {
    "display_name": "Portal",
    "database_url": None,
    "bind_host": "127.0.0.1",
    "bind_port": 5000,
    "oidc_issuer": "",
    "oidc_jwks_url": "",
    "api_audience": "",
    "jwt_algorithms": "RS256",
    "registration_lifetime_seconds": None,
    "site_codes_file": None,
    "system_super_admin_claim_path": "",
    "system_super_admin_claim_value": "System Super Admin",
    "environment": "production",
    "dev_mode": False,
}


def load_settings(**overrides):
    """Load defaults, optional TOML files, PORTAL_* environment, and overrides."""
    directory = Path(os.environ.get("PORTAL_CONFIG_DIR", ".")).resolve()
    settings = Dynaconf(
        envvar_prefix="PORTAL",
        environments=False,
        merge_enabled=True,
        settings_files=[],
        load_dotenv=False,
    )
    settings.update(DEFAULTS)
    for name in ("settings.toml", ".secrets.toml"):
        path = directory / name
        if path.exists():
            settings.load_file(path=str(path))

    # Read environment values after defaults and files, matching the other
    # Python services in this workspace.
    env_loader.load(settings, env="PORTAL")
    settings.update(overrides, merge=True)

    for key in ("database_url", "oidc_jwks_url", "site_codes_file"):
        value = getattr(settings, key)
        if isinstance(value, str) and not value.strip():
            setattr(settings, key, None)

    settings.dev_mode = str(settings.dev_mode).strip().lower() in {"1", "true", "yes", "on"}
    settings.environment = str(settings.environment).strip().lower()
    if settings.dev_mode and settings.environment != "development":
        raise ValueError("PORTAL_DEV_MODE is only allowed with PORTAL_ENVIRONMENT=development")

    lifetime = settings.registration_lifetime_seconds
    if isinstance(lifetime, str) and not lifetime.strip():
        settings.registration_lifetime_seconds = None
    elif lifetime is not None:
        try:
            settings.registration_lifetime_seconds = int(lifetime)
        except (TypeError, ValueError) as error:
            raise ValueError("registration lifetime must be a positive integer") from error
        if settings.registration_lifetime_seconds <= 0:
            raise ValueError("registration lifetime must be a positive integer")

    try:
        settings.bind_port = int(settings.bind_port)
    except (TypeError, ValueError) as error:
        raise ValueError("bind port must be an integer from 1 to 65535") from error
    if not 1 <= settings.bind_port <= 65535:
        raise ValueError("bind port must be an integer from 1 to 65535")

    return settings


def flask_config(settings):
    """Map Dynaconf's generic names to the Flask config keys used by Portal."""
    return {f"PORTAL_{key.upper()}": getattr(settings, key) for key in DEFAULTS}
