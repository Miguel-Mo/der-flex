from datetime import UTC, datetime, timedelta

import pytest

from der_flex.adapters.s2 import OfferClockSkewError, normalize_pebc_offer
from der_flex.domain import FlexibilityOffer, InMemoryOfferStore, StaleOfferError
from der_flex.simulators import BatterySimulator, build_simulator_registry

START = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)


def offers_from(
    battery: BatterySimulator,
    *,
    start: datetime = START,
    interval_count: int = 1,
    observed_at: datetime = START,
    received_at: datetime = START,
) -> list[FlexibilityOffer]:
    constraints, forecast = battery.s2_offer_messages(start, interval_count=interval_count)
    return normalize_pebc_offer(
        resource_id=battery.resource_id,
        zone_id=battery.zone_id,
        constraints_message=constraints,
        forecast_message=forecast,
        resource_registry=build_simulator_registry([battery]),
        source_epoch=battery.source_epoch,
        source_sequence=battery.source_sequence,
        observed_at=observed_at,
        received_at=received_at,
    )


def test_new_session_accepts_reset_counter_and_rejects_previous_session() -> None:
    battery = BatterySimulator(power_kw=2.0)
    battery.set_power(3.0)
    old_offer = offers_from(battery)[0]
    store = InMemoryOfferStore()
    store.upsert(old_offer)

    battery.restart_session(power_kw=-1.0)
    new_offer = offers_from(battery)[0]
    store.upsert(new_offer)

    assert (new_offer.source_epoch, new_offer.source_sequence) == (1, 0)
    with pytest.raises(StaleOfferError, match="older"):
        store.upsert(old_offer)
    aggregate = store.query(battery.zone_id, START, new_offer.interval_end, now=START)[0]
    assert aggregate.baseline_power_kw == -1.0


def test_reconnect_requires_source_position_to_advance() -> None:
    battery = BatterySimulator()
    original = offers_from(battery)[0]
    store = InMemoryOfferStore()
    store.upsert(original)
    assert store.remove_resource(battery.resource_id) == 1

    with pytest.raises(StaleOfferError, match="disconnected"):
        store.upsert(original)

    battery.restart_session()
    store.upsert(offers_from(battery)[0])


def test_new_position_invalidates_the_previous_resource_horizon() -> None:
    battery = BatterySimulator(power_kw=1.0)
    original = offers_from(battery, interval_count=4)
    store = InMemoryOfferStore()
    for offer in original:
        store.upsert(offer)

    battery.set_power(0.0)
    replacement = offers_from(battery)[0]
    store.upsert(replacement)

    aggregates = store.query(
        battery.zone_id,
        START,
        START + timedelta(hours=1),
        now=START,
    )
    assert len(aggregates) == 1
    assert aggregates[0].interval_start == START


@pytest.mark.parametrize(
    ("observed_at", "message"),
    [
        (START - timedelta(minutes=15, seconds=1), "too old"),
        (START + timedelta(minutes=2, seconds=1), "future"),
    ],
)
def test_observations_outside_clock_window_are_rejected(
    observed_at: datetime, message: str
) -> None:
    battery = BatterySimulator()

    with pytest.raises(OfferClockSkewError, match=message):
        offers_from(battery, observed_at=observed_at)


def test_clock_window_boundaries_are_accepted() -> None:
    battery = BatterySimulator()

    assert offers_from(battery, observed_at=START - timedelta(minutes=15))
    assert offers_from(battery, observed_at=START + timedelta(minutes=2))


def test_naive_observation_timestamp_is_rejected() -> None:
    battery = BatterySimulator()

    with pytest.raises(ValueError, match="timezone-aware"):
        offers_from(battery, observed_at=START.replace(tzinfo=None))
