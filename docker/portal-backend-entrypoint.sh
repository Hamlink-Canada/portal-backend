#!/bin/sh
set -eu

if [ "$#" -gt 0 ] && [ "$1" = "serve" ]; then
    shift
    exec opentelemetry-instrument gunicorn \
        --worker-class sync \
        --workers "${WEB_CONCURRENCY:-2}" \
        --bind "${PORTAL_BIND_HOST:-0.0.0.0}:${PORTAL_BIND_PORT:-5000}" \
        --access-logfile - \
        --error-logfile - \
        --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
        --timeout "${GUNICORN_TIMEOUT:-30}" \
        portal_backend.wsgi:app "$@"
fi

exec "$@"
