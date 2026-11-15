# Portal backend

The Portal backend is the Python service for generic Portal deployments.
Deployment-specific display names, hostnames, and service configuration belong
in deployment configuration rather than reusable code.

## Project boundaries

- Portal owns its Site and Repeater models and backend behavior.
- Public models and cross-product code belong in the separate `pyCommon`
  project; HamClub and HamNetControl behavior does not belong here.
- The web application does not expose privileged administrative CLI functions.
  `portal-admin` provides explicit local maintenance commands.
- The OpenAPI document is generated from the route registrations and Marshmallow
  schemas with the Flask-Smorest CLI.

## Development

The backend uses Flask, SQLAlchemy, Alembic, and the shared `rpt-common`
package. Configure a database URL and install the project with a Python 3.13+
environment. The `uv` source override uses the sibling `../pyCommon` checkout
for local development. `.env.example` lists the supported environment
variables; export their values in your shell or deployment environment.
Dynaconf also reads `settings.toml` and `.secrets.toml` from the current
directory by default; set `PORTAL_CONFIG_DIR` to use another directory. The
`.env.example` file is a reference and is not loaded automatically. Keep
secrets out of source control. Set the OIDC issuer and API audience to match
your provider. The backend discovers the provider's JWKS URI from standard
OpenID Connect metadata; `PORTAL_OIDC_JWKS_URL` is available as an optional
override. Configure the provider to issue access tokens for the API audience.
The API accepts bearer access tokens only; it does not start a browser login
flow or accept ID tokens as API credentials.

Run the backend tests with:

```bash
uv run pytest
```

The test suite uses an isolated SQLite database per test and mocks access-token
validation and external country lookup for deterministic API coverage. OIDC
key parsing and signature verification also have focused tests using generated
test signing keys.

Set `PORTAL_REGISTRATION_LIFETIME_SECONDS` to the Registration Lifetime used by
the registration systems. Repeater administrative enablement and live
registration freshness are separate values: freshness is derived from the
latest registration observation and this configured lifetime. The Repeater
schema stores only the latest observation (time, address, and source); a
separate Repeater Metadata Updater is responsible for updating it. The schema
does not store registration history.

The API validates JSON request bodies with Marshmallow schemas at the Flask
route boundary. Flask-Smorest and APISpec derive the OpenAPI document from those
schemas and the registered routes. Generate the current document after route
or schema changes with:

```bash
mkdir -p api
uv run flask --app portal_backend.app:create_app openapi write --format=json api/openapi.json
```

The app does not serve the OpenAPI document by default; the command writes it
for review or publication with deployment documentation.

Site creation requires a Maidenhead locator with at least four characters
(two field letters and two square digits); two optional subsquare letters add
precision. Portal derives the locator center and country with
`coordinates2country`; a user may also
provide a latitude/longitude pair to refine the stored point and country lookup.
When supplied, the precise point must be within the grid square. Site Codes are
loaded from the bundled legacy `sitecodes.json` mapping by default; set
`PORTAL_SITE_CODES_FILE` to a deployment-managed replacement. The Site ID
allocator reserves IDs permanently after deletion. An operator must run
`portal-admin clear-site-id SITE_ID` to release one explicitly.

The Portal database role needs write access to Portal-owned tables and the
shared `node_credentials` table used by Central Auth. Central Auth should keep
its least-privilege read access to credentials. Apply the pyCommon schema first
so shared `entities` and `node_credentials` tables exist, then apply Portal's
Alembic migrations. Repeaters receive unique generated secrets; Portal returns
a secret only on creation or rotation and never includes it in repeater lists.

When `PORTAL_DATABASE_URL` is set, the Flask app attaches a SQLAlchemy engine
and session factory. Startup does not create or migrate tables.

```bash
export PORTAL_DATABASE_URL='sqlite:///portal.db'
uv sync
uv run python -m portal_backend
```

Dynaconf loads defaults first, then `settings.toml` and `.secrets.toml`, then
`PORTAL_*` environment variables, and finally any explicit app-factory
overrides. For example, `settings.toml` may contain `database_url =
"sqlite:///portal.db"` and `bind_port = 5000`; environment names use the
`PORTAL_` prefix (such as `PORTAL_DATABASE_URL` and `PORTAL_BIND_PORT`).

The app does not create or migrate database tables at startup. The Portal schema
references shared `entities` and writes shared `node_credentials`, so apply the
pyCommon schema to the same database first, using its separate migration
credential. Then apply Portal's migrations with a database account authorized
to modify the Portal schema:

Portal currently has one `0001_portal_init.py` migration containing the complete
Portal schema for a fresh installation. Incremental migration files will be
added after the initial full build is complete.

For pyCommon, the supported input is a mounted URL file or the
`RPT_MIGRATION_DATABASE_URL` environment variable (use a shell secret store for
local development rather than committing it). For example, with a mounted URL
file:

```bash
rpt-common-migrate upgrade --url-file /run/secrets/migration-url
uv run alembic -c alembic.ini upgrade head
```

### Backend development preview

The mock OIDC preview is opt-in and refuses to start unless both
`PORTAL_ENVIRONMENT=development` and `PORTAL_DEV_MODE=true` are set and Flask is
running with debug enabled (or under tests). The normal Python entry point turns
on Flask debug mode only when `PORTAL_DEV_MODE=true`; production WSGI startup
does not satisfy the guard. Preview startup forces `sqlite:///:memory:`, applies
the current pyCommon and Portal Alembic migrations to the shared in-memory
connection, and seeds the preview records. The user picker issues a short-lived
RS256 access token signed by an ephemeral process key. The API validates that
token through the same bearer-token validation path used for the configured
OIDC provider. Preview routes are not registered when the mode is off.

CRUD operations use the standard backend API and remain visible for the life of
the process. Restarting the backend discards edits and restores the original
seed. For local frontend review, enable `VITE_PORTAL_DEV_MODE=true` and
`VITE_PORTAL_BACKEND_PREVIEW=true` in the frontend `.env`; these options are
active only under Vite's development server and are excluded from production
build behavior. Do not deploy either preview flag.

Start the backend preview with:

```bash
PORTAL_ENVIRONMENT=development PORTAL_DEV_MODE=true uv run python -m portal_backend
```

## Backend structure

- `src/portal_backend/` — Flask app factory, database, authentication, and session.
- `src/portal_backend/api_sec/` — Marshmallow request schemas and API security.
- `src/portal_backend/routes/` — route modules split by base API resource.
- `src/portal_backend/utils/` — reusable backend helpers.
- `src/portal_backend/models/` — Portal-owned ORM models.
- `api/` — generated API reference output.
- `migrations/` — Portal-owned Alembic revisions.

The schema owns Portal's Site, Repeater, Entity profile, and Entity membership
metadata; it also allocates permanent Site ID reservations and stores
admin-controlled repeater enablement and latest registration observations.
Freshness is derived with the configured Registration Lifetime. Site and
Repeater changes are authorized through Site/Entity ownership, with
System Super Admin recognized through a configurable OIDC claim. The Portal
API exposes Entity, Site, and Repeater list/create/edit flows. The management
frontend performs OIDC Authorization Code with PKCE and sends the access token
as a bearer token; the backend validates its signature, exact issuer, audience,
and time claims.
