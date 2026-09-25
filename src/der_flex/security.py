"""Small, injectable authorization boundary for the HTTP API.

This module deliberately keeps identity verification behind a protocol.  The static
bearer implementation is suitable for deterministic tests and controlled local
deployments; an external OIDC verifier can implement the same protocol later.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class AuthenticationError(Exception):
    """The request did not present a recognised identity."""


class AuthorizationError(Exception):
    """The authenticated identity is outside its allowed scope."""


@dataclass(frozen=True)
class AccessPrincipal:
    subject: str
    tenant_id: str
    scopes: frozenset[str]
    zone_ids: frozenset[str]

    def require_scope(self, scope: str) -> None:
        if "*" not in self.scopes and scope not in self.scopes:
            raise AuthorizationError("required scope is not granted")

    def require_zone(self, zone_id: str) -> None:
        if "*" not in self.zone_ids and zone_id not in self.zone_ids:
            # Do not disclose whether a zone exists for another tenant.
            raise AuthorizationError("zone is outside the tenant boundary")

    def require_tenant(self, tenant_id: str) -> None:
        if self.tenant_id != tenant_id:
            raise AuthorizationError("object is outside the tenant boundary")


class Authenticator(Protocol):
    def authenticate(self, bearer_token: str | None) -> AccessPrincipal: ...


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
            raise AuthenticationError("bearer token is required")
        matched: AccessPrincipal | None = None
        for expected, principal in self._entries:
            if hmac.compare_digest(bearer_token, expected):
                matched = principal
        if matched is None:
            raise AuthenticationError("bearer token is invalid")
        return matched
