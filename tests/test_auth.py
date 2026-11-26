"""Bearer authentication and OIDC discovery behavior."""

import pytest
from authlib.jose import JsonWebKey, JsonWebToken

import portal_backend.auth as auth

REAL_VALIDATE_ACCESS_TOKEN = auth.validate_access_token


def test_jwks_cache_discovers_keys_once_and_caches_them(monkeypatch):
    cache = auth.JwksCache("https://issuer.example/realm")
    responses = {
        "https://issuer.example/realm/.well-known/openid-configuration": {
            "issuer": "https://issuer.example/realm",
            "jwks_uri": "https://issuer.example/realm/keys",
        },
        "https://issuer.example/realm/keys": {"keys": [{"kid": "one"}]},
    }
    fetched = []
    imported = object()
    monkeypatch.setattr(
        cache, "_fetch_json", lambda url, **kwargs: fetched.append(url) or responses[url]
    )
    monkeypatch.setattr(auth.JsonWebKey, "import_key_set", lambda value: imported)

    assert cache.get() is imported
    assert cache.get() is imported
    assert fetched == [
        "https://issuer.example/realm/.well-known/openid-configuration",
        "https://issuer.example/realm/keys",
    ]


def test_jwks_cache_rejects_mismatched_discovery_issuer(monkeypatch):
    cache = auth.JwksCache("https://issuer.example/realm")
    monkeypatch.setattr(
        cache,
        "_fetch_json",
        lambda url, **kwargs: {
            "issuer": "https://attacker.example",
            "jwks_uri": "https://attacker/keys",
        },
    )
    with pytest.raises(auth.JwksUnavailable, match="issuer does not match"):
        cache.get()


def test_jwks_cache_rejects_oversized_or_non_object_documents(monkeypatch):
    monkeypatch.setattr(auth, "urlopen", lambda *args, **kwargs: _Response(b"[]"))
    with pytest.raises(auth.JwksUnavailable, match="JSON object"):
        auth.JwksCache._fetch_json("https://issuer.example/keys")
    monkeypatch.setattr(
        auth,
        "urlopen",
        lambda *args, **kwargs: _Response(b" " * (auth.JWKS_MAX_BYTES + 1)),
    )
    with pytest.raises(auth.JwksUnavailable, match="exceeds"):
        auth.JwksCache._fetch_json("https://issuer.example/keys")


def test_jwks_urls_require_https_except_loopback_preview():
    with pytest.raises(auth.JwksUnavailable, match="HTTPS"):
        auth.JwksCache._validate_url("http://issuer.example/keys")
    auth.JwksCache._validate_url("http://127.0.0.1:8080/keys", allow_local_http=True)
    with pytest.raises(auth.JwksUnavailable, match="HTTPS"):
        auth.JwksCache._validate_url("http://issuer.example/keys", allow_local_http=True)


def test_jwks_fetch_rejects_https_downgrade_redirect(monkeypatch):
    class Redirected(_Response):
        def geturl(self):
            return "http://issuer.example/keys"

    monkeypatch.setattr(auth, "urlopen", lambda *args, **kwargs: Redirected(b"{}"))
    with pytest.raises(auth.JwksUnavailable, match="HTTPS"):
        auth.JwksCache._fetch_json("https://issuer.example/keys")


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, count):
        return self.body[:count]


def test_configured_algorithms_allow_only_configured_asymmetric_algorithms(app):
    with app.app_context():
        assert auth._configured_algorithms() == ["RS256"]
        app.config["PORTAL_JWT_ALGORITHMS"] = "RS256, ES256"
        assert auth._configured_algorithms() == ["RS256", "ES256"]
        app.config["PORTAL_JWT_ALGORITHMS"] = "none"
        assert auth._configured_algorithms() is None
        app.config["PORTAL_JWT_ALGORITHMS"] = ""
        assert auth._configured_algorithms() is None


def test_access_token_validation_requires_complete_configuration(app):
    with app.app_context(), pytest.raises(auth.JwksUnavailable, match="not configured"):
        REAL_VALIDATE_ACCESS_TOKEN("opaque-token")


def test_access_token_validation_checks_issuer_subject_audience_and_claims(app, monkeypatch):
    class Claims(dict):
        def validate(self, leeway):
            assert leeway == 60

    claims = Claims(
        {
            "sub": "alice",
            "iss": "https://issuer.example",
            "aud": ["portal-api", "other"],
            "iat": 10,
            "exp": 100,
        }
    )

    class FakeJWT:
        def __init__(self, algorithms):
            assert algorithms == ["RS256"]

        def decode(self, token, key, claims_options):
            assert token == "signed-token"
            assert key == "mock-keys"
            assert all(key in claims_options for key in ("exp", "iat", "iss", "sub"))
            return claims

    class FakeCache:
        def get(self):
            return "mock-keys"

    monkeypatch.setattr(auth, "JsonWebToken", FakeJWT)
    app.config["PORTAL_OIDC_ISSUER"] = "https://issuer.example"
    app.config["PORTAL_API_AUDIENCE"] = "portal-api"
    app.extensions["portal_jwks_cache"] = FakeCache()
    with app.app_context():
        assert REAL_VALIDATE_ACCESS_TOKEN("signed-token") == claims


def test_access_token_validation_rejects_wrong_audience(app, monkeypatch):
    class Claims(dict):
        def validate(self, leeway):
            return None

    class FakeJWT:
        def __init__(self, algorithms):
            pass

        def decode(self, *args, **kwargs):
            return Claims({"sub": "alice", "aud": "wrong", "exp": 100, "iat": 10})

    class FakeCache:
        def get(self):
            return {}

    monkeypatch.setattr(auth, "JsonWebToken", FakeJWT)
    app.config["PORTAL_OIDC_ISSUER"] = "https://issuer.example"
    app.config["PORTAL_API_AUDIENCE"] = "portal-api"
    app.extensions["portal_jwks_cache"] = FakeCache()
    with app.app_context(), pytest.raises(ValueError, match="audience"):
        REAL_VALIDATE_ACCESS_TOKEN("signed-token")


def test_real_access_token_validation_verifies_signature_and_standard_claims(app):
    signing_key = JsonWebKey.generate_key(
        "RSA", 2048, is_private=True, options={"kid": "portal-test-key"}
    )
    public_key = signing_key.as_dict(is_private=False)
    jwks = JsonWebKey.import_key_set({"keys": [public_key]})
    now = int(auth.time.time())
    claims = {
        "iss": "https://issuer.example/realm",
        "sub": "alice",
        "aud": ["portal-api", "another-api"],
        "iat": now,
        "exp": now + 300,
    }
    signed = JsonWebToken(["RS256"]).encode(
        {"alg": "RS256", "kid": "portal-test-key"}, claims, signing_key
    )
    if isinstance(signed, bytes):
        signed = signed.decode("ascii")

    class Cache:
        def get(self):
            return jwks

    app.config["PORTAL_OIDC_ISSUER"] = claims["iss"]
    app.config["PORTAL_API_AUDIENCE"] = "portal-api"
    app.config["PORTAL_JWT_ALGORITHMS"] = "RS256"
    app.extensions["portal_jwks_cache"] = Cache()

    with app.app_context():
        assert REAL_VALIDATE_ACCESS_TOKEN(signed)["sub"] == "alice"
        header, payload, signature = signed.split(".")
        replacement = "A" if signature[0] != "A" else "B"
        tampered = ".".join((header, payload, replacement + signature[1:]))
        with pytest.raises(auth.JoseError):
            REAL_VALIDATE_ACCESS_TOKEN(tampered)
