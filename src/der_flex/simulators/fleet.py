from __future__ import annotations

import uuid

from der_flex.simulators.battery import BatterySimulator
from der_flex.simulators.evse import EVSESimulator
from der_flex.simulators.heat_pump import HeatPumpSimulator

ZONES = ("ES-MA-29700", "ES-MA-29001", "ES-GR-18001")


def _resource_id(kind: str, zone: str, index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"der-flex:{kind}:{zone}:{index}"))


def build_demo_fleet(per_profile_per_zone: int = 4) -> list[BatterySimulator]:
    """Build a deterministic heterogeneous fleet across three zones."""

    if per_profile_per_zone < 1:
        raise ValueError("per_profile_per_zone must be positive")

    resources: list[BatterySimulator] = []
    for zone in ZONES:
        for index in range(per_profile_per_zone):
            resources.append(
                BatterySimulator(
                    resource_id=_resource_id("battery", zone, index),
                    zone_id=zone,
                    # At most 2.9 kW so the one-hour demo forecast remains below
                    # the battery's provisioned 90% operating SoC ceiling.
                    power_kw=1.5 + (index % 8) * 0.2,
                )
            )
            resources.append(
                EVSESimulator(
                    resource_id=_resource_id("evse", zone, index),
                    zone_id=zone,
                    power_kw=3.0 + (index % 12) * 0.3,
                )
            )
            resources.append(
                HeatPumpSimulator(
                    resource_id=_resource_id("heat-pump", zone, index),
                    zone_id=zone,
                    power_kw=0.8 + (index % 6) * 0.2,
                )
            )
    return resources
