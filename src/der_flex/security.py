"""Identity verification and authorization boundaries for the HTTP API."""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlparse

import jwt
from jwt import PyJWKClient


class AuthenticationError(Exception):
    """The request did not present a recognised identity."""

    def __init__(self, code: str = "invalid_token") -> None:
        self.code = code
        super().__init__(code)


class AuthorizationError(Exception):
    """The authenticated identity is outside its allowed scope."""

    def __init__(self, code: str = "access_denied") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AccessPrincipal:
    subject: str
    tenant_id: str
    scopes: frozenset[str]
    zone_ids: frozenset[str]

    def require_scope(self, scope: str) -> None:
        if "*" not in self.scopes and scope not in self.scopes:
            raise AuthorizationError("insufficient_scope")

    def require_zone(self, zone_id: str) -> None:
        if "*" not in self.zone_ids and zone_id not in self.zone_ids:
            # Do not disclose whether a zone exists for another tenant.
            raise AuthorizationError("tenant_boundary")

    def require_tenant(self, tenant_id: str) -> None:
        if self.tenant_id != tenant_id:
            raise AuthorizationError("tenant_boundary")


class Authenticator(Protocol):
    def authenticate(self, bearer_token: str | None) -> AccessPrincipal: ...


class SigningKeyClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class OIDCJWTAuthenticator:
    """Validate asymmetric OIDC access tokens against a rotating JWKS."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: tuple[str, ...] = ("RS256",),
        leeway_seconds: int = 30,
        jwks_client: SigningKeyClient | None = None,
    ) -> None:
        if not issuer or not audience or not jwks_url:
            raise ValueError("issuer, audience and jwks_url are required")
        if urlparse(issuer).scheme != "https" or urlparse(jwks_url).scheme != "https":
            raise ValueError("OIDC issuer and JWKS URL must use HTTPS")
        supported_algorithms = {"RS256", "RS384", "RS512"}
        if not algorithms or any(algorithm not in supported_algorithms for algorithm in algorithms):
            raise ValueError("only explicitly configured RSA signature algorithms are allowed")
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._leeway_seconds = leeway_seconds
        self._jwks_client = jwks_client or PyJWKClient(jwks_url, cache_keys=True)

    def authenticate(self, bearer_token: str | None) -> AccessPrincipal:
        if bearer_token is None:
            raise AuthenticationError("missing_token")
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(bearer_token)
            claims = jwt.decode(
                bearer_token,
                signing_key.key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "tenant_id"]},
            )
            return self._principal_from_claims(cast(dict[str, Any], claims))
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            # Deliberately collapse all verification failures at the trust boundary.
            raise AuthenticationError("invalid_token") from None
        except Exception as error:
            # PyJWKClientError is not a PyJWTError in every supported PyJWT release.
            if error.__class__.__module__.startswith("jwt"):
                raise AuthenticationError("invalid_token") from None
            raise

    @staticmethod
    def _principal_from_claims(claims: dict[str, Any]) -> AccessPrincipal:
        subject = claims.get("sub")
        tenant_id = claims.get("tenant_id")
        scope_claim = claims.get("scope", "")
        zones_claim = claims.get("zones", [])
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("subject must be a non-empty string")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        if isinstance(scope_claim, str):
            scopes = frozenset(scope_claim.split())
        elif isinstance(scope_claim, list) and all(isinstance(item, str) for item in scope_claim):
            scopes = frozenset(scope_claim)
        else:
            raise ValueError("scope must be a string or string list")
        if not isinstance(zones_claim, list) or not all(
            isinstance(item, str) and item for item in zones_claim
        ):
            raise ValueError("zones must be a non-empty string list")
        if "*" in scopes or "*" in zones_claim:
            raise ValueError("OIDC identities require explicit scopes and zones")
        return AccessPrincipal(subject, tenant_id, scopes, frozenset(zones_claim))


def authenticator_from_environment(*, production: bool) -> Authenticator:
    """Build the runtime authenticator, refusing an implicit production bypass."""

    mode = os.getenv("DER_FLEX_AUTH_MODE")
    if mode is None and not production:
        mode = "development"
    if mode == "development":
        return DevelopmentAuthenticator()
    if mode == "oidc":
        required = {
            "issuer": os.getenv("DER_FLEX_OIDC_ISSUER"),
            "audience": os.getenv("DER_FLEX_OIDC_AUDIENCE"),
            "jwks_url": os.getenv("DER_FLEX_OIDC_JWKS_URL"),
        }
        if any(value is None for value in required.values()):
            raise RuntimeError("OIDC mode requires issuer, audience and JWKS URL")
        values = cast(dict[str, str], required)
        return OIDCJWTAuthenticator(
            issuer=values["issuer"],
            audience=values["audience"],
            jwks_url=values["jwks_url"],
        )
    raise RuntimeError("DER_FLEX_AUTH_MODE must be explicitly set to 'oidc' or 'development'")


class DevelopmentAuthenticator:
    """Explicit compatibility mode for the synthetic, single-tenant demo."""

    principal = AccessPrincipal(
        subject="development",
        tenant_id="development",
        scopes=frozenset({"*"}),
        zone_ids=frozenset({"*"}),
    )

    def authenticate(self, bearer_token: str | None) -> AccessPrincipal:
        del bearer_token
        return self.principal


class StaticBearerAuthenticator:
    """Constant-time bearer lookup with non-overlapping tenant zone ownership."""

    def __init__(self, principals_by_token: Mapping[str, AccessPrincipal]) -> None:
        if not principals_by_token:
            raise ValueError("at least one bearer principal is required")
        if any(not token for token in principals_by_token):
            raise ValueError("bearer tokens cannot be empty")
        self._entries = tuple(principals_by_token.items())
        owners: dict[str, str] = {}
        for principal in principals_by_token.values():
            if "*" in principal.zone_ids:
                raise ValueError("static tenant principals cannot own every zone")
            for zone_id in principal.zone_ids:
                previous = owners.setdefault(zone_id, principal.tenant_id)
                if previous != principal.tenant_id:
                    raise ValueError(f"zone {zone_id!r} is assigned to multiple tenants")

    def authenticate(self, bearer_token: str | None) -> AccessPrincipal:
        if bearer_token is None:
            raise AuthenticationError("missing_token")
        matched: AccessPrincipal | None = None
        for expected, principal in self._entries:
            if hmac.compare_digest(bearer_token, expected):
                matched = principal
        if matched is None:
            raise AuthenticationError("invalid_token")
        return matched
