import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
import pytest
from s2python.pebc import PEBCInstruction
from s2python.s2_parser import S2Parser

from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.adapters.webhooks import WebhookDispatcher
from der_flex.api import create_app
from der_flex.domain import InMemoryOfferStore, Reservation
from der_flex.reservations import (
    IdempotencyConflict,
    InsufficientCapacity,
    ReservationService,
)
from der_flex.simulators import (
    ZONES,
    BatterySimulator,
    build_demo_fleet,
    build_simulator_registry,
)

START = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)
END = START + timedelta(minutes=15)


def build_service() -> tuple[InMemoryOfferStore, ReservationService]:
    store = InMemoryOfferStore(minimum_participants=10)
    for resource in build_demo_fleet():
        constraints, forecast = resource.s2_offer_messages(START)
        for offer in normalize_pebc_offer(
            resource_id=resource.resource_id,
            zone_id=resource.zone_id,
            constraints_message=constraints,
            forecast_message=forecast,
            resource_registry=build_simulator_registry([resource]),
            source_epoch=resource.source_epoch,
            source_sequence=resource.source_sequence,
            observed_at=START,
            received_at=START,
        ):
            store.upsert(offer)
    return store, ReservationService(store)


def reserve(service: ReservationService, key: str, power_kw: float = 10.0) -> Reservation:
    return service.create(
        idempotency_key=key,
        zone_id=ZONES[0],
        interval_start=START,
        interval_end=END,
        direction="UPWARD",
        power_kw=power_kw,
    )


def test_idempotency_replays_same_reservation_and_rejects_key_reuse() -> None:
    _, service = build_service()

    first = reserve(service, "same-key")
    replay = reserve(service, "same-key")

    assert replay.reservation_id == first.reservation_id
    with pytest.raises(IdempotencyConflict):
        reserve(service, "same-key", power_kw=11.0)


def test_reservation_reduces_public_residual_capacity_and_cancel_restores_it() -> None:
    store, service = build_service()
    original = store.query(ZONES[0], START, END)[0]
    reservation = reserve(service, "residual", power_kw=10.0)

    reserved = service.apply_residual_capacity(store.query(ZONES[0], START, END))[0]
    assert reserved.upward_capacity_kw == original.upward_capacity_kw - 10.0
    assert reserved.downward_capacity_kw == original.downward_capacity_kw

    service.cancel(reservation.reservation_id)
    restored = service.apply_residual_capacity(store.query(ZONES[0], START, END))[0]
    assert restored.upward_capacity_kw == original.upward_capacity_kw


def test_public_residual_cell_is_suppressed_after_reservation_change() -> None:
    store, service = build_service()
    initial = service.public_residual_capacity(store.query(ZONES[0], START, END))
    assert len(initial) == 1

    reservation = reserve(service, "public-residual-privacy", power_kw=3.0)

    assert service.public_residual_capacity(store.query(ZONES[0], START, END)) == []
    service.cancel(reservation.reservation_id)
    assert service.public_residual_capacity(store.query(ZONES[0], START, END)) == []


def test_confirmed_reservation_expires_and_releases_capacity() -> None:
    store, service = build_service()
    created_at = START - timedelta(minutes=10)
    original = store.query(ZONES[0], START, END, now=created_at)[0]
    reservation = service.create(
        idempotency_key="expiry",
        zone_id=ZONES[0],
        interval_start=START,
        interval_end=END,
        direction="UPWARD",
        power_kw=10.0,
        now=created_at,
    )

    expired = service.get(reservation.reservation_id, now=created_at + timedelta(minutes=6))
    residual = service.apply_residual_capacity(
        store.query(ZONES[0], START, END, now=created_at + timedelta(minutes=6))
    )[0]

    assert expired.status == "EXPIRED"
    assert residual.upward_capacity_kw == original.upward_capacity_kw


def test_concurrent_reservations_cannot_double_sell_capacity() -> None:
    _, service = build_service()

    def attempt(key: str) -> str:
        try:
            reserve(service, key, power_kw=30.0)
            return "confirmed"
        except InsufficientCapacity:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, ("race-a", "race-b")))

    assert sorted(outcomes) == ["confirmed", "rejected"]


@pytest.mark.parametrize("power_kw", [float("nan"), float("inf"), float("-inf"), 1_000_000.01])
def test_reservation_service_rejects_non_finite_or_absurd_power(power_kw: float) -> None:
    _, service = build_service()

    with pytest.raises(ValueError, match="invalid reservation"):
        reserve(service, "invalid-number", power_kw=power_kw)


def test_multi_interval_reservations_share_the_physical_energy_budget() -> None:
    resource = BatterySimulator(
        power_kw=0.0,
        state_of_charge=0.20,
        max_ramp_kw_per_min=1.0,
    )
    constraints, forecast = resource.s2_offer_messages(START, interval_count=4)
    registry = build_simulator_registry([resource])
    offers = normalize_pebc_offer(
        resource_id=resource.resource_id,
        zone_id=resource.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=registry,
        source_epoch=resource.source_epoch,
        source_sequence=resource.source_sequence,
        observed_at=START,
        received_at=START,
    )
    store = InMemoryOfferStore(minimum_participants=1)
    for offer in offers:
        store.upsert(offer)
    service = ReservationService(store)

    reservations = [
        service.create(
            idempotency_key=f"energy-{index}",
            zone_id=resource.zone_id,
            interval_start=offer.interval_start,
            interval_end=offer.interval_end,
            direction="UPWARD",
            power_kw=offer.upward_capacity_kw,
            now=START - timedelta(minutes=1),
        )
        for index, offer in enumerate(offers)
    ]

    committed_energy = sum(
        reservation.allocated_power_kw
        * (reservation.interval_end - reservation.interval_start).total_seconds()
        / 3600
        for reservation in reservations
    )
    assert committed_energy == pytest.approx(1.0)
    assert committed_energy <= resource.stored_energy_kwh - resource.minimum_energy_kwh


def test_activation_generates_schema_valid_pebc_instructions_and_is_idempotent() -> None:
    _, service = build_service()
    reservation = reserve(service, "activation", power_kw=10.0)

    activation = service.activate(reservation.reservation_id)
    replay = service.activate(reservation.reservation_id)

    assert replay.activation_id == activation.activation_id
    assert activation.status == "COMPLETED"
    assert activation.instruction_count >= 1
    for instruction in service.instructions_for(activation.activation_id):
        parsed = S2Parser().parse_as_any_message(json.dumps(instruction))
        assert isinstance(parsed, PEBCInstruction)
        assert isinstance(instruction["power_constraints_id"], str)


async def api_workflow() -> None:
    store, service = build_service()
    app = create_app(store, service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "zone_id": ZONES[0],
            "interval_start": START.isoformat(),
            "interval_end": END.isoformat(),
            "direction": "DOWNWARD",
            "power_kw": 5.0,
            "consequence_type": "DEFER",
        }
        response = await client.post(
            "/api/v1/reservations",
            json=payload,
            headers={"Idempotency-Key": "api-reservation"},
        )
        assert response.status_code == 201
        reservation = response.json()
        assert reservation["participant_count"] == 12

        replay = await client.post(
            "/api/v1/reservations",
            json=payload,
            headers={"Idempotency-Key": "api-reservation"},
        )
        assert replay.json()["reservation_id"] == reservation["reservation_id"]

        activation = await client.post(
            f"/api/v1/reservations/{reservation['reservation_id']}/activate"
        )
        assert activation.status_code == 200
        activation_id = activation.json()["activation_id"]

        fetched = await client.get(f"/api/v1/activations/{activation_id}")
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "COMPLETED"


def test_complete_reservation_api_workflow() -> None:
    asyncio.run(api_workflow())


async def public_residual_privacy_workflow() -> None:
    store, service = build_service()
    app = create_app(store, service)
    transport = httpx.ASGITransport(app=app)
    query = {
        "zone_id": ZONES[0],
        "from": START.isoformat(),
        "to": END.isoformat(),
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial = await client.get("/api/v1/flexibility", params=query)
        assert len(initial.json()["data"]) == 1

        reservation = await client.post(
            "/api/v1/reservations",
            headers={"Idempotency-Key": "public-delta"},
            json={
                "zone_id": ZONES[0],
                "interval_start": START.isoformat(),
                "interval_end": END.isoformat(),
                "direction": "UPWARD",
                "power_kw": 3.0,
            },
        )
        assert reservation.status_code == 201

        after = await client.get("/api/v1/flexibility", params=query)
        assert after.status_code == 200
        assert after.json()["data"] == []


def test_public_api_suppresses_residual_delta_after_reservation() -> None:
    asyncio.run(public_residual_privacy_workflow())


async def insufficient_capacity_problem() -> None:
    store, service = build_service()
    app = create_app(store, service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/reservations",
            headers={"Idempotency-Key": "too-large"},
            json={
                "zone_id": ZONES[0],
                "interval_start": START.isoformat(),
                "interval_end": END.isoformat(),
                "direction": "UPWARD",
                "power_kw": 1000.0,
            },
        )
        assert response.status_code == 409
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["status"] == 409


def test_insufficient_capacity_uses_problem_json() -> None:
    asyncio.run(insufficient_capacity_problem())


def test_webhooks_are_signed_retried_and_correlated() -> None:
    store, _ = build_service()
    calls: list[tuple[bytes, dict[str, str]]] = []

    def sender(_url: str, body: bytes, headers: dict[str, str]) -> int:
        calls.append((body, headers))
        return 503 if len(calls) < 3 else 204

    dispatcher = WebhookDispatcher(secret="test-secret", sender=sender)
    service = ReservationService(store, webhook_dispatcher=dispatcher)
    reservation = service.create(
        idempotency_key="webhook",
        zone_id=ZONES[0],
        interval_start=START,
        interval_end=END,
        direction="UPWARD",
        power_kw=5.0,
        callback_url="https://aggregator.example/events",
    )
    activation = service.activate(reservation.reservation_id)

    assert [delivery.event for delivery in dispatcher.deliveries] == [
        "activation.accepted",
        "activation.started",
        "activation.completed",
    ]
    assert dispatcher.deliveries[0].attempts == 3
    assert all(delivery.delivered for delivery in dispatcher.deliveries)
    assert calls[0][1]["X-DER-Flex-Signature"].startswith("sha256=")
    assert activation.correlation_id == reservation.correlation_id


def test_partial_rejection_releases_rejected_capacity_conservatively() -> None:
    store, _ = build_service()
    accepted_once = False

    def accept_first(_resource_id: str, _instruction: dict[str, object]) -> bool:
        nonlocal accepted_once
        if accepted_once:
            return False
        accepted_once = True
        return True

    service = ReservationService(store, resource_acceptor=accept_first)
    original = store.query(ZONES[0], START, END)[0]
    reservation = reserve(service, "partial", power_kw=10.0)
    activation = service.activate(reservation.reservation_id)
    updated = service.get(reservation.reservation_id)
    residual = service.apply_residual_capacity(store.query(ZONES[0], START, END))[0]

    assert activation.status == "FAILED"
    assert activation.accepted_instruction_count == 1
    assert activation.rejected_instruction_count >= 1
    assert 0 < updated.allocated_power_kw < updated.requested_power_kw
    assert residual.upward_capacity_kw == pytest.approx(
        original.upward_capacity_kw - updated.allocated_power_kw
    )
