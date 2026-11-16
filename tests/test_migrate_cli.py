"""Credential selection and schema ordering for explicit Portal migrations."""

from pathlib import Path

import pytest
from rpt_common.migrate import upgrade as upgrade_common

from portal_backend.migrate_cli import _database_url
from portal_backend.migrate_cli import upgrade as upgrade_portal


def test_migration_url_precedence(tmp_path):
    url_file = tmp_path / "database-url"
    url_file.write_text("sqlite:///from-file.sqlite\n", encoding="utf-8")
    environment = {
        "PORTAL_MIGRATION_DATABASE_URL": "sqlite:///from-migration-env.sqlite",
        "PORTAL_DATABASE_URL": "sqlite:///from-app-env.sqlite",
    }

    assert _database_url(str(url_file), environment) == "sqlite:///from-file.sqlite"
    assert _database_url(environ=environment) == "sqlite:///from-migration-env.sqlite"
    assert _database_url(environ={"PORTAL_DATABASE_URL": "sqlite:///from-app-env.sqlite"}) == (
        "sqlite:///from-app-env.sqlite"
    )


def test_migration_url_requires_a_value():
    with pytest.raises(ValueError, match="PORTAL_MIGRATION_DATABASE_URL"):
        _database_url(environ={})


def test_migrations_accept_one_shared_database_url(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'shared.sqlite'}"
    monkeypatch.setenv("RPT_MIGRATION_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTAL_MIGRATION_DATABASE_URL", database_url)

    upgrade_common(database_url)
    upgrade_portal(database_url)

    assert Path(tmp_path / "shared.sqlite").is_file()
