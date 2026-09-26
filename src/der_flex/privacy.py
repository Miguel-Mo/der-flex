"""Conservative publication controls for aggregate flexibility."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from der_flex.domain.models import FlexibilityAggregate


class PrivacyQueryRejected(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PrivacyQueryBudgetExceeded(Exception):
    pass


@dataclass(frozen=True)
class PrivacyPublicationPolicy:
    """Fixed-cadence, catalogued-window publication policy.

    Quantisation reduces precision but is not presented as differential privacy.
    Query budgets are tenant-wide so additional identities do not create new budgets.
    """

    interval_minutes: int = 15
    minimum_participants: int = 10
    max_window_hours: int = 24
    queries_per_interval: int = 60
    power_resolution_kw: float = 1.0
    energy_resolution_kwh: float = 0.25
    confidence_resolution: float = 0.05
    participant_bucket_size: int = 5

    def __post_init__(self) -> None:
        values = (
            self.interval_minutes,
            self.minimum_participants,
            self.max_window_hours,
            self.queries_per_interval,
            self.power_resolution_kw,
            self.energy_resolution_kwh,
            self.confidence_resolution,
            self.participant_bucket_size,
        )
        if any(value <= 0 for value in values):
            raise ValueError("privacy publication parameters must be positive")
        if self.minimum_participants < 10:
            raise ValueError("public privacy threshold cannot be lower than 10")

    def publication_epoch(self, now: datetime | None = None) -> datetime:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("publication time must be timezone-aware")
        utc = current.astimezone(UTC)
        minute = utc.minute - utc.minute % self.interval_minutes
        return utc.replace(minute=minute, second=0, microsecond=0)

    def validate_window(self, start: datetime, end: datetime) -> None:
        if start.tzinfo is None or end.tzinfo is None:
            raise PrivacyQueryRejected("timezone_required")
        if end <= start:
            raise PrivacyQueryRejected("invalid_window")
        if end - start > timedelta(hours=self.max_window_hours):
            raise PrivacyQueryRejected("window_too_large")
        if not self._aligned(start) or not self._aligned(end):
            raise PrivacyQueryRejected("window_not_aligned")

    def sanitize(self, aggregate: FlexibilityAggregate, epoch: datetime) -> FlexibilityAggregate:
        participant_count = (
            aggregate.participant_count // self.participant_bucket_size
        ) * self.participant_bucket_size
        return aggregate.model_copy(
            update={
                "baseline_power_kw": self._nearest(
                    aggregate.baseline_power_kw, self.power_resolution_kw
                ),
                "upward_capacity_kw": self._floor(
                    aggregate.upward_capacity_kw, self.power_resolution_kw
                ),
                "downward_capacity_kw": self._floor(
                    aggregate.downward_capacity_kw, self.power_resolution_kw
                ),
                "upward_energy_kwh": self._floor(
                    aggregate.upward_energy_kwh, self.energy_resolution_kwh
                ),
                "downward_energy_kwh": self._floor(
                    aggregate.downward_energy_kwh, self.energy_resolution_kwh
                ),
                "participant_count": max(1, participant_count),
                "confidence": self._floor(aggregate.confidence, self.confidence_resolution),
                "generated_at": epoch,
            }
        )

    def eligible(self, aggregate: FlexibilityAggregate) -> bool:
        return aggregate.participant_count >= self.minimum_participants

    def _aligned(self, value: datetime) -> bool:
        utc = value.astimezone(UTC)
        return utc.minute % self.interval_minutes == 0 and utc.second == 0 and utc.microsecond == 0

    @staticmethod
    def _floor(value: float, resolution: float) -> float:
        return round(math.floor((value + 1e-12) / resolution) * resolution, 9)

    @staticmethod
    def _nearest(value: float, resolution: float) -> float:
        return round(round(value / resolution) * resolution, 9)
