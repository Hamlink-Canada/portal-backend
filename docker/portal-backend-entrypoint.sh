#!/bin/sh
set -eu

if [ "$#" -gt 0 ] && [ "$1" = "serve" ]; then
    shift
    workers=${WEB_CONCURRENCY:-2}
    if python -c 'from portal_backend.config import load_settings; raise SystemExit(0 if load_settings().dev_mode else 1)'; then
        workers=1
    fi
    exec opentelemetry-instrument gunicorn \
        --worker-class sync \
        --workers "$workers" \
        --bind "${PORTAL_BIND_HOST:-0.0.0.0}:${PORTAL_BIND_PORT:-5000}" \
        --access-logfile - \
        --error-logfile - \
        --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
        --timeout "${GUNICORN_TIMEOUT:-30}" \
        portal_backend.wsgi:app "$@"
fi

exec "$@"
