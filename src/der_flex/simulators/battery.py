from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass
class BatterySimulator:
    """Deterministic battery profile used by the first vertical slice."""

    resource_id: str = "10000000-0000-4000-8000-000000000100"
    zone_id: str = "ES-MA-29700"
    capacity_kwh: float = 10.0
    state_of_charge: float = 0.60
    power_kw: float = 2.0
    max_charge_kw: float = 5.0
    max_discharge_kw: float = 5.0
    min_state_of_charge: float = 0.10
    max_state_of_charge: float = 0.90
    max_ramp_kw_per_min: float = 10.0
    consequence_type: str = "DEFER"
    source_epoch: int = field(default=0, init=False)
    source_sequence: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.capacity_kwh <= 0:
            raise ValueError("battery capacity must be positive")
        if not 0 <= self.state_of_charge <= 1:
            raise ValueError("state of charge must be between zero and one")
        if not 0 <= self.min_state_of_charge < self.max_state_of_charge <= 1:
            raise ValueError("operating state-of-charge limits are invalid")
        if not self.min_state_of_charge <= self.state_of_charge <= self.max_state_of_charge:
            raise ValueError("state of charge is outside operating limits")
        if self.max_charge_kw < 0 or self.max_discharge_kw < 0:
            raise ValueError("physical power limits cannot be negative")
        if self.max_ramp_kw_per_min < 0:
            raise ValueError("physical ramp rate cannot be negative")
        if not -self.max_discharge_kw <= self.power_kw <= self.max_charge_kw:
            raise ValueError("initial power is outside physical limits")

    def set_power(self, power_kw: float) -> None:
        if not -self.max_discharge_kw <= power_kw <= self.max_charge_kw:
            raise ValueError("power is outside battery limits")
        if power_kw != self.power_kw:
            self.source_sequence += 1
        self.power_kw = power_kw

    def restart_session(self, *, power_kw: float | None = None) -> None:
        if power_kw is not None and not -self.max_discharge_kw <= power_kw <= self.max_charge_kw:
            raise ValueError("power is outside battery limits")
        self.source_epoch += 1
        self.source_sequence = 0
        if power_kw is not None:
            self.power_kw = power_kw

    @property
    def stored_energy_kwh(self) -> float:
        return self.state_of_charge * self.capacity_kwh

    @property
    def minimum_energy_kwh(self) -> float:
        return self.min_state_of_charge * self.capacity_kwh

    @property
    def maximum_energy_kwh(self) -> float:
        return self.max_state_of_charge * self.capacity_kwh

    def advance(self, minutes: int = 15) -> None:
        energy_delta = self.power_kw * (minutes / 60)
        next_soc = self.state_of_charge + energy_delta / self.capacity_kwh
        bounded_soc = max(
            self.min_state_of_charge, min(self.max_state_of_charge, next_soc)
        )
        if bounded_soc != self.state_of_charge:
            self.source_sequence += 1
        self.state_of_charge = bounded_soc

    def s2_offer_messages(
        self, start: datetime | None = None, *, interval_count: int = 1
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if interval_count < 1 or interval_count > 96:
            raise ValueError("interval_count must be between 1 and 96")
        if start is None:
            start = datetime.now(UTC).replace(second=0, microsecond=0)
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        end = start + timedelta(minutes=15 * interval_count)
        constraint_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{self.resource_id}:{start.isoformat()}:{self.source_epoch}:"
                f"{self.source_sequence}",
            )
        )
        charge_limit = (
            self.max_charge_kw if self.state_of_charge < self.max_state_of_charge else 0.0
        )
        discharge_limit = (
            self.max_discharge_kw
            if self.state_of_charge > self.min_state_of_charge
            else 0.0
        )
        boundary = {
            "start_of_range": -discharge_limit * 1000,
            "end_of_range": charge_limit * 1000,
        }
        constraints: dict[str, Any] = {
            "message_type": "PEBC.PowerConstraints",
            "message_id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"constraint-message:{constraint_id}")
            ),
            "id": constraint_id,
            "valid_from": start.isoformat().replace("+00:00", "Z"),
            "valid_until": end.isoformat().replace("+00:00", "Z"),
            "consequence_type": self.consequence_type,
            "allowed_limit_ranges": [
                {
                    "commodity_quantity": "ELECTRIC.POWER.L1",
                    "limit_type": limit_type,
                    "range_boundary": deepcopy(boundary),
                    "abnormal_condition_only": False,
                }
                for limit_type in ("UPPER_LIMIT", "LOWER_LIMIT")
            ],
        }
        forecast_elements: list[dict[str, Any]] = []
        forecast_energy_kwh = self.stored_energy_kwh
        interval_hours = 0.25
        for _ in range(interval_count):
            minimum_power_kw = max(
                -self.max_discharge_kw,
                (self.minimum_energy_kwh - forecast_energy_kwh) / interval_hours,
            )
            maximum_power_kw = min(
                self.max_charge_kw,
                (self.maximum_energy_kwh - forecast_energy_kwh) / interval_hours,
            )
            forecast_power_kw = max(
                minimum_power_kw, min(maximum_power_kw, self.power_kw)
            )
            forecast_elements.append(
                {
                    "duration": 900_000,
                    "power_values": [
                        {
                            "value_expected": forecast_power_kw * 1000,
                            "value_lower_95PPR": (forecast_power_kw - 0.2) * 1000,
                            "value_upper_95PPR": (forecast_power_kw + 0.2) * 1000,
                            "commodity_quantity": "ELECTRIC.POWER.L1",
                        }
                    ],
                }
            )
            forecast_energy_kwh += forecast_power_kw * interval_hours
        forecast: dict[str, Any] = {
            "message_type": "PowerForecast",
            "message_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"forecast:{constraint_id}")),
            "start_time": constraints["valid_from"],
            "elements": forecast_elements,
        }
        return constraints, forecast
