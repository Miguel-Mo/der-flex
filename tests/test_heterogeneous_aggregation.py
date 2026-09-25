from copy import deepcopy
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.domain import InMemoryOfferStore
from der_flex.simulators import (
    ZONES,
    BatterySimulator,
    build_demo_fleet,
    build_scenario,
    build_simulator_registry,
)

START = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)
END = START + timedelta(minutes=15)


def ingest(resource: BatterySimulator, store: InMemoryOfferStore, start: datetime = START) -> None:
    constraints, forecast = resource.s2_offer_messages(start)
    for offer in normalize_pebc_offer(
        resource_id=resource.resource_id,
        zone_id=resource.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=build_simulator_registry([resource]),
        source_epoch=resource.source_epoch,
        source_sequence=resource.source_sequence,
        observed_at=start,
        received_at=start,
    ):
        store.upsert(offer)


def test_fleet_contains_three_profiles_in_each_zone() -> None:
    fleet = build_demo_fleet()

    assert len(fleet) == 36
    for zone in ZONES:
        zone_resources = [resource for resource in fleet if resource.zone_id == zone]
        assert len(zone_resources) == 12
        assert {type(resource).__name__ for resource in zone_resources} == {
            "BatterySimulator",
            "EVSESimulator",
            "HeatPumpSimulator",
        }


def test_heterogeneous_fleet_is_published_only_after_privacy_threshold() -> None:
    store = InMemoryOfferStore(minimum_participants=10)
    fleet = build_demo_fleet()
    for resource in fleet:
        ingest(resource, store)

    aggregates = store.query(ZONES[0], START, END, now=START)

    assert len(aggregates) == 1
    assert aggregates[0].participant_count == 12
    assert aggregates[0].upward_capacity_kw > 0
    assert aggregates[0].downward_capacity_kw > 0
    assert store.zones(now=START) == sorted(ZONES)


def test_disconnect_removes_offer_and_suppresses_zone() -> None:
    store = InMemoryOfferStore(minimum_participants=10)
    resources = [resource for resource in build_demo_fleet() if resource.zone_id == ZONES[0]]
    for resource in resources:
        ingest(resource, store)

    assert store.remove_resource(resources[0].resource_id) == 1
    assert store.remove_resource(resources[1].resource_id) == 1
    assert store.remove_resource(resources[2].resource_id) == 1

    assert store.query(ZONES[0], START, END, now=START) == []


def test_published_cell_is_suppressed_after_cohort_change_to_prevent_difference_attack() -> None:
    store = InMemoryOfferStore(minimum_participants=10)
    resources = [resource for resource in build_demo_fleet() if resource.zone_id == ZONES[0]]
    for resource in resources:
        ingest(resource, store)

    original = store.query(ZONES[0], START, END, now=START)
    assert original[0].participant_count == 12

    store.remove_resource(resources[0].resource_id)

    assert store.query(ZONES[0], START, END, now=START) == []


def test_published_cell_is_suppressed_after_offer_value_changes() -> None:
    store = InMemoryOfferStore(minimum_participants=10)
    resources = [resource for resource in build_demo_fleet() if resource.zone_id == ZONES[0]]
    for resource in resources:
        ingest(resource, store)

    original = store.query(ZONES[0], START, END, now=START)
    assert original[0].participant_count == 12

    battery = next(resource for resource in resources if type(resource) is BatterySimulator)
    battery.set_power(battery.power_kw + 0.1)
    ingest(battery, store)

    assert store.query(ZONES[0], START, END, now=START) == []


def test_adjacent_cell_is_suppressed_when_participant_cohort_changes() -> None:
    store = InMemoryOfferStore(minimum_participants=10)
    resources = [resource for resource in build_demo_fleet() if resource.zone_id == ZONES[0]]
    for resource in resources:
        ingest(resource, store)

    for resource in resources[1:]:
        ingest(resource, store, start=END)
    constraints, forecast = resources[0].s2_offer_messages(END)
    replacement_id = f"{resources[0].resource_id}-replacement"
    resource_registry = build_simulator_registry([resources[0]])
    original_record = resource_registry.require(resources[0].resource_id, resources[0].zone_id)
    resource_registry.register(original_record.model_copy(update={"resource_id": replacement_id}))
    replacement = normalize_pebc_offer(
        resource_id=replacement_id,
        zone_id=resources[0].zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=resource_registry,
        source_epoch=resources[0].source_epoch,
        source_sequence=resources[0].source_sequence,
        observed_at=END,
        received_at=END,
    )[0]
    store.upsert(replacement)

    aggregates = store.query(ZONES[0], START, END + timedelta(minutes=15), now=START)

    assert len(aggregates) == 1
    assert aggregates[0].interval_start == START


def test_expired_offers_are_never_published() -> None:
    store = InMemoryOfferStore(minimum_participants=1)
    resource = BatterySimulator()
    ingest(resource, store)

    assert store.query(ZONES[0], START, END, now=END + timedelta(seconds=1)) == []


def test_duplicate_offer_is_idempotent() -> None:
    store = InMemoryOfferStore(minimum_participants=1)
    resource = BatterySimulator()
    ingest(resource, store)
    first = store.query(resource.zone_id, START, END, now=START)[0]

    ingest(resource, store)
    replay = store.query(resource.zone_id, START, END, now=START)[0]

    assert replay.participant_count == 1
    assert replay.upward_capacity_kw == first.upward_capacity_kw
    assert replay.downward_capacity_kw == first.downward_capacity_kw


def test_utc_intervals_remain_contiguous_across_madrid_daylight_saving_change() -> None:
    transition_start = datetime(2026, 3, 29, 0, 30, tzinfo=UTC)
    resource = BatterySimulator()
    constraints, forecast = resource.s2_offer_messages(transition_start, interval_count=4)

    offers = normalize_pebc_offer(
        resource_id=resource.resource_id,
        zone_id=resource.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=build_simulator_registry([resource]),
        source_epoch=resource.source_epoch,
        source_sequence=resource.source_sequence,
        observed_at=transition_start,
        received_at=transition_start,
    )

    assert len(offers) == 4
    assert all(
        offers[index].interval_end == offers[index + 1].interval_start
        for index in range(len(offers) - 1)
    )
    madrid = ZoneInfo("Europe/Madrid")
    assert offers[0].interval_start.astimezone(madrid).utcoffset() == timedelta(hours=1)
    assert offers[-1].interval_end.astimezone(madrid).utcoffset() == timedelta(hours=2)


def test_misaligned_forecast_is_resampled_conservatively() -> None:
    resource = BatterySimulator(power_kw=1.0)
    start = START + timedelta(minutes=5)
    constraints, forecast = resource.s2_offer_messages(start)
    constraints["valid_until"] = (start + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    first_element = forecast["elements"][0]
    first_element["duration"] = 600_000
    second_element = deepcopy(first_element)
    second_element["power_values"][0]["value_expected"] = 2000.0
    third_element = deepcopy(first_element)
    third_element["power_values"][0]["value_expected"] = 3000.0
    forecast["elements"] = [first_element, second_element, third_element]

    offers = normalize_pebc_offer(
        resource_id=resource.resource_id,
        zone_id=resource.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=build_simulator_registry([resource]),
        source_epoch=resource.source_epoch,
        source_sequence=resource.source_sequence,
        observed_at=start,
        received_at=start,
    )

    assert len(offers) == 1
    assert offers[0].interval_start == START + timedelta(minutes=15)
    assert offers[0].interval_end == START + timedelta(minutes=30)
    assert offers[0].baseline_power_kw == pytest.approx(2.333333)
    assert offers[0].upward_capacity_kw == pytest.approx(7.0)
    assert offers[0].downward_capacity_kw == 2.0


def test_named_scenarios_are_deterministic_and_model_departures() -> None:
    normal = build_scenario("normal_afternoon")
    peak = build_scenario("demand_peak")
    departure = build_scenario("ev_departure")
    connectivity = build_scenario("connectivity_loss")

    assert len(normal.connected_resources) == 36
    assert len(peak.connected_resources) == 36
    assert sum(resource.power_kw for resource in peak.connected_resources) > sum(
        resource.power_kw for resource in normal.connected_resources
    )
    assert len(departure.connected_resources) == 24
    assert len(departure.disconnected_resource_ids) == 12
    assert len(connectivity.connected_resources) == 27
    assert len(connectivity.disconnected_resource_ids) == 9
