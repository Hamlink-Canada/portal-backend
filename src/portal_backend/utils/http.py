"""Small common HTTP response helpers."""

from flask import jsonify


def failure(error, status):
    """Return a standard Portal API error payload."""
    return jsonify(error=error), status


def no_store(response):
    """Prevent caches from storing responses containing registration secrets."""
    response.headers["Cache-Control"] = "no-store"
    return response
