from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime
from threading import RLock
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResourceRegistryError(ValueError):
    """Base error for failures at the provisioning trust boundary."""


class UnknownResourceError(ResourceRegistryError):
    pass


class DisabledResourceError(ResourceRegistryError):
    pass


class ResourceZoneMismatch(ResourceRegistryError):
    pass


class ResourceProvisioningConflict(ResourceRegistryError):
    pass


class StaleProvisioningRecord(ResourceRegistryError):
    pass


class ProvisionedResource(BaseModel):
    """Immutable operator-owned physical envelope for one DER."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    resource_id: str = Field(min_length=1, max_length=128)
    zone_id: str = Field(min_length=1, max_length=128)
    resource_type: Literal["BATTERY", "EVSE", "HEAT_PUMP"]
    min_power_kw: float = Field(ge=-100_000, le=100_000)
    max_power_kw: float = Field(ge=-100_000, le=100_000)
    min_energy_kwh: float = Field(ge=0, le=1_000_000_000)
    max_energy_kwh: float = Field(ge=0, le=1_000_000_000)
    initial_energy_kwh: float = Field(ge=0, le=1_000_000_000)
    ramp_rate_kw_per_min: float = Field(ge=0, le=100_000)
    provisioning_version: int = Field(ge=1)
    provisioned_at: datetime
    enabled: bool = True
    tenant_id: str = "development"

    @model_validator(mode="after")
    def validate_envelope(self) -> ProvisionedResource:
        quantities = (
            self.min_power_kw,
            self.max_power_kw,
            self.min_energy_kwh,
            self.max_energy_kwh,
            self.initial_energy_kwh,
            self.ramp_rate_kw_per_min,
        )
        if not all(math.isfinite(value) for value in quantities):
            raise ValueError("physical envelope values must be finite")
        if self.min_power_kw > self.max_power_kw:
            raise ValueError("physical power envelope is invalid")
        if not self.min_energy_kwh <= self.initial_energy_kwh <= self.max_energy_kwh:
            raise ValueError("physical energy envelope is invalid")
        if self.provisioned_at.tzinfo is None:
            raise ValueError("provisioned_at must be timezone-aware")
        return self


class ResourceRegistry(Protocol):
    def require(
        self, resource_id: str, zone_id: str, tenant_id: str = "development"
    ) -> ProvisionedResource: ...


class InMemoryResourceRegistry:
    """Thread-safe local registry; production storage remains a later milestone."""

    def __init__(self, records: Iterable[ProvisionedResource] = ()) -> None:
        self._records: dict[str, ProvisionedResource] = {}
        self._lock = RLock()
        for record in records:
            self.register(record)

    def register(self, record: ProvisionedResource) -> None:
        with self._lock:
            current = self._records.get(record.resource_id)
            if current is not None:
                if record.provisioning_version < current.provisioning_version:
                    raise StaleProvisioningRecord("provisioning version is older than current")
                if record.provisioning_version == current.provisioning_version:
                    if record == current:
                        return
                    raise ResourceProvisioningConflict(
                        "same provisioning version has conflicting content"
                    )
            self._records[record.resource_id] = record

    def require(
        self, resource_id: str, zone_id: str, tenant_id: str = "development"
    ) -> ProvisionedResource:
        with self._lock:
            record = self._records.get(resource_id)
            if record is None:
                raise UnknownResourceError(f"resource {resource_id!r} is not provisioned")
            if not record.enabled:
                raise DisabledResourceError(f"resource {resource_id!r} is disabled")
            if record.tenant_id != tenant_id:
                raise UnknownResourceError(f"resource {resource_id!r} is not provisioned")
            if record.zone_id != zone_id:
                raise ResourceZoneMismatch(
                    f"resource {resource_id!r} is not provisioned for zone {zone_id!r}"
                )
            return record
