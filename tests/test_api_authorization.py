from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from der_flex.api.app import create_app
from der_flex.domain.models import FlexibilityOffer
from der_flex.domain.store import InMemoryOfferStore
from der_flex.security import AccessPrincipal, StaticBearerAuthenticator
from der_flex.security_audit import SecurityAuditEvent


class MemorySecurityAudit:
    def __init__(self) -> None:
        self.events: list[SecurityAuditEvent] = []

    def record(self, event: SecurityAuditEvent) -> None:
        self.events.append(event)


def principal(tenant: str, scopes: set[str], zones: set[str]) -> AccessPrincipal:
    return AccessPrincipal(
        subject=f"service-{tenant}",
        tenant_id=tenant,
        scopes=frozenset(scopes),
        zone_ids=frozenset(zones),
    )


def secured_client() -> TestClient:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    store = InMemoryOfferStore(minimum_participants=1)
    for zone_id, resource_id in (("north", "north-1"), ("south", "south-1")):
        store.upsert(
            FlexibilityOffer(
                tenant_id=f"{zone_id}-tenant",
                resource_id=resource_id,
                zone_id=zone_id,
                interval_start=now,
                interval_end=now + timedelta(minutes=15),
                baseline_power_kw=0,
                upward_capacity_kw=10,
                downward_capacity_kw=10,
                upward_energy_kwh=2.5,
                downward_energy_kwh=2.5,
                confidence=1,
                consequence_type="DEFER",
                source_version="1",
                source_epoch=1,
                source_sequence=1,
                observed_at=now,
                expires_at=now + timedelta(minutes=15),
            )
        )
    auth = StaticBearerAuthenticator(
        {
            "north-reader": principal("north-tenant", {"flexibility:read"}, {"north"}),
            "north-operator": principal(
                "north-tenant",
                {
                    "flexibility:read",
                    "reservation:read",
                    "reservation:write",
                    "activation:read",
                    "activation:write",
                },
                {"north"},
            ),
            "south-operator": principal(
                "south-tenant",
                {"reservation:read", "reservation:write"},
                {"south"},
            ),
        }
    )
    return TestClient(create_app(store, authenticator=auth))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def reservation_payload(zone_id: str = "north") -> dict[str, object]:
    start = datetime(2030, 1, 1, tzinfo=UTC)
    return {
        "zone_id": zone_id,
        "interval_start": start.isoformat(),
        "interval_end": (start + timedelta(minutes=15)).isoformat(),
        "direction": "UPWARD",
        "power_kw": 1,
    }


def test_missing_invalid_and_wrong_scheme_tokens_are_rejected() -> None:
    with secured_client() as client:
        for headers in ({}, bearer("invalid"), {"Authorization": "Basic abc"}):
            response = client.get("/api/v1/flexibility/zones", headers=headers)
            assert response.status_code == 401
            assert response.json() == {"detail": "authentication required"}


def test_scope_and_zone_boundaries_do_not_disclose_other_tenants() -> None:
    with secured_client() as client:
        zones = client.get("/api/v1/flexibility/zones", headers=bearer("north-reader"))
        assert zones.status_code == 200
        assert zones.json() == {"data": ["north"]}

        forbidden_scope = client.post(
            "/api/v1/reservations",
            headers={**bearer("north-reader"), "Idempotency-Key": "reader-write"},
            json=reservation_payload(),
        )
        assert forbidden_scope.status_code == 404

        forbidden_zone = client.get(
            "/api/v1/flexibility",
            headers=bearer("north-reader"),
            params={
                "zone_id": "south",
                "from": "2030-01-01T00:00:00Z",
                "to": "2030-01-01T00:15:00Z",
            },
        )
        assert forbidden_zone.status_code == 404


def test_cross_tenant_reservation_uuid_is_hidden() -> None:
    with secured_client() as client:
        created = client.post(
            "/api/v1/reservations",
            headers={**bearer("north-operator"), "Idempotency-Key": "shared"},
            json=reservation_payload(),
        )
        assert created.status_code == 201
        reservation_id = created.json()["reservation_id"]

        own = client.get(
            f"/api/v1/reservations/{reservation_id}",
            headers=bearer("north-operator"),
        )
        assert own.status_code == 200

        cross_tenant = client.get(
            f"/api/v1/reservations/{reservation_id}",
            headers=bearer("south-operator"),
        )
        assert cross_tenant.status_code == 404
        assert cross_tenant.json() == {"detail": "not found"}


def test_static_configuration_rejects_overlapping_tenant_zones() -> None:
    with pytest.raises(ValueError, match="multiple tenants"):
        StaticBearerAuthenticator(
            {
                "one": principal("tenant-a", {"*"}, {"north"}),
                "two": principal("tenant-b", {"*"}, {"north"}),
            }
        )


@pytest.mark.parametrize(
    "method,path,params,json_body,extra_headers",
    [
        ("GET", "/api/v1/flexibility/zones", None, None, {}),
        (
            "GET",
            "/api/v1/flexibility",
            {
                "zone_id": "north",
                "from": "2030-01-01T00:00:00Z",
                "to": "2030-01-01T00:15:00Z",
            },
            None,
            {},
        ),
        (
            "POST",
            "/api/v1/reservations",
            None,
            reservation_payload(),
            {"Idempotency-Key": "matrix"},
        ),
        ("GET", "/api/v1/reservations/00000000-0000-0000-0000-000000000001", None, None, {}),
        ("DELETE", "/api/v1/reservations/00000000-0000-0000-0000-000000000001", None, None, {}),
        (
            "POST",
            "/api/v1/reservations/00000000-0000-0000-0000-000000000001/activate",
            None,
            None,
            {},
        ),
        ("GET", "/api/v1/activations/00000000-0000-0000-0000-000000000001", None, None, {}),
        ("GET", "/api/v1/admin/security/status", None, None, {}),
    ],
)
def test_every_business_endpoint_rejects_vertical_scope_escalation(
    method: str,
    path: str,
    params: dict[str, str] | None,
    json_body: dict[str, Any] | None,
    extra_headers: dict[str, str],
) -> None:
    audit = MemorySecurityAudit()
    auth = StaticBearerAuthenticator({"empty-role": principal("north-tenant", set(), {"north"})})
    headers = {"Authorization": "Bearer empty-role", **extra_headers}
    with TestClient(create_app(authenticator=auth, security_audit=audit)) as client:
        response = client.request(method, path, headers=headers, params=params, json=json_body)
    assert response.status_code == 404
    assert audit.events[-1].reason == "insufficient_scope"
    assert audit.events[-1].route == path or "{" in audit.events[-1].route


def test_security_audit_uses_route_template_and_never_records_credentials_or_uuid() -> None:
    audit = MemorySecurityAudit()
    secret = "top-secret-credential"
    target_uuid = "00000000-0000-0000-0000-000000000042"
    auth = StaticBearerAuthenticator({secret: principal("north-tenant", set(), {"north"})})
    with TestClient(create_app(authenticator=auth, security_audit=audit)) as client:
        response = client.get(f"/api/v1/reservations/{target_uuid}", headers=bearer(secret))
    assert response.status_code == 404
    serialized = repr(audit.events)
    assert secret not in serialized
    assert target_uuid not in serialized
    assert "north" not in serialized
    assert audit.events == [
        SecurityAuditEvent(
            outcome="denied",
            reason="insufficient_scope",
            method="GET",
            route="/api/v1/reservations/{reservation_id}",
        )
    ]


def test_openapi_declares_bearer_authentication_for_business_routes() -> None:
    auth = StaticBearerAuthenticator(
        {"token": principal("north-tenant", {"flexibility:read"}, {"north"})}
    )
    schema = create_app(authenticator=auth).openapi()
    schemes = schema["components"]["securitySchemes"]
    assert schemes == {"HTTPBearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}
    assert schema["paths"]["/api/v1/flexibility/zones"]["get"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in schema["paths"]["/health/live"]["get"]
