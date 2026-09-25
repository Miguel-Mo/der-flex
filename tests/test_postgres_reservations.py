from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from typing import Any

import psycopg
import pytest

from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.domain import InMemoryOfferStore
from der_flex.reservations import (
    InsufficientCapacity,
    PostgresReservationBackend,
    ReservationService,
)
from der_flex.simulators import ZONES, build_demo_fleet, build_simulator_registry

DATABASE_URL = os.getenv("DER_FLEX_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="DER_FLEX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)

START = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)
END = START + timedelta(minutes=15)


def build_offer_store() -> InMemoryOfferStore:
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
    return store


@pytest.fixture
def postgres_backend() -> PostgresReservationBackend:
    assert DATABASE_URL is not None
    backend = PostgresReservationBackend(DATABASE_URL)
    backend.initialize()
    backend.reset_for_tests()
    return backend


def reserve(service: ReservationService, key: str, power_kw: float = 30.0) -> str:
    try:
        service.create(
            idempotency_key=key,
            zone_id=ZONES[0],
            interval_start=START,
            interval_end=END,
            direction="UPWARD",
            power_kw=power_kw,
        )
        return "confirmed"
    except InsufficientCapacity:
        return "rejected"


def process_reserve(
    database_url: str, key: str, start_event: Any, results: Any
) -> None:
    service = ReservationService(
        build_offer_store(), backend=PostgresReservationBackend(database_url)
    )
    results.put("ready")
    if not start_event.wait(timeout=10):
        results.put("timeout")
        return
    results.put(reserve(service, key))


def test_two_service_instances_cannot_double_sell(
    postgres_backend: PostgresReservationBackend,
) -> None:
    assert DATABASE_URL is not None
    first = ReservationService(build_offer_store(), backend=postgres_backend)
    second = ReservationService(
        build_offer_store(), backend=PostgresReservationBackend(DATABASE_URL)
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda item: reserve(*item),
                ((first, "process-a"), (second, "process-b")),
            )
        )

    assert sorted(outcomes) == ["confirmed", "rejected"]


def test_schema_records_migration_and_database_invariants(
    postgres_backend: PostgresReservationBackend,
) -> None:
    del postgres_backend
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        versions = connection.execute(
            "SELECT version FROM der_flex_schema_migrations ORDER BY version"
        ).fetchall()
        constraints = {
            row[0]
            for row in connection.execute(
                """
                SELECT conname FROM pg_constraint
                WHERE conname LIKE 'der_flex_%'
                """
            ).fetchall()
        }

    assert versions == [(1,)]
    assert {
        "der_flex_reservation_power",
        "der_flex_reservation_status",
        "der_flex_allocation_power",
        "der_flex_activation_counts",
        "der_flex_public_residual_nonnegative",
    } <= constraints


def test_two_operating_system_processes_cannot_double_sell(
    postgres_backend: PostgresReservationBackend,
) -> None:
    del postgres_backend
    assert DATABASE_URL is not None
    context = get_context("spawn")
    start_event = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=process_reserve,
            args=(DATABASE_URL, f"os-process-{index}", start_event, results),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    assert [results.get(timeout=15) for _ in processes] == ["ready", "ready"]
    start_event.set()
    outcomes = [results.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert sorted(outcomes) == ["confirmed", "rejected"]


def test_reservation_and_idempotency_survive_service_restart(
    postgres_backend: PostgresReservationBackend,
) -> None:
    assert DATABASE_URL is not None
    first = ReservationService(build_offer_store(), backend=postgres_backend)
    created = first.create(
        idempotency_key="durable-replay",
        zone_id=ZONES[0],
        interval_start=START,
        interval_end=END,
        direction="UPWARD",
        power_kw=10.0,
    )

    restarted = ReservationService(
        build_offer_store(), backend=PostgresReservationBackend(DATABASE_URL)
    )
    loaded = restarted.get(created.reservation_id)
    replay = restarted.create(
        idempotency_key="durable-replay",
        zone_id=ZONES[0],
        interval_start=START,
        interval_end=END,
        direction="UPWARD",
        power_kw=10.0,
    )

    assert loaded == created
    assert replay.reservation_id == created.reservation_id


def test_cancel_and_activation_are_visible_across_service_instances(
    postgres_backend: PostgresReservationBackend,
) -> None:
    assert DATABASE_URL is not None
    first = ReservationService(build_offer_store(), backend=postgres_backend)
    second = ReservationService(
        build_offer_store(), backend=PostgresReservationBackend(DATABASE_URL)
    )
    cancelled = first.create(
        idempotency_key="cancel-across-processes",
        zone_id=ZONES[0],
        interval_start=START,
        interval_end=END,
        direction="UPWARD",
        power_kw=10.0,
    )
    assert second.cancel(cancelled.reservation_id).status == "CANCELLED"
    assert reserve(first, "capacity-released", 30.0) == "confirmed"

    postgres_backend.reset_for_tests()
    created = first.create(
        idempotency_key="activation-across-processes",
        zone_id=ZONES[0],
        interval_start=START,
        interval_end=END,
        direction="UPWARD",
        power_kw=10.0,
    )
    activation = second.activate(created.reservation_id)

    assert first.get_activation(activation.activation_id) == activation
    assert first.instructions_for(activation.activation_id)


def test_expiry_and_privacy_suppression_survive_restart(
    postgres_backend: PostgresReservationBackend,
) -> None:
    assert DATABASE_URL is not None
    first_store = build_offer_store()
    first = ReservationService(first_store, backend=postgres_backend)
    assert first.public_residual_capacity(first_store.query(ZONES[0], START, END))

    created_at = START - timedelta(minutes=10)
    created = first.create(
        idempotency_key="durable-expiry-and-privacy",
        zone_id=ZONES[0],
        interval_start=START,
        interval_end=END,
        direction="UPWARD",
        power_kw=3.0,
        now=created_at,
    )

    restarted_store = build_offer_store()
    restarted = ReservationService(
        restarted_store, backend=PostgresReservationBackend(DATABASE_URL)
    )
    assert restarted.public_residual_capacity(
        restarted_store.query(ZONES[0], START, END)
    ) == []
    assert restarted.get(
        created.reservation_id, now=created_at + timedelta(minutes=6)
    ).status == "EXPIRED"
    assert restarted.public_residual_capacity(
        restarted_store.query(ZONES[0], START, END)
    ) == []
