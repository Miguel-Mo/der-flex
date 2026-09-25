from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from threading import Event

import psycopg
import pytest

from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.domain import (
    DisabledResourceError,
    FlexibilityOffer,
    OfferVersionConflict,
    PostgresOfferStore,
    PostgresResourceRegistry,
    ResourceProvisioningConflict,
    StaleOfferError,
    StaleProvisioningRecord,
)
from der_flex.locking import product_lock_key
from der_flex.simulators import BatterySimulator, build_simulator_registry

DATABASE_URL = os.getenv("DER_FLEX_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="DER_FLEX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)

START = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)
END = START + timedelta(minutes=15)


@pytest.fixture
def postgres_domain() -> tuple[PostgresOfferStore, PostgresResourceRegistry]:
    assert DATABASE_URL is not None
    registry = PostgresResourceRegistry(DATABASE_URL)
    store = PostgresOfferStore(DATABASE_URL, minimum_participants=1)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """
            TRUNCATE der_flex_offers, der_flex_offer_sources,
                     der_flex_published_cohorts, der_flex_resources CASCADE
            """
        )
        connection.commit()
    return store, registry


def provision_and_normalize(
    registry: PostgresResourceRegistry,
) -> tuple[BatterySimulator, list[FlexibilityOffer]]:
    resource = BatterySimulator()
    record = build_simulator_registry([resource]).require(
        resource.resource_id, resource.zone_id
    )
    registry.register(record)
    constraints, forecast = resource.s2_offer_messages(START)
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
    return resource, offers


def test_registry_and_offers_survive_new_instances(
    postgres_domain: tuple[PostgresOfferStore, PostgresResourceRegistry],
) -> None:
    store, registry = postgres_domain
    resource, offers = provision_and_normalize(registry)
    for offer in offers:
        store.upsert(offer)
    first = store.query(resource.zone_id, START, END, now=START)

    assert DATABASE_URL is not None
    restarted_registry = PostgresResourceRegistry(DATABASE_URL)
    restarted_store = PostgresOfferStore(DATABASE_URL, minimum_participants=1)
    loaded = restarted_store.query(resource.zone_id, START, END, now=START)

    assert restarted_registry.require(resource.resource_id, resource.zone_id) == registry.require(
        resource.resource_id, resource.zone_id
    )
    assert loaded == first
    assert restarted_store.zones(now=START) == [resource.zone_id]


def test_offer_order_conflicts_disconnect_and_privacy_survive_restart(
    postgres_domain: tuple[PostgresOfferStore, PostgresResourceRegistry],
) -> None:
    store, registry = postgres_domain
    resource, offers = provision_and_normalize(registry)
    offer = offers[0]
    store.upsert(offer)
    assert store.query(resource.zone_id, START, END, now=START)

    with pytest.raises(OfferVersionConflict):
        store.upsert(
            offer.model_copy(update={"upward_capacity_kw": offer.upward_capacity_kw / 2})
        )

    advanced = offer.model_copy(
        update={
            "source_sequence": offer.source_sequence + 1,
            "source_version": f"{offer.source_version}-advanced",
            "upward_capacity_kw": offer.upward_capacity_kw / 2,
        }
    )
    store.upsert(advanced)
    assert store.query(resource.zone_id, START, END, now=START) == []

    assert store.remove_resource(resource.resource_id) == 1
    with pytest.raises(StaleOfferError):
        store.upsert(advanced)

    assert DATABASE_URL is not None
    restarted = PostgresOfferStore(DATABASE_URL, minimum_participants=1)
    assert restarted.query(resource.zone_id, START, END, now=START) == []


def test_registry_preserves_monotonic_operator_versions(
    postgres_domain: tuple[PostgresOfferStore, PostgresResourceRegistry],
) -> None:
    _store, registry = postgres_domain
    resource, _offers = provision_and_normalize(registry)
    current = registry.require(resource.resource_id, resource.zone_id)
    registry.register(current)

    with pytest.raises(ResourceProvisioningConflict):
        registry.register(current.model_copy(update={"enabled": False}))
    with pytest.raises(StaleProvisioningRecord):
        registry.register(current.model_copy(update={"provisioning_version": 0}))

    updated = current.model_copy(
        update={"provisioning_version": 2, "enabled": False}
    )
    registry.register(updated)
    assert DATABASE_URL is not None
    restarted = PostgresResourceRegistry(DATABASE_URL)
    with pytest.raises(DisabledResourceError, match="disabled"):
        restarted.require(resource.resource_id, resource.zone_id)


def test_reprovisioning_revokes_old_offers_and_suppresses_published_cell(
    postgres_domain: tuple[PostgresOfferStore, PostgresResourceRegistry],
) -> None:
    store, registry = postgres_domain
    resource, offers = provision_and_normalize(registry)
    for offer in offers:
        store.upsert(offer)
    assert store.query(resource.zone_id, START, END, now=START)

    current = registry.require(resource.resource_id, resource.zone_id)
    registry.register(
        current.model_copy(
            update={
                "provisioning_version": current.provisioning_version + 1,
                "max_power_kw": current.max_power_kw / 2,
            }
        )
    )

    assert store.eligible_offers(
        zone_id=resource.zone_id,
        interval_start=START,
        interval_end=END,
        consequence_type=offers[0].consequence_type,
        now=START,
    ) == []
    assert store.query(resource.zone_id, START, END, now=START) == []
    with pytest.raises(StaleOfferError):
        store.upsert(offers[0])


def test_conflicting_concurrent_offer_is_never_silently_overwritten(
    postgres_domain: tuple[PostgresOfferStore, PostgresResourceRegistry],
) -> None:
    store, registry = postgres_domain
    _resource, offers = provision_and_normalize(registry)
    original = offers[0]
    conflicting = original.model_copy(
        update={"upward_capacity_kw": original.upward_capacity_kw / 2}
    )

    def attempt(candidate: FlexibilityOffer) -> str:
        try:
            store.upsert(candidate)
            return "accepted"
        except OfferVersionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, (original, conflicting)))

    assert sorted(outcomes) == ["accepted", "conflict"]


def test_schema_version_two_is_recorded(
    postgres_domain: tuple[PostgresOfferStore, PostgresResourceRegistry],
) -> None:
    del postgres_domain
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
                WHERE conname LIKE 'der_flex_resource_%'
                   OR conname LIKE 'der_flex_offer_%'
                """
            ).fetchall()
        }
    assert versions == [(1,), (2,)]
    assert {
        "der_flex_resource_power",
        "der_flex_resource_energy",
        "der_flex_offer_baseline",
        "der_flex_offer_capacity",
        "der_flex_offer_position",
    } <= constraints


def test_offer_update_waits_for_reservation_product_lock(
    postgres_domain: tuple[PostgresOfferStore, PostgresResourceRegistry],
) -> None:
    store, registry = postgres_domain
    _resource, offers = provision_and_normalize(registry)
    offer = offers[0]
    store.upsert(offer)
    advanced = offer.model_copy(
        update={
            "source_sequence": offer.source_sequence + 1,
            "source_version": f"{offer.source_version}-locked",
        }
    )
    lock_key = product_lock_key(
        offer.zone_id,
        offer.interval_start,
        offer.interval_end,
        offer.consequence_type,
        "UPWARD",
    )
    started = Event()

    def update_offer() -> None:
        started.set()
        store.upsert(advanced)

    assert DATABASE_URL is not None
    with ThreadPoolExecutor(max_workers=1) as executor:
        with psycopg.connect(DATABASE_URL) as connection, connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (lock_key,),
            )
            future = executor.submit(update_offer)
            assert started.wait(timeout=2)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        future.result(timeout=2)
