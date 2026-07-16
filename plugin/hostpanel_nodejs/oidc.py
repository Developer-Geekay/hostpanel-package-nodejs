from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from typing import Any, Optional

import jwt
from fastapi import HTTPException

# GitHub Actions OIDC verification (DEPLOY_PLAN.md Phase 4).
#
# The workflow requests a short-lived JWT from GitHub and sends it as the
# deploy bearer token. We verify the signature against GitHub's published
# JWKS and then authorize the token's repository/ref claims against the
# app row. No credential is stored anywhere on either side.

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = "https://token.actions.githubusercontent.com/.well-known/jwks"

# Protocol constant, not deployment config: the workflow requests its token
# with this audience and verification requires it, so a token minted for any
# other service (or the default audience) can never be replayed here.
AUDIENCE = "hostpanel-nodejs-deploy"

JWKS_TTL_S = 3600
FORCED_REFRESH_MIN_INTERVAL_S = 60
CLOCK_SKEW_S = 60
FETCH_TIMEOUT_S = 10

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_jwks_keys: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0
_last_forced_refresh: float = 0.0


def _fetch_jwks() -> dict[str, Any]:
    with urllib.request.urlopen(JWKS_URL, timeout=FETCH_TIMEOUT_S) as resp:
        return json.loads(resp.read())


def _refresh_keys(force: bool = False) -> None:
    global _jwks_keys, _jwks_fetched_at, _last_forced_refresh
    with _cache_lock:
        now = time.time()
        if _jwks_keys and not force and now - _jwks_fetched_at < JWKS_TTL_S:
            return
        if force:
            # An unknown kid triggers a forced refetch (GitHub key rotation),
            # but a stream of garbage tokens must not turn us into a JWKS
            # hammering client.
            if now - _last_forced_refresh < FORCED_REFRESH_MIN_INTERVAL_S:
                return
            _last_forced_refresh = now
        try:
            data = _fetch_jwks()
            keys: dict[str, Any] = {}
            for jwk_dict in data.get("keys", []):
                kid = jwk_dict.get("kid")
                if kid and jwk_dict.get("kty") == "RSA":
                    keys[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk_dict))
            if keys:
                _jwks_keys = keys
                _jwks_fetched_at = now
        except Exception as exc:
            # A transient GitHub outage must not hard-fail deploys: keep
            # serving previously fetched keys — signatures still verify.
            if _jwks_keys:
                logger.warning("GitHub JWKS refresh failed; serving cached keys: %s", exc)
            else:
                raise HTTPException(status_code=503, detail=f"Cannot fetch GitHub signing keys: {exc}")


def _key_for(token: str) -> Any:
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Malformed token: {exc}")
    _refresh_keys()
    key = _jwks_keys.get(kid)
    if key is None:
        _refresh_keys(force=True)
        key = _jwks_keys.get(kid)
    if key is None:
        raise HTTPException(status_code=401, detail="Unknown token signing key")
    return key


def verify(authorization: Optional[str]) -> dict[str, Any]:
    """Validate the bearer JWT's signature, issuer, audience, and lifetime.
    Returns the verified claims; authorization against an app is separate."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization[len("Bearer "):].strip()
    key = _key_for(token)
    try:
        return jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            leeway=CLOCK_SKEW_S,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


def authorize(app: dict[str, Any], claims: dict[str, Any]) -> None:
    """The token proves which repo and ref it came from; the app row says
    which repo and ref may deploy it. Anything else is a 403 — this is what
    binds an app id to exactly one source."""
    expected_repo = app.get("repo")
    expected_ref = app.get("ref")
    if not expected_repo or not expected_ref:
        raise HTTPException(
            status_code=403,
            detail="Deploy source is not configured for this application — set repo and ref via deploy-mode",
        )
    claimed_repo = claims.get("repository")
    claimed_ref = claims.get("ref")
    if claimed_repo != expected_repo or claimed_ref != expected_ref:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Token from {claimed_repo!r}@{claimed_ref!r} is not authorized for this application "
                f"(expects {expected_repo!r}@{expected_ref!r})"
            ),
        )
