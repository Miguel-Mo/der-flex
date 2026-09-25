from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from der_flex.domain import InMemoryResourceRegistry, ProvisionedResource
from der_flex.simulators.battery import BatterySimulator
from der_flex.simulators.evse import EVSESimulator
from der_flex.simulators.heat_pump import HeatPumpSimulator

DEMO_PROVISIONED_AT = datetime(2026, 9, 12, tzinfo=UTC)


def build_simulator_registry(
    resources: Iterable[BatterySimulator],
) -> InMemoryResourceRegistry:
    """Provision demo/test simulators; real deployments must use operator-owned data."""

    records: list[ProvisionedResource] = []
    for resource in resources:
        resource_type: Literal["BATTERY", "EVSE", "HEAT_PUMP"]
        if isinstance(resource, EVSESimulator):
            resource_type = "EVSE"
        elif isinstance(resource, HeatPumpSimulator):
            resource_type = "HEAT_PUMP"
        else:
            resource_type = "BATTERY"
        records.append(
            ProvisionedResource(
                resource_id=resource.resource_id,
                zone_id=resource.zone_id,
                resource_type=resource_type,
                min_power_kw=-resource.max_discharge_kw,
                max_power_kw=resource.max_charge_kw,
                min_energy_kwh=resource.minimum_energy_kwh,
                max_energy_kwh=resource.maximum_energy_kwh,
                initial_energy_kwh=resource.stored_energy_kwh,
                ramp_rate_kw_per_min=resource.max_ramp_kw_per_min,
                provisioning_version=1,
                provisioned_at=DEMO_PROVISIONED_AT,
            )
        )
    return InMemoryResourceRegistry(records)
