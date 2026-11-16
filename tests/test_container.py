"""Opt-in acceptance checks for a locally built Portal backend image."""

import json
import os
import secrets
import subprocess
import time

import pytest


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.PIPE).strip()


def test_production_image_rejects_missing_required_configuration():
    image = os.environ.get("RPT_TEST_IMAGE")
    if not image:
        pytest.skip("set RPT_TEST_IMAGE to a locally built image")

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--env",
            "OTEL_TRACES_EXPORTER=none",
            "--env",
            "OTEL_METRICS_EXPORTER=none",
            "--env",
            "OTEL_LOGS_EXPORTER=none",
            image,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Missing required production settings" in result.stderr


def test_nonroot_image_starts_and_passes_healthcheck():
    image = os.environ.get("RPT_TEST_IMAGE")
    if not image:
        pytest.skip("set RPT_TEST_IMAGE to a locally built image")

    name = "portal-backend-acceptance-" + secrets.token_hex(4)
    try:
        docker(
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            "none",
            "--env",
            "PORTAL_DATABASE_URL=sqlite:////tmp/portal-smoke.sqlite",
            "--env",
            "PORTAL_OIDC_ISSUER=https://identity.example.invalid/realms/test",
            "--env",
            "PORTAL_API_AUDIENCE=portal-api",
            "--env",
            "OTEL_TRACES_EXPORTER=none",
            "--env",
            "OTEL_METRICS_EXPORTER=none",
            "--env",
            "OTEL_LOGS_EXPORTER=none",
            image,
        )
        deadline = time.monotonic() + 45
        while True:
            info = json.loads(docker("inspect", name))[0]
            health = info["State"].get("Health", {}).get("Status")
            if health == "healthy":
                break
            if info["State"]["Status"] != "running" or time.monotonic() >= deadline:
                logs = subprocess.run(
                    ["docker", "logs", name], capture_output=True, text=True
                )
                pytest.fail(f"container did not become healthy:\n{logs.stdout}\n{logs.stderr}")
            time.sleep(0.25)

        assert info["Config"]["User"] == "10001:10001"
        assert docker("exec", name, "id", "-u") == "10001"
        docker("stop", "--time", "30", name)
        assert json.loads(docker("inspect", name))[0]["State"]["ExitCode"] == 0

        # Shared schema goes first; Portal migration credentials fall back to
        # the application URL when no separate value is supplied.
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--env",
            "RPT_MIGRATION_DATABASE_URL=sqlite:////tmp/portal-migrations.sqlite",
            "--env",
            "PORTAL_DATABASE_URL=sqlite:////tmp/portal-migrations.sqlite",
            image,
            "/bin/sh",
            "-c",
            "rpt-common-migrate upgrade && portal-migrate upgrade",
        )
    finally:
        subprocess.run(["docker", "rm", "--force", "--volumes", name], capture_output=True)
