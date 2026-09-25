from __future__ import annotations

from datetime import datetime

from der_flex.domain.models import ConsequenceType, FlexibilityDirection


def product_lock_key(
    zone_id: str,
    interval_start: datetime,
    interval_end: datetime,
    consequence_type: ConsequenceType,
    direction: FlexibilityDirection,
) -> str:
    """Stable cross-module lock identity for one reservable product."""

    return (
        f"product:{zone_id}:{interval_start.isoformat()}:{interval_end.isoformat()}:"
        f"{consequence_type}:{direction}"
    )
