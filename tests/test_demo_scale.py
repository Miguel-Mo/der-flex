from __future__ import annotations

from fastapi.testclient import TestClient

from der_flex.demo import app
from der_flex.simulators import build_demo_fleet


def test_demo_runs_108_resources_for_one_simulated_hour_without_identifier_leak() -> None:
    fleet = build_demo_fleet(per_profile_per_zone=12)
    assert len(fleet) == 108
    assert len({resource.resource_id for resource in fleet}) == 108

    start, end = app.state.demo_interval
    with TestClient(app) as client:
        zones_response = client.get("/api/v1/flexibility/zones")
        assert zones_response.status_code == 200
        zones = zones_response.json()["data"]
        assert len(zones) == 3

        for zone_id in zones:
            response = client.get(
                "/api/v1/flexibility",
                params={"zone_id": zone_id, "from": start.isoformat(), "to": end.isoformat()},
            )
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["data"]) == 4
            # Public counts are bucketed down to avoid disclosing the exact cohort size.
            assert all(item["participant_count"] == 35 for item in payload["data"])
            assert "resource_id" not in response.text


def test_demo_limits_request_bodies_and_exposes_anonymous_metrics() -> None:
    with TestClient(app) as client:
        openapi = client.get("/openapi.json")
        assert openapi.json()["info"]["version"] == "0.2.0"

        health = client.get("/health/ready")
        assert health.headers["x-request-id"]

        oversized = client.post(
            "/api/v1/reservations",
            content=b"x" * 32_769,
            headers={"Content-Type": "application/json", "Idempotency-Key": "too-large"},
        )
        assert oversized.status_code == 413
        assert oversized.headers["content-type"].startswith("application/problem+json")

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert "der_flex_http_requests_total" in metrics.text
        assert "resource_id" not in metrics.text


def test_reservation_payload_rejects_unknown_fields() -> None:
    start, end = app.state.demo_interval
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/reservations",
            headers={"Idempotency-Key": "strict-json"},
            json={
                "zone_id": "ES-MA-29700",
                "interval_start": start.isoformat(),
                "interval_end": (start + (end - start) / 4).isoformat(),
                "direction": "UPWARD",
                "power_kw": 1,
                "unexpected": "must fail",
            },
        )

        assert response.status_code == 422


def test_reservation_rejects_naive_times_and_private_callback() -> None:
    start, _ = app.state.demo_interval
    base_payload = {
        "zone_id": "ES-MA-29700",
        "interval_start": start.isoformat(),
        "interval_end": (start + (app.state.demo_interval[1] - start) / 4).isoformat(),
        "direction": "UPWARD",
        "power_kw": 1,
    }
    with TestClient(app) as client:
        naive = client.post(
            "/api/v1/reservations",
            headers={"Idempotency-Key": "naive-time"},
            json={
                **base_payload,
                "interval_start": start.replace(tzinfo=None).isoformat(),
            },
        )
        private_callback = client.post(
            "/api/v1/reservations",
            headers={"Idempotency-Key": "private-callback"},
            json={**base_payload, "callback_url": "https://127.0.0.1/hook"},
        )

        assert naive.status_code == 422
        assert private_callback.status_code == 422


def test_reservation_api_rejects_non_finite_and_excessive_power() -> None:
    start, end = app.state.demo_interval
    prefix = (
        '{"zone_id":"ES-MA-29700",'
        f'"interval_start":"{start.isoformat()}",'
        f'"interval_end":"{(start + (end - start) / 4).isoformat()}",'
        '"direction":"UPWARD","power_kw":'
    )
    with TestClient(app) as client:
        for index, value in enumerate(("NaN", "Infinity", "1000000.01")):
            response = client.post(
                "/api/v1/reservations",
                headers={
                    "Content-Type": "application/json",
                    "Idempotency-Key": f"invalid-numeric-{index}",
                },
                content=f"{prefix}{value}}}",
            )
            assert response.status_code == 422
