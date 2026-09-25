from __future__ import annotations

from der_flex.simulators.battery import BatterySimulator


class EVSESimulator(BatterySimulator):
    """Unidirectional home charger whose charging may be deferred."""

    def __init__(
        self,
        *,
        resource_id: str = "50000000-0000-4000-8000-000000000100",
        zone_id: str = "ES-MA-29700",
        power_kw: float = 3.7,
    ) -> None:
        super().__init__(
            resource_id=resource_id,
            zone_id=zone_id,
            capacity_kwh=60.0,
            state_of_charge=0.45,
            power_kw=power_kw,
            max_charge_kw=7.4,
            max_discharge_kw=0.0,
            consequence_type="DEFER",
        )
