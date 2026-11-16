# syntax=docker/dockerfile:1.7
# Required named context: --build-context pycommon=../pyCommon
FROM ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 AS uv
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /build/Portal-Backend

# Materialize the shared editable dependency beside the backend project so the
# locked build uses the exact named-context revision supplied by CI or Docker.
COPY --from=pycommon /src/ /build/pyCommon/src/
COPY --from=pycommon /pyproject.toml /README.md /LICENSE /build/pyCommon/
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable --extra otel --python /usr/local/bin/python

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS runtime
ARG VCS_REF=development
LABEL org.opencontainers.image.source="https://github.com/Hamlink-Canada/Portal-Backend" \
      org.opencontainers.image.revision="$VCS_REF"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORTAL_CONFIG_DIR=/config \
    PORTAL_BIND_HOST=0.0.0.0 \
    PORTAL_BIND_PORT=5000 \
    OTEL_TRACES_EXPORTER=none \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none \
    WEB_CONCURRENCY=2
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations/ migrations/
COPY docker/portal-backend-entrypoint.sh /usr/local/bin/portal-backend-entrypoint
RUN chmod 0755 /usr/local/bin/portal-backend-entrypoint

USER 10001:10001
EXPOSE 5000/tcp
HEALTHCHECK --interval=30s --timeout=15s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORTAL_BIND_PORT','5000')+'/api/v1/health', timeout=10)"]
ENTRYPOINT ["/usr/local/bin/portal-backend-entrypoint"]
CMD ["serve"]
