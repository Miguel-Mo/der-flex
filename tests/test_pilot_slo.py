from __future__ import annotations

import pytest

from scripts.verify_pilot_slo import Sample, assert_slos, percentile, summarize


def test_percentile_uses_nearest_rank_and_rejects_invalid_input() -> None:
    assert percentile([5.0, 1.0, 4.0, 2.0, 3.0], 0.50) == 3.0
    assert percentile([5.0, 1.0, 4.0, 2.0, 3.0], 0.95) == 5.0
    with pytest.raises(ValueError, match="without samples"):
        percentile([], 0.95)
    with pytest.raises(ValueError, match="quantile"):
        percentile([1.0], 0)


def test_summary_keeps_shed_status_separate_from_success_latency() -> None:
    samples = [
        Sample("reservation", 201, 10.0),
        Sample("reservation", 201, 20.0),
        Sample("reservation", 503, 1.0),
    ]

    result = summarize(samples, "reservation")

    assert result["requests"] == 3
    assert result["successful"] == 2
    assert result["statuses"] == {"201": 2, "503": 1}
    assert result["p95_ms"] == 20.0


def test_slo_gate_rejects_excess_latency() -> None:
    report = {
        "operations": {
            "flexibility": {"p95_ms": 10},
            "reservation": {"p95_ms": 20},
            "activation": {"p95_ms": 30},
        },
        "fault_injection": {"detection_ms": 7_000},
    }

    with pytest.raises(RuntimeError, match="dependency_failure_detection"):
        assert_slos(report)
