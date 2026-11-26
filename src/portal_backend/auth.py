"""Bearer-token validation for access tokens issued by the configured IdP."""

import json
import threading
import time
from functools import wraps
from ipaddress import ip_address
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError
from flask import current_app, g, jsonify, request

try:
    from opentelemetry import trace
except ImportError:  # Telemetry is an optional extra.
    trace = None


def _set_trace_attribute(key, value):
    if trace is None:
        return
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute(key, value)


ALLOWED_ASYMMETRIC_ALGORITHMS = frozenset(
    {
        "ES256",
        "ES384",
        "ES512",
        "PS256",
        "PS384",
        "PS512",
        "RS256",
        "RS384",
        "RS512",
    }
)
JWKS_MAX_BYTES = 1_048_576
JWKS_CACHE_SECONDS = 300
TOKEN_MAX_LENGTH = 32_768


class JwksUnavailable(Exception):
    """The configured identity provider's signing keys could not be loaded."""


class JwksCache:
    """Discover and cache a bounded in-memory copy of the provider's signing keys."""

    def __init__(self, issuer, url=None, *, allow_local_http=False):
        self.issuer = issuer
        self.url = url
        self.allow_local_http = allow_local_http
        self._validate_url(issuer, allow_local_http)
        if url:
            self._validate_url(url, allow_local_http)
        self._key_set = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get(self):
        now = time.monotonic()
        if self._key_set is not None and now < self._expires_at:
            return self._key_set

        with self._lock:
            now = time.monotonic()
            if self._key_set is not None and now < self._expires_at:
                return self._key_set
            try:
                jwks_url = self.url
                if not jwks_url:
                    self._validate_url(
                        f"{self.issuer.rstrip('/')}/.well-known/openid-configuration",
                        self.allow_local_http,
                    )
                    discovery = self._fetch_json(
                        f"{self.issuer.rstrip('/')}/.well-known/openid-configuration",
                        allow_local_http=self.allow_local_http,
                    )
                    if discovery.get("issuer") != self.issuer:
                        raise JwksUnavailable("discovery issuer does not match configuration")
                    jwks_url = discovery.get("jwks_uri")
                    if not isinstance(jwks_url, str) or not jwks_url:
                        raise JwksUnavailable("discovery document has no JWKS URI")
                self._validate_url(jwks_url, self.allow_local_http)
                self._key_set = JsonWebKey.import_key_set(
                    self._fetch_json(jwks_url, allow_local_http=self.allow_local_http)
                )
                self._expires_at = time.monotonic() + JWKS_CACHE_SECONDS
                return self._key_set
            except (OSError, ValueError, TypeError, JoseError) as error:
                self._key_set = None
                self._expires_at = 0.0
                raise JwksUnavailable("unable to load provider signing keys") from error

    @staticmethod
    def _validate_url(url, allow_local_http=False):
        try:
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            local = host.lower() == "localhost"
            try:
                local = local or ip_address(host).is_loopback
            except ValueError:
                pass
            if parsed.scheme != "https" and not (
                allow_local_http and parsed.scheme == "http" and local
            ):
                raise JwksUnavailable("identity provider URLs must use HTTPS")
            if not host or parsed.username or parsed.password or parsed.fragment:
                raise JwksUnavailable("invalid identity provider URL")
        except (TypeError, ValueError) as error:
            if isinstance(error, JwksUnavailable):
                raise
            raise JwksUnavailable("invalid identity provider URL") from error

    @staticmethod
    def _fetch_json(url, *, allow_local_http=False):
        JwksCache._validate_url(url, allow_local_http)
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=5) as response:
            JwksCache._validate_url(getattr(response, "geturl", lambda: url)(), allow_local_http)
            body = response.read(JWKS_MAX_BYTES + 1)
        if len(body) > JWKS_MAX_BYTES:
            raise JwksUnavailable("identity provider response exceeds the configured limit")
        document = json.loads(body)
        if not isinstance(document, dict):
            raise JwksUnavailable("identity provider response must be a JSON object")
        return document


def _configured_algorithms():
    raw = current_app.config.get("PORTAL_JWT_ALGORITHMS", "RS256")
    algorithms = [value.strip() for value in raw.split(",") if value.strip()]
    if not algorithms or any(value not in ALLOWED_ASYMMETRIC_ALGORITHMS for value in algorithms):
        return None
    return algorithms


def validate_access_token(token):
    """Verify token signature and required issuer, audience, and time claims."""
    issuer = current_app.config.get("PORTAL_OIDC_ISSUER", "")
    audience = current_app.config.get("PORTAL_API_AUDIENCE")
    algorithms = _configured_algorithms()
    key_cache = current_app.extensions.get("portal_jwks_cache")
    if not issuer or not audience or not algorithms or key_cache is None:
        raise JwksUnavailable("token validation is not configured")

    claims = JsonWebToken(algorithms).decode(
        token,
        key=key_cache.get(),
        claims_options={
            "exp": {"essential": True},
            "iat": {"essential": True},
            "iss": {"essential": True, "value": issuer},
            "sub": {"essential": True},
        },
    )
    claims.validate(leeway=60)

    token_audience = claims.get("aud")
    audiences = token_audience if isinstance(token_audience, list) else [token_audience]
    if audience not in audiences:
        raise ValueError("token audience is not valid")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise ValueError("token subject is not valid")
    return claims


def require_access_token(view):
    """Protect a Flask route with an OIDC provider's Bearer access token."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        _set_trace_attribute("client.address", request.remote_addr or "")
        authorization = request.headers.get("Authorization", "")
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            _set_trace_attribute("auth.failure.category", "missing_bearer_token")
            response = jsonify(error="unauthorized")
            response.status_code = 401
            response.headers["WWW-Authenticate"] = 'Bearer realm="portal"'
            return response
        if len(parts[1]) > TOKEN_MAX_LENGTH:
            _set_trace_attribute("auth.failure.category", "bearer_token_too_large")
            response = jsonify(error="unauthorized")
            response.status_code = 401
            response.headers["WWW-Authenticate"] = 'Bearer realm="portal", error="invalid_token"'
            return response

        try:
            g.access_token_claims = validate_access_token(parts[1])
        except JwksUnavailable:
            _set_trace_attribute("auth.failure.category", "identity_provider_unavailable")
            return jsonify(error="identity_provider_unavailable"), 503
        except (JoseError, ValueError, TypeError, KeyError):
            _set_trace_attribute("auth.failure.category", "invalid_bearer_token")
            response = jsonify(error="unauthorized")
            response.status_code = 401
            response.headers["WWW-Authenticate"] = 'Bearer realm="portal", error="invalid_token"'
            return response
        _set_trace_attribute("enduser.id", g.access_token_claims["sub"])
        return view(*args, **kwargs)

    return wrapped


def initialize_jwks_cache(app):
    issuer = app.config.get("PORTAL_OIDC_ISSUER")
    jwks_url = app.config.get("PORTAL_OIDC_JWKS_URL")
    if issuer:
        allow_local_http = (
            app.config.get("PORTAL_DEV_MODE") is True
            and app.config.get("PORTAL_ENVIRONMENT") == "development"
        )
        app.extensions["portal_jwks_cache"] = JwksCache(
            issuer, jwks_url or None, allow_local_http=allow_local_http
        )
