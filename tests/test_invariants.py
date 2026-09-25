from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.domain import (
    FlexibilityOffer,
    InMemoryOfferStore,
    InMemoryResourceRegistry,
    ProvisionedResource,
)
from der_flex.reservations import InsufficientCapacity, ReservationService
from der_flex.simulators import (
    ZONES,
    BatterySimulator,
    build_demo_fleet,
    build_simulator_registry,
)

START = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)
END = START + timedelta(minutes=15)


def offers_for(resources: list[BatterySimulator]) -> list[FlexibilityOffer]:
    offers: list[FlexibilityOffer] = []
    resource_registry = build_simulator_registry(resources)
    for resource in resources:
        constraints, forecast = resource.s2_offer_messages(START)
        offers.extend(
            normalize_pebc_offer(
                resource_id=resource.resource_id,
                zone_id=resource.zone_id,
                constraints_message=constraints,
                forecast_message=forecast,
                resource_registry=resource_registry,
                source_epoch=resource.source_epoch,
                source_sequence=resource.source_sequence,
                observed_at=START,
                received_at=START,
            )
        )
    return offers


def test_offer_arrival_order_does_not_change_unpublished_aggregate() -> None:
    resources = [resource for resource in build_demo_fleet() if resource.zone_id == ZONES[0]]
    offers = offers_for(resources)
    expected = None

    for seed in range(20):
        shuffled = offers.copy()
        random.Random(seed).shuffle(shuffled)
        store = InMemoryOfferStore(minimum_participants=10)
        for offer in shuffled:
            store.upsert(offer)
        aggregate = store.query(ZONES[0], START, END, now=START)[0].model_dump(
            exclude={"generated_at"}
        )
        expected = aggregate if expected is None else expected
        assert aggregate == expected


def test_random_physical_envelopes_are_never_exceeded() -> None:
    generator = random.Random(20260912)

    for index in range(100):
        maximum_discharge = generator.uniform(0.1, 15.0)
        maximum_charge = generator.uniform(0.1, 15.0)
        baseline = generator.uniform(-maximum_discharge, maximum_charge)
        resource = BatterySimulator(
            resource_id=f"physical-{index}",
            power_kw=baseline,
            max_charge_kw=maximum_charge,
            max_discharge_kw=maximum_discharge,
        )
        constraints, forecast = resource.s2_offer_messages(START)
        for limit in constraints["allowed_limit_ranges"]:
            limit["range_boundary"] = {
                "start_of_range": -1_000_000.0,
                "end_of_range": 1_000_000.0,
            }
        resource_registry = InMemoryResourceRegistry(
            [
                ProvisionedResource(
                    resource_id=resource.resource_id,
                    zone_id=resource.zone_id,
                    resource_type="BATTERY",
                    min_power_kw=-maximum_discharge,
                    max_power_kw=maximum_charge,
                    min_energy_kwh=0.0,
                    max_energy_kwh=200.0,
                    initial_energy_kwh=100.0,
                    ramp_rate_kw_per_min=resource.max_ramp_kw_per_min,
                    provisioning_version=1,
                    provisioned_at=START,
                )
            ]
        )
        offer = normalize_pebc_offer(
            resource_id=resource.resource_id,
            zone_id=resource.zone_id,
            constraints_message=constraints,
            forecast_message=forecast,
            resource_registry=resource_registry,
            source_epoch=resource.source_epoch,
            source_sequence=resource.source_sequence,
            observed_at=START,
            received_at=START,
        )[0]

        tolerance_kw = 1e-9
        assert (
            offer.baseline_power_kw - offer.upward_capacity_kw >= -maximum_discharge - tolerance_kw
        )
        assert offer.baseline_power_kw + offer.downward_capacity_kw <= maximum_charge + tolerance_kw


def test_reservation_burst_never_oversells_or_makes_residual_negative() -> None:
    resources = [resource for resource in build_demo_fleet() if resource.zone_id == ZONES[0]]
    store = InMemoryOfferStore(minimum_participants=10)
    for offer in offers_for(resources):
        store.upsert(offer)
    service = ReservationService(store)
    offered = store.query(ZONES[0], START, END, now=START)[0].upward_capacity_kw

    def attempt(index: int) -> float:
        try:
            reservation = service.create(
                idempotency_key=f"burst-{index}",
                zone_id=ZONES[0],
                interval_start=START,
                interval_end=END,
                direction="UPWARD",
                power_kw=1.0,
                now=START - timedelta(minutes=1),
            )
            return reservation.allocated_power_kw
        except InsufficientCapacity:
            return 0.0

    with ThreadPoolExecutor(max_workers=16) as executor:
        allocated = list(executor.map(attempt, range(100)))

    residual = service.apply_residual_capacity(store.query(ZONES[0], START, END, now=START))[0]
    assert sum(allocated) <= offered
    assert residual.upward_capacity_kw >= 0
    assert residual.upward_capacity_kw == round(offered - sum(allocated), 6)


def test_random_multi_interval_trajectories_respect_energy_and_ramp() -> None:
    generator = random.Random(20260913)

    for index in range(100):
        ramp_rate = generator.uniform(0.05, 5.0)
        resource = BatterySimulator(
            resource_id=f"trajectory-{index}",
            power_kw=0.0,
            state_of_charge=generator.uniform(0.15, 0.85),
            max_ramp_kw_per_min=ramp_rate,
        )
        constraints, forecast = resource.s2_offer_messages(START, interval_count=8)
        forecast = deepcopy(forecast)
        baseline_energy = resource.stored_energy_kwh
        for element in forecast["elements"]:
            desired_power = generator.uniform(-resource.max_discharge_kw, resource.max_charge_kw)
            feasible_minimum = max(
                -resource.max_discharge_kw,
                (resource.minimum_energy_kwh - baseline_energy) / 0.25,
            )
            feasible_maximum = min(
                resource.max_charge_kw,
                (resource.maximum_energy_kwh - baseline_energy) / 0.25,
            )
            baseline_power = max(feasible_minimum, min(feasible_maximum, desired_power))
            value = element["power_values"][0]
            value.update(
                {
                    "value_expected": baseline_power * 1000,
                    "value_lower_95PPR": (baseline_power - 0.2) * 1000,
                    "value_upper_95PPR": (baseline_power + 0.2) * 1000,
                }
            )
            baseline_energy += baseline_power * 0.25

        offers = normalize_pebc_offer(
            resource_id=resource.resource_id,
            zone_id=resource.zone_id,
            constraints_message=constraints,
            forecast_message=forecast,
            resource_registry=build_simulator_registry([resource]),
            source_epoch=resource.source_epoch,
            source_sequence=resource.source_sequence,
            observed_at=START,
            received_at=START,
        )
        upward_energy = resource.stored_energy_kwh
        downward_energy = resource.stored_energy_kwh
        for offer in offers:
            hours = (offer.interval_end - offer.interval_start).total_seconds() / 3600
            upward_energy += (offer.baseline_power_kw - offer.upward_capacity_kw) * hours
            downward_energy += (offer.baseline_power_kw + offer.downward_capacity_kw) * hours
            assert upward_energy >= resource.minimum_energy_kwh - 1e-9
            assert downward_energy <= resource.maximum_energy_kwh + 1e-9
            assert offer.upward_capacity_kw <= ramp_rate + 1e-9
            assert offer.downward_capacity_kw <= ramp_rate + 1e-9
