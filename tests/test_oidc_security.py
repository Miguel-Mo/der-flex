from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from der_flex.security import (
    AuthenticationError,
    DevelopmentAuthenticator,
    OIDCJWTAuthenticator,
    authenticator_from_environment,
)

ISSUER = "https://identity.example.test/"
AUDIENCE = "der-flex-api"
JWKS_URL = "https://identity.example.test/.well-known/jwks.json"


class RotatingKeyClient:
    def __init__(self, keys: dict[str, Any]) -> None:
        self.keys = keys

    def get_signing_key_from_jwt(self, token: str) -> Any:
        kid = jwt.get_unverified_header(token)["kid"]
        if kid not in self.keys:
            raise jwt.PyJWKClientError("unknown key")
        return SimpleNamespace(key=self.keys[kid].public_key())


def token(
    key: Any,
    *,
    kid: str = "key-1",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    claims: dict[str, Any] | None = None,
    omit_claims: frozenset[str] = frozenset(),
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": "service-a",
        "tenant_id": "tenant-a",
        "scope": "flexibility:read reservation:write",
        "zones": ["north"],
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
    }
    payload.update(claims or {})
    for claim in omit_claims:
        payload.pop(claim, None)
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def keys() -> dict[str, Any]:
    return {
        "key-1": rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "key-2": rsa.generate_private_key(public_exponent=65537, key_size=2048),
    }


def authenticator(keys: dict[str, Any]) -> OIDCJWTAuthenticator:
    return OIDCJWTAuthenticator(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=JWKS_URL,
        leeway_seconds=0,
        jwks_client=RotatingKeyClient(keys),
    )


def test_oidc_accepts_scoped_tenant_identity_and_rotated_key(keys: dict[str, Any]) -> None:
    identity = authenticator(keys)
    first = identity.authenticate(token(keys["key-1"]))
    rotated = identity.authenticate(token(keys["key-2"], kid="key-2"))
    assert first == rotated
    assert first.tenant_id == "tenant-a"
    assert first.scopes == frozenset({"flexibility:read", "reservation:write"})
    assert first.zone_ids == frozenset({"north"})


@pytest.mark.parametrize(
    "claims,issuer,audience",
    [
        ({"exp": datetime.now(UTC) - timedelta(seconds=1)}, ISSUER, AUDIENCE),
        ({"nbf": datetime.now(UTC) + timedelta(minutes=1)}, ISSUER, AUDIENCE),
        ({}, "https://attacker.example.test/", AUDIENCE),
        ({}, ISSUER, "another-api"),
        ({"tenant_id": ""}, ISSUER, AUDIENCE),
        ({"tenant_id": None}, ISSUER, AUDIENCE),
        ({"scope": "*", "zones": ["north"]}, ISSUER, AUDIENCE),
        ({"scope": "flexibility:read", "zones": ["*"]}, ISSUER, AUDIENCE),
    ],
)
def test_oidc_rejects_invalid_time_trust_and_tenant_claims(
    keys: dict[str, Any], claims: dict[str, Any], issuer: str, audience: str
) -> None:
    with pytest.raises(AuthenticationError, match="invalid_token"):
        authenticator(keys).authenticate(
            token(keys["key-1"], issuer=issuer, audience=audience, claims=claims)
        )


def test_oidc_rejects_unknown_key_missing_token_and_insecure_configuration(
    keys: dict[str, Any],
) -> None:
    identity = authenticator(keys)
    with pytest.raises(AuthenticationError, match="missing_token"):
        identity.authenticate(None)
    with pytest.raises(AuthenticationError, match="invalid_token"):
        identity.authenticate(token(keys["key-1"], kid="retired-key"))
    with pytest.raises(ValueError, match="HTTPS"):
        OIDCJWTAuthenticator(
            issuer="http://issuer.test",
            audience=AUDIENCE,
            jwks_url=JWKS_URL,
        )


def test_oidc_requires_not_before_claim(keys: dict[str, Any]) -> None:
    with pytest.raises(AuthenticationError, match="invalid_token"):
        authenticator(keys).authenticate(token(keys["key-1"], omit_claims=frozenset({"nbf"})))


def test_runtime_authentication_is_explicit_for_production(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        "DER_FLEX_AUTH_MODE",
        "DER_FLEX_OIDC_ISSUER",
        "DER_FLEX_OIDC_AUDIENCE",
        "DER_FLEX_OIDC_JWKS_URL",
    ):
        monkeypatch.delenv(variable, raising=False)
    assert isinstance(authenticator_from_environment(production=False), DevelopmentAuthenticator)
    with pytest.raises(RuntimeError, match="explicitly set"):
        authenticator_from_environment(production=True)
    monkeypatch.setenv("DER_FLEX_AUTH_MODE", "oidc")
    with pytest.raises(RuntimeError, match="requires"):
        authenticator_from_environment(production=True)
    monkeypatch.setenv("DER_FLEX_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("DER_FLEX_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("DER_FLEX_OIDC_JWKS_URL", JWKS_URL)
    assert isinstance(authenticator_from_environment(production=True), OIDCJWTAuthenticator)
