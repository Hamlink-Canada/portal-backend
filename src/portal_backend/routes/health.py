"""Public service health route."""

from flask import jsonify
from flask_smorest import Blueprint

health_bp = Blueprint("health", __name__, description="Service health operations.")


@health_bp.get("/api/v1/health")
@health_bp.doc(summary="Report service availability", security=[])
def get_health():
    return jsonify(status="ok")
