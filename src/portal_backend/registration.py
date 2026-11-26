"""Helpers for deriving live registration freshness."""

from datetime import datetime, timedelta, timezone


def registration_freshness(last_registration_at, *, lifetime_seconds, now=None):
    """Return ``current``, ``stale``, or ``unknown`` from the latest observation.

    ``enabled`` is an administrative control and is intentionally not part of
    this calculation. The Kafka metadata updater writes the latest registration
    time; deployments supply Registration Lifetime as configuration.
    """
    if last_registration_at is None or lifetime_seconds is None:
        return "unknown"
    try:
        lifetime_seconds = int(lifetime_seconds)
    except (TypeError, ValueError):
        return "unknown"
    if lifetime_seconds <= 0:
        return "unknown"

    observed_at = last_registration_at
    if not isinstance(observed_at, datetime):
        return "unknown"
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    return (
        "current"
        if observed_at + timedelta(seconds=lifetime_seconds) > current_time
        else "stale"
    )
