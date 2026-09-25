from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from der_flex.simulators.battery import BatterySimulator
from der_flex.simulators.evse import EVSESimulator
from der_flex.simulators.fleet import build_demo_fleet
from der_flex.simulators.heat_pump import HeatPumpSimulator

ScenarioName = Literal[
    "normal_afternoon",
    "demand_peak",
    "ev_departure",
    "connectivity_loss",
]


@dataclass(frozen=True)
class Scenario:
    name: ScenarioName
    connected_resources: list[BatterySimulator]
    disconnected_resource_ids: tuple[str, ...] = ()


def build_scenario(name: ScenarioName) -> Scenario:
    fleet = build_demo_fleet()

    if name == "normal_afternoon":
        return Scenario(name=name, connected_resources=fleet)

    if name == "demand_peak":
        for resource in fleet:
            if isinstance(resource, EVSESimulator):
                resource.set_power(7.0)
            elif isinstance(resource, HeatPumpSimulator):
                resource.set_power(1.8)
            else:
                resource.set_power(4.0)
        return Scenario(name=name, connected_resources=fleet)

    if name == "ev_departure":
        disconnected = tuple(
            resource.resource_id for resource in fleet if isinstance(resource, EVSESimulator)
        )
        connected = [resource for resource in fleet if resource.resource_id not in disconnected]
        return Scenario(
            name=name, connected_resources=connected, disconnected_resource_ids=disconnected
        )

    if name == "connectivity_loss":
        disconnected = tuple(resource.resource_id for resource in fleet[::4])
        connected = [resource for resource in fleet if resource.resource_id not in disconnected]
        return Scenario(
            name=name, connected_resources=connected, disconnected_resource_ids=disconnected
        )

    raise ValueError(f"unknown scenario: {name}")
