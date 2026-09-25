import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from der_flex.adapters.openadr import OpenADRAdapter, OpenADREvent, OpenADRReportRequest
from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.domain import InMemoryOfferStore
from der_flex.reservations import ReservationService
from der_flex.simulators import build_demo_fleet, build_simulator_registry

FIXTURE = Path(__file__).parent / "fixtures" / "openadr" / "dispatch-event-3.1.0.json"
START = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)


def build_adapter() -> OpenADRAdapter:
    store = InMemoryOfferStore(minimum_participants=10)
    for resource in build_demo_fleet():
        constraints, forecast = resource.s2_offer_messages(START)
        for offer in normalize_pebc_offer(
            resource_id=resource.resource_id,
            zone_id=resource.zone_id,
            constraints_message=constraints,
            forecast_message=forecast,
            resource_registry=build_simulator_registry([resource]),
            source_epoch=resource.source_epoch,
            source_sequence=resource.source_sequence,
            observed_at=START,
            received_at=START,
        ):
            store.upsert(offer)
    return OpenADRAdapter(ReservationService(store))


def test_openadr_event_dispatches_and_returns_aggregate_report() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    OpenADREvent.model_validate(payload)
    adapter = build_adapter()

    results = adapter.handle_event(payload)

    assert len(results) == 1
    assert results[0].reservation.requested_power_kw == 10.0
    assert results[0].reservation.direction == "UPWARD"
    assert results[0].activation.status == "COMPLETED"
    report = results[0].report.model_dump(by_alias=True, mode="json", exclude_none=True)
    OpenADRReportRequest.model_validate(report)
    assert report["eventID"] == "oadr-event-001"
    assert report["resources"][0]["resourceName"] == "aggregate:ES-MA-29700"
    assert "resource_id" not in json.dumps(report)


def test_openadr_event_replay_is_idempotent() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    adapter = build_adapter()

    first = adapter.handle_event(payload)[0]
    replay = adapter.handle_event(payload)[0]

    assert replay.reservation.reservation_id == first.reservation.reservation_id
    assert replay.activation.activation_id == first.activation.activation_id


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_openadr_rejects_non_finite_dispatch_values(value: float) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["intervals"][0]["payloads"][0]["values"] = [value]

    with pytest.raises(ValueError, match="must be finite"):
        build_adapter().handle_event(payload)
