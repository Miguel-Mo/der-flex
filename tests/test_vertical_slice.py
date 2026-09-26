import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, cast

import httpx2 as httpx
import pytest
from fastapi import FastAPI

from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.api import create_app
from der_flex.domain import InMemoryOfferStore, OfferVersionConflict, StaleOfferError
from der_flex.privacy import PrivacyPublicationPolicy
from der_flex.simulators import BatterySimulator, build_simulator_registry

START = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)
UNIT_POLICY = PrivacyPublicationPolicy(
    minimum_participants=1,
    participant_bucket_size=1,
    power_resolution_kw=0.001,
    energy_resolution_kwh=0.001,
    confidence_resolution=0.001,
)


def ingest_battery(battery: BatterySimulator, store: InMemoryOfferStore) -> None:
    constraints, forecast = battery.s2_offer_messages(START)
    offers = normalize_pebc_offer(
        resource_id=battery.resource_id,
        zone_id=battery.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=build_simulator_registry([battery]),
        source_epoch=battery.source_epoch,
        observed_at=START,
        received_at=START,
        source_sequence=battery.source_sequence,
    )
    assert len(offers) == 1
    store.upsert(offers[0])


async def query(app: FastAPI) -> dict[str, Any]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/flexibility",
            params={
                "zone_id": "ES-MA-29700",
                "from": "2030-01-01T18:00:00Z",
                "to": "2030-01-01T18:15:00Z",
            },
        )
    assert response.status_code == 200
    return cast(dict[str, Any], response.json()["data"][0])


def test_s2_offer_is_normalized_and_exposed_by_rest_api() -> None:
    battery = BatterySimulator(power_kw=2.0)
    store = InMemoryOfferStore(minimum_participants=1)
    ingest_battery(battery, store)

    aggregate = asyncio.run(query(create_app(store, privacy_policy=UNIT_POLICY)))

    assert aggregate["baseline_power_kw"] == 2.0
    assert aggregate["upward_capacity_kw"] == 7.0
    assert aggregate["downward_capacity_kw"] == 3.0
    assert aggregate["participant_count"] == 1
    assert aggregate["product_class"] == "BEST_EFFORT_PEBC"


def test_a_new_battery_state_replaces_the_interval_offer() -> None:
    battery = BatterySimulator(power_kw=2.0)
    store = InMemoryOfferStore(minimum_participants=1)
    ingest_battery(battery, store)
    battery.set_power(-1.0)
    ingest_battery(battery, store)

    aggregate = asyncio.run(query(create_app(store, privacy_policy=UNIT_POLICY)))

    assert aggregate["baseline_power_kw"] == -1.0
    assert aggregate["upward_capacity_kw"] == 4.0
    assert aggregate["downward_capacity_kw"] == 6.0


def test_privacy_threshold_suppresses_single_participant() -> None:
    battery = BatterySimulator()
    store = InMemoryOfferStore(minimum_participants=10)
    ingest_battery(battery, store)

    result = store.query(
        battery.zone_id,
        START,
        datetime(2030, 1, 1, 18, 15, tzinfo=UTC),
        now=START,
    )

    assert result == []


def test_battery_rejects_power_outside_physical_limits() -> None:
    with pytest.raises(ValueError, match="outside battery limits"):
        BatterySimulator().set_power(6.0)
    with pytest.raises(ValueError, match="initial power"):
        BatterySimulator(power_kw=6.0)


def test_out_of_order_offer_cannot_replace_newer_resource_state() -> None:
    battery = BatterySimulator(power_kw=2.0)
    constraints, forecast = battery.s2_offer_messages(START)
    older = normalize_pebc_offer(
        resource_id=battery.resource_id,
        zone_id=battery.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=build_simulator_registry([battery]),
        source_epoch=battery.source_epoch,
        source_sequence=battery.source_sequence,
        observed_at=START,
        received_at=START,
    )[0]
    battery.set_power(-1.0)
    constraints, forecast = battery.s2_offer_messages(START)
    newer = normalize_pebc_offer(
        resource_id=battery.resource_id,
        zone_id=battery.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=build_simulator_registry([battery]),
        source_epoch=battery.source_epoch,
        source_sequence=battery.source_sequence,
        observed_at=START,
        received_at=START,
    )[0]
    store = InMemoryOfferStore(minimum_participants=1)

    store.upsert(older)
    store.upsert(newer)
    with pytest.raises(StaleOfferError, match="older"):
        store.upsert(older)

    aggregate = store.query(battery.zone_id, START, older.interval_end, now=START)[0]
    assert aggregate.baseline_power_kw == -1.0


def test_same_offer_sequence_rejects_conflicting_content() -> None:
    battery = BatterySimulator(power_kw=2.0)
    constraints, forecast = battery.s2_offer_messages(START)
    offer = normalize_pebc_offer(
        resource_id=battery.resource_id,
        zone_id=battery.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=build_simulator_registry([battery]),
        source_epoch=battery.source_epoch,
        source_sequence=battery.source_sequence,
        observed_at=START,
        received_at=START,
    )[0]
    conflict = offer.model_copy(update={"baseline_power_kw": -1.0})
    store = InMemoryOfferStore(minimum_participants=1)

    store.upsert(offer)
    with pytest.raises(OfferVersionConflict, match="conflicting"):
        store.upsert(conflict)


def test_physical_envelope_clamps_overstated_s2_constraints() -> None:
    battery = BatterySimulator(power_kw=2.0)
    constraints, forecast = battery.s2_offer_messages(START)
    overstated = deepcopy(constraints)
    for limit in overstated["allowed_limit_ranges"]:
        limit["range_boundary"] = {
            "start_of_range": -50_000.0,
            "end_of_range": 50_000.0,
        }

    offer = normalize_pebc_offer(
        resource_id=battery.resource_id,
        zone_id=battery.zone_id,
        constraints_message=overstated,
        forecast_message=forecast,
        resource_registry=build_simulator_registry([battery]),
        source_epoch=battery.source_epoch,
        source_sequence=battery.source_sequence,
        observed_at=START,
        received_at=START,
    )[0]

    assert offer.upward_capacity_kw == 7.0
    assert offer.downward_capacity_kw == 3.0


def test_forecast_outside_physical_envelope_is_rejected() -> None:
    battery = BatterySimulator(power_kw=2.0)
    constraints, forecast = battery.s2_offer_messages(START)
    invalid_forecast = deepcopy(forecast)
    value = invalid_forecast["elements"][0]["power_values"][0]
    value.update(
        {
            "value_expected": 6_000.0,
            "value_lower_95PPR": 5_900.0,
            "value_upper_95PPR": 6_100.0,
        }
    )

    with pytest.raises(ValueError, match="outside the physical power envelope"):
        normalize_pebc_offer(
            resource_id=battery.resource_id,
            zone_id=battery.zone_id,
            constraints_message=constraints,
            forecast_message=invalid_forecast,
            resource_registry=build_simulator_registry([battery]),
            source_epoch=battery.source_epoch,
            source_sequence=battery.source_sequence,
            observed_at=START,
            received_at=START,
        )


def test_multi_interval_offer_never_exceeds_available_discharge_energy() -> None:
    battery = BatterySimulator(power_kw=0.0, state_of_charge=0.20)
    constraints, forecast = battery.s2_offer_messages(START, interval_count=4)

    offers = normalize_pebc_offer(
        resource_id=battery.resource_id,
        zone_id=battery.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=build_simulator_registry([battery]),
        source_epoch=battery.source_epoch,
        source_sequence=battery.source_sequence,
        observed_at=START,
        received_at=START,
    )

    assert len(offers) == 4
    assert sum(offer.upward_energy_kwh for offer in offers) == pytest.approx(1.0)
    assert offers[0].upward_capacity_kw == pytest.approx(4.0)
    assert all(offer.upward_capacity_kw == 0.0 for offer in offers[1:])


def test_ramp_rate_caps_both_flexibility_directions() -> None:
    battery = BatterySimulator(power_kw=2.0, max_ramp_kw_per_min=0.5)
    constraints, forecast = battery.s2_offer_messages(START)

    offer = normalize_pebc_offer(
        resource_id=battery.resource_id,
        zone_id=battery.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=build_simulator_registry([battery]),
        source_epoch=battery.source_epoch,
        source_sequence=battery.source_sequence,
        observed_at=START,
        received_at=START,
    )[0]

    assert offer.upward_capacity_kw == 0.5
    assert offer.downward_capacity_kw == 0.5


def test_forecast_that_crosses_energy_envelope_is_rejected() -> None:
    battery = BatterySimulator(power_kw=5.0, state_of_charge=0.89)
    constraints, forecast = battery.s2_offer_messages(START)
    forecast["elements"][0]["power_values"][0].update(
        {
            "value_expected": 5_000.0,
            "value_lower_95PPR": 4_800.0,
            "value_upper_95PPR": 5_000.0,
        }
    )

    with pytest.raises(ValueError, match="outside the physical energy envelope"):
        normalize_pebc_offer(
            resource_id=battery.resource_id,
            zone_id=battery.zone_id,
            constraints_message=constraints,
            forecast_message=forecast,
            resource_registry=build_simulator_registry([battery]),
            source_epoch=battery.source_epoch,
            source_sequence=battery.source_sequence,
            observed_at=START,
            received_at=START,
        )


def test_s2_constraint_horizon_longer_than_one_day_is_rejected() -> None:
    battery = BatterySimulator()
    constraints, forecast = battery.s2_offer_messages(START)
    constraints["valid_until"] = "2030-01-02T18:00:01Z"

    with pytest.raises(ValueError, match="cannot exceed one day"):
        normalize_pebc_offer(
            resource_id=battery.resource_id,
            zone_id=battery.zone_id,
            constraints_message=constraints,
            forecast_message=forecast,
            resource_registry=build_simulator_registry([battery]),
            source_epoch=battery.source_epoch,
            source_sequence=battery.source_sequence,
            observed_at=START,
            received_at=START,
        )
