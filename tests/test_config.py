"""Dynaconf loading and configuration validation tests."""

import pytest

from portal_backend.app import validate_production_settings
from portal_backend.config import DEFAULTS, flask_config, load_settings


def test_dynaconf_precedence_and_portal_prefix(tmp_path, monkeypatch):
    (tmp_path / "settings.toml").write_text(
        'display_name = "File Portal"\nbind_port = 5010\napi_audience = "file-api"\n',
        encoding="utf-8",
    )
    (tmp_path / ".secrets.toml").write_text('api_audience = "secret-file-api"\n', encoding="utf-8")
    monkeypatch.setenv("PORTAL_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PORTAL_DISPLAY_NAME", "Environment Portal")
    monkeypatch.setenv("PORTAL_BIND_PORT", "5011")
    monkeypatch.setenv("PORTAL_API_AUDIENCE", "environment-api")

    settings = load_settings(display_name="Explicit Portal")
    assert settings.display_name == "Explicit Portal"
    assert settings.bind_port == 5011
    assert settings.api_audience == "environment-api"
    assert flask_config(settings)["PORTAL_BIND_PORT"] == 5011
    assert "PORTAL_DATABASE_URL" in flask_config(load_settings())


def test_dynaconf_optional_blank_values_are_normalized(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PORTAL_DATABASE_URL", "")
    monkeypatch.setenv("PORTAL_OIDC_JWKS_URL", "  ")
    monkeypatch.setenv("PORTAL_SITE_CODES_FILE", "")
    monkeypatch.setenv("PORTAL_REGISTRATION_LIFETIME_SECONDS", "")
    settings = load_settings()
    assert settings.database_url is None
    assert settings.oidc_jwks_url is None
    assert settings.site_codes_file is None
    assert settings.registration_lifetime_seconds is None


@pytest.mark.parametrize("port", ["zero", "0", "65536"])
def test_dynaconf_rejects_invalid_bind_ports(tmp_path, monkeypatch, port):
    monkeypatch.setenv("PORTAL_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PORTAL_BIND_PORT", port)
    with pytest.raises(ValueError, match="bind port"):
        load_settings()


@pytest.mark.parametrize("lifetime", ["invalid", "0", "-1"])
def test_dynaconf_rejects_invalid_registration_lifetime(tmp_path, monkeypatch, lifetime):
    monkeypatch.setenv("PORTAL_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PORTAL_REGISTRATION_LIFETIME_SECONDS", lifetime)
    with pytest.raises(ValueError, match="registration lifetime"):
        load_settings()


def test_flask_config_maps_all_configured_keys():
    settings = load_settings(
        display_name="Example",
        database_url="sqlite:///example.db",
        bind_host="0.0.0.0",
        bind_port=5050,
    )
    mapped = flask_config(settings)
    assert len(mapped) == len(DEFAULTS)
    assert mapped["PORTAL_DISPLAY_NAME"] == "Example"
    assert mapped["PORTAL_DATABASE_URL"] == "sqlite:///example.db"
    assert mapped["PORTAL_BIND_HOST"] == "0.0.0.0"
    assert mapped["PORTAL_BIND_PORT"] == 5050


def test_sqlite_driver_scheme_still_enables_foreign_keys():
    from portal_backend.database import create_database_engine

    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    try:
        assert engine.dialect.name == "sqlite"
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        engine.dispose()


def test_production_app_requires_core_configuration():
    with pytest.raises(RuntimeError, match="PORTAL_DATABASE_URL.*PORTAL_OIDC_ISSUER"):
        validate_production_settings(
            {
                "PORTAL_ENVIRONMENT": "production",
                "PORTAL_DATABASE_URL": None,
                "PORTAL_OIDC_ISSUER": "",
                "PORTAL_API_AUDIENCE": "",
            }
        )


@pytest.mark.parametrize(
    "config",
    [
        {"TESTING": True, "PORTAL_ENVIRONMENT": "production"},
        {"PORTAL_ENVIRONMENT": "development"},
        {"PORTAL_ENVIRONMENT": "development", "PORTAL_DEV_MODE": True},
    ],
)
def test_nonproduction_and_test_modes_can_omit_production_configuration(config):
    validate_production_settings(config)
