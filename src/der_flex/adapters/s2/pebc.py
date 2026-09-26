from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from s2python.common import CommodityQuantity, PowerForecast
from s2python.pebc import (
    PEBCPowerConstraints,
    PEBCPowerEnvelopeLimitType,
)
from s2python.s2_parser import S2Parser
from s2python.s2_validation_error import S2ValidationError

from der_flex.domain.models import FlexibilityOffer
from der_flex.domain.resources import ResourceRegistry

SUPPORTED_QUANTITY = CommodityQuantity.ELECTRIC_POWER_L1
ENERGY_TOLERANCE_KWH = 1e-9


class OfferClockSkewError(ValueError):
    """The source observation falls outside the accepted local clock window."""


@dataclass(frozen=True)
class _Segment:
    start: datetime
    end: datetime
    baseline_w: float
    upward_w: float
    downward_w: float
    confidence: float


def _parse(message: dict[str, Any], expected: type[Any]) -> Any:
    try:
        parsed = S2Parser().parse_as_any_message(json.dumps(message))
    except S2ValidationError as error:
        raise ValueError("invalid S2 message") from error
    if not isinstance(parsed, expected):
        raise ValueError(f"expected {expected.__name__}, received {type(parsed).__name__}")
    return parsed


def _confidence(power_value: Any) -> float:
    expected = power_value.value_expected
    lower = power_value.value_lower_95PPR
    upper = power_value.value_upper_95PPR
    if lower is None or upper is None:
        return 0.5
    scale = max(abs(expected) * 2, 1000.0)
    return float(max(0.0, min(1.0, 1.0 - ((upper - lower) / scale))))


def normalize_pebc_offer(
    *,
    resource_id: str,
    zone_id: str,
    constraints_message: dict[str, Any],
    forecast_message: dict[str, Any],
    resource_registry: ResourceRegistry,
    source_epoch: int,
    source_sequence: int,
    observed_at: datetime,
    received_at: datetime,
    resolution: timedelta = timedelta(minutes=15),
    activation_response_time: timedelta = timedelta(minutes=1),
    maximum_observation_age: timedelta = timedelta(minutes=15),
    maximum_future_skew: timedelta = timedelta(minutes=2),
) -> list[FlexibilityOffer]:
    """Translate S2 data within provisioned power, energy and ramp envelopes.

    Ramp capability is the provisioned rate multiplied by ``activation_response_time``;
    the default contract assumes that the requested adjustment must be reached in one
    minute. Energy budgets are shared conservatively across the complete forecast.
    """

    provisioned = resource_registry.require(resource_id, zone_id)
    if observed_at.tzinfo is None or received_at.tzinfo is None:
        raise ValueError("observation and receipt timestamps must be timezone-aware")
    if maximum_observation_age.total_seconds() < 0 or maximum_future_skew.total_seconds() < 0:
        raise ValueError("clock tolerances cannot be negative")
    observed_at = observed_at.astimezone(UTC)
    received_at = received_at.astimezone(UTC)
    if observed_at < received_at - maximum_observation_age:
        raise OfferClockSkewError("source observation is too old")
    if observed_at > received_at + maximum_future_skew:
        raise OfferClockSkewError("source observation is too far in the future")
    physical_min_power_kw = provisioned.min_power_kw
    physical_max_power_kw = provisioned.max_power_kw
    physical_min_energy_kwh = provisioned.min_energy_kwh
    physical_max_energy_kwh = provisioned.max_energy_kwh
    physical_initial_energy_kwh = provisioned.initial_energy_kwh
    physical_ramp_rate_kw_per_min = provisioned.ramp_rate_kw_per_min

    if (
        not math.isfinite(physical_min_power_kw)
        or not math.isfinite(physical_max_power_kw)
        or physical_min_power_kw > physical_max_power_kw
    ):
        raise ValueError("physical power envelope is invalid")
    if (
        not math.isfinite(physical_min_energy_kwh)
        or not math.isfinite(physical_max_energy_kwh)
        or not math.isfinite(physical_initial_energy_kwh)
        or not physical_min_energy_kwh <= physical_initial_energy_kwh <= physical_max_energy_kwh
    ):
        raise ValueError("physical energy envelope is invalid")
    if (
        not math.isfinite(physical_ramp_rate_kw_per_min)
        or physical_ramp_rate_kw_per_min < 0
        or activation_response_time.total_seconds() <= 0
    ):
        raise ValueError("physical ramp envelope is invalid")

    constraints: PEBCPowerConstraints = _parse(constraints_message, PEBCPowerConstraints)
    forecast: PowerForecast = _parse(forecast_message, PowerForecast)

    upper_ranges = [
        item.range_boundary
        for item in constraints.allowed_limit_ranges
        if item.commodity_quantity == SUPPORTED_QUANTITY
        and item.limit_type == PEBCPowerEnvelopeLimitType.UPPER_LIMIT
        and not item.abnormal_condition_only
    ]
    lower_ranges = [
        item.range_boundary
        for item in constraints.allowed_limit_ranges
        if item.commodity_quantity == SUPPORTED_QUANTITY
        and item.limit_type == PEBCPowerEnvelopeLimitType.LOWER_LIMIT
        and not item.abnormal_condition_only
    ]
    if not upper_ranges or not lower_ranges or constraints.valid_until is None:
        return []
    range_values = [
        value
        for item in (*upper_ranges, *lower_ranges)
        for value in (item.start_of_range, item.end_of_range)
    ]
    if not all(math.isfinite(value) for value in range_values):
        raise ValueError("S2 power constraints must be finite")
    if constraints.valid_until - constraints.valid_from > timedelta(days=1):
        raise ValueError("S2 constraint horizon cannot exceed one day")

    minimum_upper_w = max(
        min(item.start_of_range for item in upper_ranges), physical_min_power_kw * 1000
    )
    maximum_lower_w = min(
        max(item.end_of_range for item in lower_ranges), physical_max_power_kw * 1000
    )
    if minimum_upper_w > maximum_lower_w:
        raise ValueError("S2 constraints do not overlap the physical power envelope")
    if resolution.total_seconds() <= 0:
        raise ValueError("resolution must be positive")

    cursor = forecast.start_time.astimezone(UTC)
    raw_segments: list[_Segment] = []
    maximum_ramp_adjustment_w = (
        physical_ramp_rate_kw_per_min * (activation_response_time.total_seconds() / 60) * 1000
    )

    for element in forecast.elements:
        duration = timedelta(milliseconds=element.duration.root)
        interval_end = cursor + duration
        values = [
            value
            for value in element.power_values
            if value.commodity_quantity == SUPPORTED_QUANTITY
        ]
        if (
            len(values) == 1
            and cursor >= constraints.valid_from
            and interval_end <= constraints.valid_until
        ):
            power_value = values[0]
            baseline_w = power_value.value_expected
            uncertainty_values = (
                power_value.value_lower_95PPR,
                power_value.value_upper_95PPR,
            )
            if not math.isfinite(baseline_w) or any(
                value is not None and not math.isfinite(value) for value in uncertainty_values
            ):
                raise ValueError("S2 forecast power values must be finite")
            if not minimum_upper_w <= baseline_w <= maximum_lower_w:
                raise ValueError("forecast baseline is outside the physical power envelope")
            upward_w = min(max(0.0, baseline_w - minimum_upper_w), maximum_ramp_adjustment_w)
            downward_w = min(max(0.0, maximum_lower_w - baseline_w), maximum_ramp_adjustment_w)
            raw_segments.append(
                _Segment(
                    start=cursor,
                    end=interval_end,
                    baseline_w=baseline_w,
                    upward_w=upward_w,
                    downward_w=downward_w,
                    confidence=_confidence(power_value),
                )
            )
        cursor = interval_end

    if not raw_segments:
        return []

    baseline_energy_kwh = physical_initial_energy_kwh
    baseline_end_energy: list[float] = []
    for segment in raw_segments:
        hours = (segment.end - segment.start).total_seconds() / 3600
        baseline_energy_kwh += segment.baseline_w / 1000 * hours
        if (
            baseline_energy_kwh < physical_min_energy_kwh - ENERGY_TOLERANCE_KWH
            or baseline_energy_kwh > physical_max_energy_kwh + ENERGY_TOLERANCE_KWH
        ):
            raise ValueError("forecast baseline is outside the physical energy envelope")
        baseline_energy_kwh = max(
            physical_min_energy_kwh,
            min(physical_max_energy_kwh, baseline_energy_kwh),
        )
        baseline_end_energy.append(baseline_energy_kwh)

    future_upward_budget = [0.0] * len(raw_segments)
    future_downward_budget = [0.0] * len(raw_segments)
    upward_headroom = math.inf
    downward_headroom = math.inf
    for index in range(len(raw_segments) - 1, -1, -1):
        upward_headroom = min(
            upward_headroom,
            baseline_end_energy[index] - physical_min_energy_kwh,
        )
        downward_headroom = min(
            downward_headroom,
            physical_max_energy_kwh - baseline_end_energy[index],
        )
        future_upward_budget[index] = max(0.0, upward_headroom)
        future_downward_budget[index] = max(0.0, downward_headroom)

    segments: list[_Segment] = []
    allocated_upward_energy = 0.0
    allocated_downward_energy = 0.0
    for index, segment in enumerate(raw_segments):
        hours = (segment.end - segment.start).total_seconds() / 3600
        upward_energy_kwh = min(
            segment.upward_w / 1000 * hours,
            max(0.0, future_upward_budget[index] - allocated_upward_energy),
        )
        downward_energy_kwh = min(
            segment.downward_w / 1000 * hours,
            max(0.0, future_downward_budget[index] - allocated_downward_energy),
        )
        allocated_upward_energy += upward_energy_kwh
        allocated_downward_energy += downward_energy_kwh
        segments.append(
            _Segment(
                start=segment.start,
                end=segment.end,
                baseline_w=segment.baseline_w,
                upward_w=upward_energy_kwh / hours * 1000,
                downward_w=downward_energy_kwh / hours * 1000,
                confidence=segment.confidence,
            )
        )

    resolution_seconds = resolution.total_seconds()
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    first_offset = (segments[0].start - epoch).total_seconds()
    bucket_start = epoch + timedelta(
        seconds=math.ceil(first_offset / resolution_seconds) * resolution_seconds
    )
    final_end = min(segments[-1].end, constraints.valid_until.astimezone(UTC))
    offers: list[FlexibilityOffer] = []

    while bucket_start + resolution <= final_end:
        bucket_end = bucket_start + resolution
        overlaps = [
            segment
            for segment in segments
            if segment.start < bucket_end and segment.end > bucket_start
        ]
        covered_seconds = sum(
            (min(segment.end, bucket_end) - max(segment.start, bucket_start)).total_seconds()
            for segment in overlaps
        )
        if overlaps and math.isclose(covered_seconds, resolution_seconds):
            baseline_w = (
                sum(
                    segment.baseline_w
                    * (
                        min(segment.end, bucket_end) - max(segment.start, bucket_start)
                    ).total_seconds()
                    for segment in overlaps
                )
                / resolution_seconds
            )
            upward_w = min(segment.upward_w for segment in overlaps)
            downward_w = min(segment.downward_w for segment in overlaps)
            upward_energy_kwh = sum(
                segment.upward_w
                / 1000
                * (min(segment.end, bucket_end) - max(segment.start, bucket_start)).total_seconds()
                / 3600
                for segment in overlaps
            )
            downward_energy_kwh = sum(
                segment.downward_w
                / 1000
                * (min(segment.end, bucket_end) - max(segment.start, bucket_start)).total_seconds()
                / 3600
                for segment in overlaps
            )
            offers.append(
                FlexibilityOffer(
                    resource_id=resource_id,
                    zone_id=zone_id,
                    interval_start=bucket_start,
                    interval_end=bucket_end,
                    baseline_power_kw=baseline_w / 1000,
                    upward_capacity_kw=upward_w / 1000,
                    downward_capacity_kw=downward_w / 1000,
                    upward_energy_kwh=upward_energy_kwh,
                    downward_energy_kwh=downward_energy_kwh,
                    confidence=min(segment.confidence for segment in overlaps),
                    consequence_type=constraints.consequence_type.value,
                    source_version=str(constraints.id),
                    source_epoch=source_epoch,
                    source_sequence=source_sequence,
                    observed_at=observed_at,
                    expires_at=constraints.valid_until,
                )
            )
        bucket_start = bucket_end

    return offers
