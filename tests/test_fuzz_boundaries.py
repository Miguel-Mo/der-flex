from __future__ import annotations

import json
import math
import random
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from der_flex.adapters.openadr.adapter import parse_duration
from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.api import create_app
from der_flex.simulators import BatterySimulator, build_simulator_registry

START = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)
FUZZ_CASES = 300


def random_json_value(generator: random.Random, depth: int = 0) -> Any:
    scalars: list[Any] = [
        None,
        True,
        False,
        0,
        -1,
        1_000_001,
        10**100,
        float("nan"),
        float("inf"),
        "",
        "\x00",
        "A" * 1_024,
        "2030-01-01T18:00:00Z",
    ]
    if depth >= 3 or generator.random() < 0.6:
        return generator.choice(scalars)
    if generator.random() < 0.5:
        return [random_json_value(generator, depth + 1) for _ in range(generator.randrange(5))]
    return {
        generator.choice(("zone_id", "power_kw", "interval_start", "x", "")): (
            random_json_value(generator, depth + 1)
        )
        for _ in range(generator.randrange(5))
    }


def test_fuzzed_reservation_json_never_raises_or_returns_server_error() -> None:
    generator = random.Random(0xD3F1E8)
    with TestClient(create_app()) as client:
        for case in range(FUZZ_CASES):
            payload = random_json_value(generator)
            encoded = json.dumps(payload, allow_nan=True).encode()
            response = client.post(
                "/api/v1/reservations",
                content=encoded,
                headers={
                    "Content-Type": "application/json",
                    "Idempotency-Key": f"fuzz-{case}",
                },
            )
            assert 400 <= response.status_code < 500, (case, encoded, response.text)
            assert "resource_id" not in response.text


def test_fuzzed_malformed_json_never_reaches_application_logic() -> None:
    generator = random.Random(0xBAD5EED)
    with TestClient(create_app()) as client:
        for case in range(FUZZ_CASES):
            size = generator.randrange(0, 512)
            payload = generator.randbytes(size)
            response = client.post(
                "/api/v1/reservations",
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "Idempotency-Key": f"malformed-{case}",
                },
            )
            assert 400 <= response.status_code < 500, (case, payload, response.text)


def test_fuzzed_openadr_durations_are_rejected_or_bounded() -> None:
    generator = random.Random(0x0AD3)
    alphabet = "PTHMS0123456789-+. abcdef"
    candidates = [
        f"PT{generator.randrange(10**30)}{generator.choice(('H', 'M', 'S'))}"
        if generator.random() < 0.5
        else "".join(generator.choice(alphabet) for _ in range(generator.randrange(80)))
        for _ in range(FUZZ_CASES)
    ]
    candidates.extend(("PT0S", "PT24H", "PT1440M", "PT86400S", "PT86401S"))

    for case, value in enumerate(candidates):
        try:
            duration = parse_duration(value)
        except ValueError:
            continue
        assert 0 < duration.total_seconds() <= 86_400, (case, value, duration)


def test_fuzzed_s2_numeric_fields_are_rejected_or_remain_physically_bounded() -> None:
    generator = random.Random(0x52F022)
    candidate_values = [
        -1e308,
        -1e9,
        -5_000.0,
        -0.0,
        0.0,
        5_000.0,
        1e9,
        1e308,
        float("nan"),
        float("inf"),
        float("-inf"),
    ]
    battery = BatterySimulator(power_kw=2.0)
    registry = build_simulator_registry([battery])

    for case in range(FUZZ_CASES):
        constraints, forecast = battery.s2_offer_messages(START)
        constraints = deepcopy(constraints)
        forecast = deepcopy(forecast)
        value = generator.choice(candidate_values)
        target = generator.randrange(7)
        if target < 4:
            limit = constraints["allowed_limit_ranges"][target // 2]["range_boundary"]
            limit[("start_of_range", "end_of_range")[target % 2]] = value
        else:
            power = forecast["elements"][0]["power_values"][0]
            power[("value_expected", "value_lower_95PPR", "value_upper_95PPR")[target - 4]] = (
                value
            )

        try:
            offers = normalize_pebc_offer(
                resource_id=battery.resource_id,
                zone_id=battery.zone_id,
                constraints_message=constraints,
                forecast_message=forecast,
                resource_registry=registry,
                source_epoch=battery.source_epoch,
                source_sequence=battery.source_sequence,
                observed_at=START,
                received_at=START,
            )
        except ValueError:
            continue

        for offer in offers:
            numeric = (
                offer.baseline_power_kw,
                offer.upward_capacity_kw,
                offer.downward_capacity_kw,
                offer.upward_energy_kwh,
                offer.downward_energy_kwh,
                offer.confidence,
            )
            assert all(math.isfinite(item) for item in numeric), (case, target, value)
            assert -5 <= offer.baseline_power_kw <= 5
            assert 0 <= offer.upward_capacity_kw <= 10
            assert 0 <= offer.downward_capacity_kw <= 10
