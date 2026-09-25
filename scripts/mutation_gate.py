"""Run a small, reproducible mutation gate over safety-critical rules."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    identifier: str
    path: str
    original: str
    replacement: str
    tests: tuple[str, ...]


MUTATIONS = (
    Mutation(
        "temporal-order-reversed",
        "src/der_flex/domain/store.py",
        "incoming_position < source_position",
        "incoming_position > source_position",
        (
            "tests/test_temporal_ordering.py::test_new_session_accepts_reset_counter_and_rejects_previous_session",
        ),
    ),
    Mutation(
        "privacy-threshold-reversed",
        "src/der_flex/domain/store.py",
        "participants < self.minimum_participants",
        "participants > self.minimum_participants",
        ("tests/test_vertical_slice.py::test_privacy_threshold_suppresses_single_participant",),
    ),
    Mutation(
        "reservation-delta-not-suppressed",
        "src/der_flex/reservations/service.py",
        "previous is not None and previous != residual",
        "previous is not None and previous == residual",
        (
            "tests/test_reservations.py::test_public_residual_cell_is_suppressed_after_reservation_change",
        ),
    ),
    Mutation(
        "reserved-capacity-added-back",
        "src/der_flex/reservations/service.py",
        "capacity - already_reserved",
        "capacity + already_reserved",
        (
            "tests/test_reservations.py::test_concurrent_reservations_cannot_double_sell_capacity",
        ),
    ),
    Mutation(
        "physical-lower-bound-trusted-from-s2",
        "src/der_flex/adapters/s2/pebc.py",
        "minimum_upper_w = max(",
        "minimum_upper_w = min(",
        (
            "tests/test_vertical_slice.py::test_physical_envelope_clamps_overstated_s2_constraints",
        ),
    ),
    Mutation(
        "openadr-duration-tenfold",
        "src/der_flex/adapters/openadr/adapter.py",
        "0 < total_seconds <= 86_400",
        "0 < total_seconds <= 864_000",
        (
            "tests/test_fuzz_boundaries.py::test_fuzzed_openadr_durations_are_rejected_or_bounded",
        ),
    ),
)


def apply_mutation(source_root: Path, mutation: Mutation) -> None:
    target = source_root.parent / mutation.path
    content = target.read_text(encoding="utf-8")
    occurrences = content.count(mutation.original)
    if occurrences != 1:
        raise RuntimeError(
            f"{mutation.identifier}: expected one mutation site, found {occurrences}"
        )
    target.write_text(
        content.replace(mutation.original, mutation.replacement), encoding="utf-8"
    )


def run_mutation(mutation: Mutation, workspace: Path) -> dict[str, object]:
    mutant_root = workspace / mutation.identifier
    source_root = mutant_root / "src"
    shutil.copytree(ROOT / "src", source_root)
    apply_mutation(source_root, mutation)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *mutation.tests],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONPATH": str(source_root)},
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    killed = result.returncode == 1 and "1 failed" in output
    return {
        "id": mutation.identifier,
        "killed": killed,
        "tests": list(mutation.tests),
        "returncode": result.returncode,
        "diagnostic": "" if killed else output[-2_000:],
    }


def verify_mutations() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="der-flex-mutations-") as temporary:
        results = [run_mutation(mutation, Path(temporary)) for mutation in MUTATIONS]
    survived = [str(result["id"]) for result in results if not result["killed"]]
    return {
        "schema_version": "1.0",
        "mutation_count": len(results),
        "killed_count": len(results) - len(survived),
        "score_percent": round((len(results) - len(survived)) / len(results) * 100, 2),
        "passed": not survived,
        "survived": survived,
        "results": results,
    }


def main() -> None:
    report = verify_mutations()
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
