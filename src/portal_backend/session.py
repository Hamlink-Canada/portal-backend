"""Authenticated API endpoint exposing the caller's basic identity claims."""

from flask import g, jsonify
from flask_smorest import Blueprint

from portal_backend.auth import require_access_token
from portal_backend.utils.authorization import is_system_super_admin

session_bp = Blueprint("session", __name__, description="Authenticated caller identity.")


@session_bp.get("/api/v1/session")
@require_access_token
@session_bp.doc(
    summary="Return the identity represented by the access token",
    security=[{"bearerAuth": []}],
)
def get_session():
    claims = g.access_token_claims
    response = jsonify(
        user={
            "id": claims["sub"],
            "username": claims.get("preferred_username"),
            "name": claims.get("name"),
            "email": claims.get("email"),
            "is_system_super_admin": is_system_super_admin(claims),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response
