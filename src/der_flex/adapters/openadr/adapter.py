from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import timedelta

from der_flex.adapters.openadr.models import (
    Interval,
    IntervalPeriod,
    OpenADREvent,
    OpenADRReportRequest,
    ReportPayloadDescriptor,
    ReportResource,
    ValuesMap,
)
from der_flex.domain.models import Activation, FlexibilityDirection, Reservation
from der_flex.reservations import ReservationService

PAYLOAD_DIRECTIONS: dict[str, FlexibilityDirection] = {
    "DER_FLEX_UPWARD_KW": "UPWARD",
    "DER_FLEX_DOWNWARD_KW": "DOWNWARD",
}


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not match or not any(match.groups()):
        raise ValueError(f"unsupported ISO 8601 duration: {value}")
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    total_seconds = hours * 3600 + minutes * 60 + seconds
    if not 0 < total_seconds <= 86_400:
        raise ValueError("OpenADR interval duration must be between one second and one day")
    return timedelta(seconds=total_seconds)


@dataclass(frozen=True)
class OpenADREventResult:
    reservation: Reservation
    activation: Activation
    report: OpenADRReportRequest


class OpenADRAdapter:
    """Narrow OpenADR 3.1.0 VEN adapter for DER Flex dispatch events."""

    def __init__(self, reservations: ReservationService, client_name: str = "der-flex-ven") -> None:
        self.reservations = reservations
        self.client_name = client_name

    def handle_event(self, payload: dict[str, object]) -> list[OpenADREventResult]:
        event = OpenADREvent.model_validate(payload)
        results: list[OpenADREventResult] = []
        cursor = event.interval_period.start if event.interval_period else None
        default_duration = (
            parse_duration(event.interval_period.duration) if event.interval_period else None
        )

        for interval in event.intervals:
            period = interval.interval_period
            start = period.start if period else cursor
            duration = parse_duration(period.duration) if period else default_duration
            if start is None or duration is None:
                raise ValueError("each interval needs an effective start and duration")
            dispatches = [item for item in interval.payloads if item.type in PAYLOAD_DIRECTIONS]
            if len(dispatches) != 1 or len(dispatches[0].values) != 1:
                raise ValueError("interval must contain exactly one DER Flex dispatch value")
            dispatch = dispatches[0]
            power_kw = dispatch.values[0]
            if isinstance(power_kw, bool) or not isinstance(power_kw, (float, int)):
                raise ValueError("dispatch value must be numeric")
            if not math.isfinite(power_kw):
                raise ValueError("dispatch value must be finite")
            reservation = self.reservations.create(
                idempotency_key=f"openadr:{event.id}:{interval.id}",
                zone_id=event.zone_id,
                interval_start=start,
                interval_end=start + duration,
                direction=PAYLOAD_DIRECTIONS[dispatch.type],
                power_kw=float(power_kw),
            )
            activation = self.reservations.activate(reservation.reservation_id)
            report = self._build_report(event, interval.id, reservation, activation)
            results.append(OpenADREventResult(reservation, activation, report))
            cursor = start + duration
        return results

    def _build_report(
        self,
        event: OpenADREvent,
        interval_id: int,
        reservation: Reservation,
        activation: Activation,
    ) -> OpenADRReportRequest:
        duration = reservation.interval_end - reservation.interval_start
        iso_duration = f"PT{int(duration.total_seconds())}S"
        return OpenADRReportRequest(
            eventID=event.id,
            clientName=self.client_name,
            reportName=f"DER Flex activation {activation.activation_id}",
            payloadDescriptors=[
                ReportPayloadDescriptor(
                    objectType="REPORT_PAYLOAD_DESCRIPTOR",
                    payloadType="DER_FLEX_ACCEPTED_KW",
                    units="KW",
                    confidence=100 if activation.status == "COMPLETED" else 0,
                ),
                ReportPayloadDescriptor(
                    objectType="REPORT_PAYLOAD_DESCRIPTOR",
                    payloadType="DER_FLEX_ACTIVATION_STATUS",
                ),
            ],
            resources=[
                ReportResource(
                    resourceName=f"aggregate:{reservation.zone_id}",
                    intervalPeriod=IntervalPeriod(
                        start=reservation.interval_start,
                        duration=iso_duration,
                    ),
                    intervals=[
                        Interval(
                            id=interval_id,
                            payloads=[
                                ValuesMap(
                                    type="DER_FLEX_ACCEPTED_KW",
                                    values=[reservation.allocated_power_kw],
                                ),
                                ValuesMap(
                                    type="DER_FLEX_ACTIVATION_STATUS",
                                    values=[activation.status],
                                ),
                            ],
                        )
                    ],
                )
            ],
        )
