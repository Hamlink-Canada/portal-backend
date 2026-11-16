"""Production WSGI application for Gunicorn."""

from portal_backend.app import create_app, validate_production_settings

app = create_app()
validate_production_settings(app.config)
