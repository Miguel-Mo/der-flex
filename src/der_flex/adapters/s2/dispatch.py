from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from s2python.pebc import PEBCInstruction
from s2python.s2_parser import S2Parser

from der_flex.domain.models import Reservation

if TYPE_CHECKING:
    from der_flex.reservations.service import Allocation


def build_pebc_instruction(
    *, allocation: Allocation, reservation: Reservation
) -> dict[str, object]:
    target_kw = (
        allocation.baseline_power_kw - allocation.power_kw
        if reservation.direction == "UPWARD"
        else allocation.baseline_power_kw + allocation.power_kw
    )
    duration_ms = int(
        (reservation.interval_end - reservation.interval_start).total_seconds() * 1000
    )
    payload: dict[str, object] = {
        "message_type": "PEBC.Instruction",
        "message_id": str(uuid.uuid4()),
        "id": str(uuid.uuid4()),
        "execution_time": reservation.interval_start.isoformat().replace("+00:00", "Z"),
        "abnormal_condition": False,
        "power_constraints_id": allocation.source_version,
        "power_envelopes": [
            {
                "id": str(uuid.uuid4()),
                "commodity_quantity": "ELECTRIC.POWER.L1",
                "power_envelope_elements": [
                    {
                        "duration": duration_ms,
                        "upper_limit": target_kw * 1000,
                        "lower_limit": target_kw * 1000,
                    }
                ],
            }
        ],
    }
    parsed = S2Parser().parse_as_any_message(json.dumps(payload))
    if not isinstance(parsed, PEBCInstruction):
        raise AssertionError("generated dispatch is not a PEBC instruction")
    return payload
