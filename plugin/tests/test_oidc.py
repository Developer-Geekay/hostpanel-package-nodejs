from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from hostpanel_nodejs import oidc

KID = "test-key-1"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_private_pem = _private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)

_other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_other_pem = _other_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)


def _jwks() -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_private_key.public_key()))
    jwk["kid"] = KID
    return {"keys": [jwk]}


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": oidc.ISSUER,
        "aud": oidc.AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "repository": "Developer-Geekay/portfolio",
        "ref": "refs/heads/main",
    }
    claims.update(overrides)
    return claims


def _token(claims: dict, key=None, kid: str = KID) -> str:
    return "Bearer " + jwt.encode(claims, key or _private_pem, algorithm="RS256", headers={"kid": kid})


@pytest.fixture(autouse=True)
def jwks_cache(monkeypatch):
    """Fresh key cache per test, backed by the test JWKS instead of GitHub."""
    monkeypatch.setattr(oidc, "_jwks_keys", {})
    monkeypatch.setattr(oidc, "_jwks_fetched_at", 0.0)
    monkeypatch.setattr(oidc, "_last_forced_refresh", 0.0)
    monkeypatch.setattr(oidc, "_fetch_jwks", _jwks)


APP = {"id": "portfolio-example-com", "repo": "Developer-Geekay/portfolio", "ref": "refs/heads/main"}


def test_valid_token_verifies_and_authorizes():
    claims = oidc.verify(_token(_claims()))
    oidc.authorize(APP, claims)
    assert claims["repository"] == "Developer-Geekay/portfolio"


def _expect(status: int, authorization):
    with pytest.raises(HTTPException) as exc:
        oidc.verify(authorization)
    assert exc.value.status_code == status
    return exc.value


def test_missing_and_malformed_tokens():
    _expect(401, None)
    _expect(401, "Bearer not-a-jwt")


def test_expired_token():
    now = int(time.time())
    _expect(401, _token(_claims(iat=now - 900, exp=now - 600)))


def test_wrong_audience_and_issuer():
    _expect(401, _token(_claims(aud="sts.amazonaws.com")))
    _expect(401, _token(_claims(iss="https://evil.example.com")))


def test_forged_signature_rejected():
    # Signed by a key GitHub (our JWKS) never published, same kid.
    _expect(401, _token(_claims(), key=_other_pem))


def test_hs256_confusion_rejected():
    token = "Bearer " + jwt.encode(_claims(), "x" * 32, algorithm="HS256", headers={"kid": KID})
    _expect(401, token)


def test_unknown_kid_rejected_after_forced_refresh():
    _expect(401, _token(_claims(), kid="rotated-away"))


def test_stale_jwks_served_when_refresh_fails(monkeypatch):
    oidc.verify(_token(_claims()))  # primes the cache
    monkeypatch.setattr(oidc, "_fetch_jwks", lambda: (_ for _ in ()).throw(OSError("github down")))
    monkeypatch.setattr(oidc, "_jwks_fetched_at", 0.0)  # force TTL expiry
    claims = oidc.verify(_token(_claims()))  # still verifies on stale keys
    assert claims["repository"] == "Developer-Geekay/portfolio"


def test_no_cache_and_fetch_failure_is_503(monkeypatch):
    monkeypatch.setattr(oidc, "_fetch_jwks", lambda: (_ for _ in ()).throw(OSError("github down")))
    _expect(503, _token(_claims()))


def test_authorize_repo_mismatch():
    claims = oidc.verify(_token(_claims(repository="attacker/portfolio")))
    with pytest.raises(HTTPException) as exc:
        oidc.authorize(APP, claims)
    assert exc.value.status_code == 403
    assert "attacker/portfolio" in exc.value.detail
    assert "Developer-Geekay/portfolio" in exc.value.detail


def test_authorize_ref_mismatch():
    claims = oidc.verify(_token(_claims(ref="refs/heads/feature-x")))
    with pytest.raises(HTTPException) as exc:
        oidc.authorize(APP, claims)
    assert exc.value.status_code == 403


def test_authorize_requires_configured_source():
    claims = oidc.verify(_token(_claims()))
    with pytest.raises(HTTPException) as exc:
        oidc.authorize({"id": "x", "repo": None, "ref": None}, claims)
    assert exc.value.status_code == 403
    assert "not configured" in exc.value.detail
