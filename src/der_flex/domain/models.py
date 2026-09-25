from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

type ConsequenceType = Literal["VANISH", "DEFER"]
type FlexibilityDirection = Literal["UPWARD", "DOWNWARD"]
type ReservationStatus = Literal[
    "CONFIRMED", "ACTIVATED", "COMPLETED", "CANCELLED", "EXPIRED", "FAILED"
]


class FlexibilityOffer(BaseModel):
    """Private, resource-level flexibility for one interval."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    resource_id: str
    zone_id: str
    interval_start: datetime
    interval_end: datetime
    baseline_power_kw: float
    upward_capacity_kw: float = Field(ge=0)
    downward_capacity_kw: float = Field(ge=0)
    upward_energy_kwh: float = Field(ge=0)
    downward_energy_kwh: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    product_class: Literal["BEST_EFFORT_PEBC"] = "BEST_EFFORT_PEBC"
    consequence_type: ConsequenceType
    source_version: str
    source_epoch: int = Field(ge=0)
    source_sequence: int = Field(ge=0)
    observed_at: datetime
    expires_at: datetime
    tenant_id: str = "development"

    @model_validator(mode="after")
    def validate_times(self) -> FlexibilityOffer:
        if (
            self.interval_start.tzinfo is None
            or self.interval_end.tzinfo is None
            or self.observed_at.tzinfo is None
        ):
            raise ValueError("offer timestamps must be timezone-aware")
        if self.interval_end <= self.interval_start:
            raise ValueError("interval_end must be after interval_start")
        if self.expires_at < self.interval_end:
            raise ValueError("offer cannot expire before its interval ends")
        return self


class FlexibilityAggregate(BaseModel):
    """Public aggregate with no resource-level identifier."""

    model_config = ConfigDict(allow_inf_nan=False)

    schema_version: Literal["1.0"] = "1.0"
    zone_id: str
    interval_start: datetime
    interval_end: datetime
    baseline_power_kw: float
    upward_capacity_kw: float = Field(ge=0)
    downward_capacity_kw: float = Field(ge=0)
    upward_energy_kwh: float = Field(ge=0)
    downward_energy_kwh: float = Field(ge=0)
    participant_count: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)
    product_class: Literal["BEST_EFFORT_PEBC"] = "BEST_EFFORT_PEBC"
    consequence_type: ConsequenceType
    generated_at: datetime
    tenant_id: str = Field(default="development", exclude=True)


class Reservation(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    reservation_id: UUID
    correlation_id: UUID
    zone_id: str
    interval_start: datetime
    interval_end: datetime
    direction: FlexibilityDirection
    requested_power_kw: float = Field(gt=0)
    allocated_power_kw: float = Field(ge=0)
    participant_count: int = Field(ge=1)
    consequence_type: ConsequenceType
    product_class: Literal["BEST_EFFORT_PEBC"] = "BEST_EFFORT_PEBC"
    status: ReservationStatus
    created_at: datetime
    expires_at: datetime
    tenant_id: str = Field(default="development", exclude=True)


class Activation(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    activation_id: UUID
    reservation_id: UUID
    correlation_id: UUID
    status: Literal["PENDING", "COMPLETED", "FAILED"]
    instruction_count: int = Field(ge=0)
    accepted_instruction_count: int = Field(ge=0)
    rejected_instruction_count: int = Field(ge=0)
    created_at: datetime
    completed_at: datetime | None = None
