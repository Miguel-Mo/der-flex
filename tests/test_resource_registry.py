from copy import deepcopy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.domain import (
    DisabledResourceError,
    FlexibilityOffer,
    InMemoryResourceRegistry,
    ProvisionedResource,
    ResourceProvisioningConflict,
    ResourceZoneMismatch,
    StaleProvisioningRecord,
    UnknownResourceError,
)
from der_flex.simulators import BatterySimulator

START = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)


def provisioned_battery(
    battery: BatterySimulator, *, version: int = 1, enabled: bool = True
) -> ProvisionedResource:
    return ProvisionedResource(
        resource_id=battery.resource_id,
        zone_id=battery.zone_id,
        resource_type="BATTERY",
        min_power_kw=-1.0,
        max_power_kw=1.0,
        min_energy_kwh=1.0,
        max_energy_kwh=9.0,
        initial_energy_kwh=5.0,
        ramp_rate_kw_per_min=10.0,
        provisioning_version=version,
        provisioned_at=START,
        enabled=enabled,
    )


def normalize(
    battery: BatterySimulator, registry: InMemoryResourceRegistry
) -> list[FlexibilityOffer]:
    constraints, forecast = battery.s2_offer_messages(START)
    return normalize_pebc_offer(
        resource_id=battery.resource_id,
        zone_id=battery.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=registry,
        source_epoch=battery.source_epoch,
        source_sequence=battery.source_sequence,
        observed_at=START,
        received_at=START,
    )


def test_unknown_disabled_and_wrong_zone_resources_are_rejected() -> None:
    battery = BatterySimulator(power_kw=0.0)

    with pytest.raises(UnknownResourceError):
        normalize(battery, InMemoryResourceRegistry())
    with pytest.raises(DisabledResourceError):
        normalize(
            battery,
            InMemoryResourceRegistry([provisioned_battery(battery, enabled=False)]),
        )

    wrong_zone = provisioned_battery(battery).model_copy(update={"zone_id": "ES-OTHER"})
    with pytest.raises(ResourceZoneMismatch):
        normalize(battery, InMemoryResourceRegistry([wrong_zone]))


def test_forged_s2_limits_cannot_expand_operator_owned_envelope() -> None:
    battery = BatterySimulator(power_kw=0.0)
    constraints, forecast = battery.s2_offer_messages(START)
    forged_constraints = deepcopy(constraints)
    for limit in forged_constraints["allowed_limit_ranges"]:
        limit["range_boundary"] = {
            "start_of_range": -1_000_000.0,
            "end_of_range": 1_000_000.0,
        }

    offer = normalize_pebc_offer(
        resource_id=battery.resource_id,
        zone_id=battery.zone_id,
        constraints_message=forged_constraints,
        forecast_message=forecast,
        resource_registry=InMemoryResourceRegistry([provisioned_battery(battery)]),
        source_epoch=battery.source_epoch,
        source_sequence=battery.source_sequence,
        observed_at=START,
        received_at=START,
    )[0]

    assert offer.upward_capacity_kw == 1.0
    assert offer.downward_capacity_kw == 1.0


def test_registry_rejects_stale_and_conflicting_versions() -> None:
    battery = BatterySimulator()
    original = provisioned_battery(battery, version=2)
    registry = InMemoryResourceRegistry([original])

    registry.register(original)
    with pytest.raises(StaleProvisioningRecord):
        registry.register(original.model_copy(update={"provisioning_version": 1}))
    with pytest.raises(ResourceProvisioningConflict):
        registry.register(original.model_copy(update={"max_power_kw": 2.0}))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_registry_rejects_non_finite_physical_values(value: float) -> None:
    battery = BatterySimulator()
    data = provisioned_battery(battery).model_dump()
    data["max_power_kw"] = value

    with pytest.raises(ValidationError, match="finite number"):
        ProvisionedResource.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_energy_kwh", -0.001),
        ("max_energy_kwh", 1_000_000_001.0),
        ("max_power_kw", 100_000.01),
        ("ramp_rate_kw_per_min", 100_000.01),
    ],
)
def test_registry_rejects_values_outside_operational_ceiling(
    field: str, value: float
) -> None:
    battery = BatterySimulator()
    data = provisioned_battery(battery).model_dump()
    data[field] = value

    with pytest.raises(ValidationError):
        ProvisionedResource.model_validate(data)
