"""Search the deployment-synchronized user directory."""

from flask import jsonify
from flask_smorest import Blueprint
from sqlalchemy import String, cast, or_, select

from portal_backend.api_sec.schemas import UserSearchArgsSchema
from portal_backend.auth import require_access_token
from portal_backend.database import get_session_factory
from portal_backend.models import PortalUser
from portal_backend.utils.http import failure

users_bp = Blueprint("users", __name__, description="Synchronized user directory.")


@users_bp.get("/api/v1/users")
@require_access_token
@users_bp.doc(summary="Search synchronized users by name or call sign", security=[{"bearerAuth": []}])
@users_bp.arguments(UserSearchArgsSchema, location="query")
def search_users(args):
    term = args["q"].strip()
    if len(term) < 2:
        return jsonify(items=[])
    pattern = f"%{term}%"
    try:
        with get_session_factory()() as session:
            users = session.scalars(
                select(PortalUser)
                .where(
                    or_(
                        PortalUser.display_name.ilike(pattern),
                        PortalUser.username.ilike(pattern),
                        cast(PortalUser.call_signs, String).ilike(pattern),
                    )
                )
                .order_by(PortalUser.display_name, PortalUser.subject)
                .limit(25)
            ).all()
            return jsonify(
                items=[
                    {
                        "subject": user.subject,
                        "display_name": user.display_name,
                        "call_signs": user.call_signs,
                    }
                    for user in users
                ]
            )
    except RuntimeError:
        return failure("database_unavailable", 503)
