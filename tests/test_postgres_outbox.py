from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.adapters.webhooks import WebhookDispatcher
from der_flex.domain import InMemoryOfferStore
from der_flex.outbox import OutboxProcessor
from der_flex.reservations import PostgresReservationBackend, ReservationService
from der_flex.reservations.backend import OutboxTask
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


def create_reservation(
    service: ReservationService,
    key: str,
    *,
    callback_url: str | None = None,
    power_kw: float = 10.0,
) -> uuid.UUID:
    reservation = service.create(
        idempotency_key=key,
        zone_id=ZONES[0],
        interval_start=START,
        interval_end=END,
        direction="UPWARD",
        power_kw=power_kw,
        callback_url=callback_url,
    )
    return reservation.reservation_id


def test_commit_precedes_side_effects_and_restart_drains_outbox(
    postgres_backend: PostgresReservationBackend,
) -> None:
    webhook_calls: list[tuple[str, str]] = []
    resource_calls: list[str] = []

    def send_webhook(url: str, _body: bytes, headers: dict[str, str]) -> int:
        webhook_calls.append((url, headers["X-DER-Flex-Event"]))
        return 204

    def accept_resource(resource: str, _payload: dict[str, object]) -> bool:
        resource_calls.append(resource)
        return True

    dispatcher = WebhookDispatcher(
        secret="test-secret",
        sender=send_webhook,
    )
    service = ReservationService(
        build_offer_store(),
        backend=postgres_backend,
        webhook_dispatcher=dispatcher,
        resource_acceptor=accept_resource,
        process_outbox_on_activate=False,
    )
    reservation_id = create_reservation(
        service, "crash-after-commit", callback_url="https://consumer.invalid/events"
    )

    pending = service.activate(reservation_id)

    assert pending.status == "PENDING"
    assert webhook_calls == []
    assert resource_calls == []
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        rows = connection.execute(
            "SELECT kind, status FROM der_flex_outbox ORDER BY kind"
        ).fetchall()
    assert rows
    assert {status for _kind, status in rows} == {"PENDING"}

    restarted = OutboxProcessor(
        PostgresReservationBackend(DATABASE_URL),
        dispatcher,
        accept_resource,
    )
    assert restarted.drain() >= 3
    completed = service.get_activation(pending.activation_id)
    assert completed.status == "COMPLETED"
    assert completed.accepted_instruction_count == completed.instruction_count
    assert [event for _url, event in webhook_calls] == [
        "activation.accepted",
        "activation.started",
        "activation.completed",
    ]


def test_webhook_retry_survives_restart_with_stable_event_ids(
    postgres_backend: PostgresReservationBackend,
) -> None:
    failed_headers: list[dict[str, str]] = []

    def reject_webhook(_url: str, _body: bytes, headers: dict[str, str]) -> int:
        failed_headers.append(headers)
        raise RuntimeError("unexpected adapter failure")

    failing = WebhookDispatcher(
        secret="test-secret",
        sender=reject_webhook,
    )
    service = ReservationService(
        build_offer_store(),
        backend=postgres_backend,
        webhook_dispatcher=failing,
    )
    reservation_id = create_reservation(
        service, "webhook-retry", callback_url="https://consumer.invalid/events"
    )
    activation = service.activate(reservation_id)
    assert activation.status == "COMPLETED"
    assert failed_headers

    delivered_ids: list[str] = []

    def deliver_webhook(_url: str, _body: bytes, headers: dict[str, str]) -> int:
        delivered_ids.append(headers["X-DER-Flex-Event-ID"])
        return 204

    succeeding = WebhookDispatcher(
        secret="test-secret",
        sender=deliver_webhook,
    )
    processor = OutboxProcessor(postgres_backend, succeeding, lambda _resource, _payload: True)
    processor.drain(now=datetime.now(UTC) + timedelta(minutes=10))

    assert len(delivered_ids) == 3
    assert len(set(delivered_ids)) == 3
    assert {headers["X-DER-Flex-Event-ID"] for headers in failed_headers} == set(delivered_ids)
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        statuses = connection.execute(
            "SELECT DISTINCT status FROM der_flex_outbox WHERE kind = 'WEBHOOK'"
        ).fetchall()
    assert statuses == [("DELIVERED",)]


def test_resource_rejection_finalizes_failed_and_releases_allocation(
    postgres_backend: PostgresReservationBackend,
) -> None:
    service = ReservationService(
        build_offer_store(),
        backend=postgres_backend,
        resource_acceptor=lambda _resource, _payload: False,
    )
    reservation_id = create_reservation(service, "resource-rejection")

    activation = service.activate(reservation_id)

    assert activation.status == "FAILED"
    assert activation.accepted_instruction_count == 0
    assert activation.rejected_instruction_count == activation.instruction_count
    assert service.get(reservation_id).allocated_power_kw == 0


def test_transient_resource_failure_is_retried_after_restart(
    postgres_backend: PostgresReservationBackend,
) -> None:
    service = ReservationService(
        build_offer_store(),
        backend=postgres_backend,
        process_outbox_on_activate=False,
    )
    reservation_id = create_reservation(service, "resource-retry")
    activation = service.activate(reservation_id)

    def unavailable(_resource: str, _payload: dict[str, object]) -> bool:
        raise OSError("resource gateway unavailable")

    first_worker = OutboxProcessor(postgres_backend, WebhookDispatcher(), unavailable)
    first_worker.run_once()
    assert service.get_activation(activation.activation_id).status == "PENDING"

    restarted_worker = OutboxProcessor(
        postgres_backend,
        WebhookDispatcher(),
        lambda _resource, _payload: True,
    )
    restarted_worker.drain(now=datetime.now(UTC) + timedelta(minutes=10))
    completed = service.get_activation(activation.activation_id)
    assert completed.status == "COMPLETED"
    assert completed.accepted_instruction_count == completed.instruction_count


def test_expired_worker_lease_is_reclaimed(
    postgres_backend: PostgresReservationBackend,
) -> None:
    service = ReservationService(
        build_offer_store(),
        backend=postgres_backend,
        process_outbox_on_activate=False,
    )
    reservation_id = create_reservation(service, "lease-recovery")
    service.activate(reservation_id)
    now = datetime.now(UTC)

    claimed = postgres_backend.claim_outbox(now=now, limit=100, lease_seconds=30)
    assert claimed
    assert (
        postgres_backend.claim_outbox(now=now + timedelta(seconds=29), limit=100, lease_seconds=30)
        == ()
    )
    reclaimed = postgres_backend.claim_outbox(
        now=now + timedelta(seconds=31), limit=100, lease_seconds=30
    )

    assert {task.event_id for task in reclaimed} == {task.event_id for task in claimed}
    assert all(task.attempts == 2 for task in reclaimed)
    postgres_backend.resolve_outbox(
        claimed[0],
        delivered=True,
        retryable=False,
        error=None,
        now=now + timedelta(seconds=32),
        max_attempts=8,
    )
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        stale_status = connection.execute(
            "SELECT status, attempts FROM der_flex_outbox WHERE event_id = %s",
            (claimed[0].event_id,),
        ).fetchone()
    assert stale_status == ("PROCESSING", 2)


def test_two_workers_never_claim_the_same_event(
    postgres_backend: PostgresReservationBackend,
) -> None:
    assert DATABASE_URL is not None
    service = ReservationService(
        build_offer_store(),
        backend=postgres_backend,
        process_outbox_on_activate=False,
    )
    reservation_id = create_reservation(
        service, "parallel-claim", callback_url="https://consumer.invalid/events"
    )
    service.activate(reservation_id)
    backends = (
        PostgresReservationBackend(DATABASE_URL),
        PostgresReservationBackend(DATABASE_URL),
    )
    now = datetime.now(UTC)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(
            executor.map(
                lambda backend: backend.claim_outbox(now=now, limit=2, lease_seconds=30),
                backends,
            )
        )

    first_ids = {task.event_id for task in claims[0]}
    second_ids = {task.event_id for task in claims[1]}
    assert first_ids
    assert second_ids
    assert first_ids.isdisjoint(second_ids)


def test_outbox_enqueue_rolls_back_with_transaction(
    postgres_backend: PostgresReservationBackend,
) -> None:
    service = ReservationService(
        build_offer_store(),
        backend=postgres_backend,
        process_outbox_on_activate=False,
    )
    reservation_id = create_reservation(service, "transaction-rollback")
    activation = service.activate(reservation_id)
    task = OutboxTask(
        event_id=uuid.uuid4(),
        kind="WEBHOOK",
        destination="https://consumer.invalid/events",
        event_type="test.rollback",
        payload={"probe": True},
        attempts=0,
        activation_id=activation.activation_id,
        reservation_id=reservation_id,
    )

    with pytest.raises(RuntimeError, match="rollback"), postgres_backend.transaction() as state:
        state.enqueue_outbox(task, available_at=datetime.now(UTC))
        raise RuntimeError("rollback")

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM der_flex_outbox WHERE event_id = %s", (task.event_id,)
        ).fetchone()
    assert count == (0,)
