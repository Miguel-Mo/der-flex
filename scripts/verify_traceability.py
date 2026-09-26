"""Validate that audit claims point to existing code, tests and evidence outputs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "traceability.json"
EXPECTED_AUDIT_CLAIMS = {
    "T01", "T02", "P01", "P02", "PR01", "C01", "S01", "PERF01", "SEC01", "PKG01"
}
ALLOWED_STATUSES = {"PASS", "PASS_WITH_SCOPE_LIMIT", "NOT_RUN"}
KNOWN_EVIDENCE_OUTPUTS = {
    "evidence.json",
    "source-manifest.json",
    "sbom.cdx.json",
    "mutation-report.json",
    "traceability-report.json",
    "requirements-runtime.lock",
    "security-audit.json",
    "container-report.json",
    "pilot-slo-report.json",
    "offline-provenance.json",
    "SHA256SUMS",
}


def load_matrix() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(MATRIX.read_text(encoding="utf-8")))


def collected_tests() -> set[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"pytest collection failed:\n{result.stdout}\n{result.stderr}")
    return {line.strip() for line in result.stdout.splitlines() if "::" in line}


def validate_matrix(matrix: dict[str, Any], tests: set[str]) -> dict[str, object]:
    claims = matrix.get("claims")
    if not isinstance(claims, list) or not claims:
        raise RuntimeError("traceability matrix must contain claims")
    identifiers: set[str] = set()
    references = 0
    for raw_claim in claims:
        if not isinstance(raw_claim, dict):
            raise RuntimeError("every claim must be an object")
        claim = cast(dict[str, Any], raw_claim)
        identifier = str(claim.get("id", ""))
        if not identifier or identifier in identifiers:
            raise RuntimeError(f"duplicate or empty claim id: {identifier!r}")
        identifiers.add(identifier)
        if claim.get("status") not in ALLOWED_STATUSES:
            raise RuntimeError(f"{identifier}: invalid status")
        if not claim.get("claim") or not claim.get("commands") or not claim.get("residual_limit"):
            raise RuntimeError(f"{identifier}: incomplete claim description")
        for path_value in claim.get("implementation_paths", []):
            references += 1
            if not (ROOT / str(path_value)).is_file():
                raise RuntimeError(f"{identifier}: missing implementation path {path_value}")
        for test_node in claim.get("test_nodes", []):
            references += 1
            if str(test_node) not in tests:
                raise RuntimeError(f"{identifier}: test node was not collected: {test_node}")
        unknown_outputs = set(claim.get("evidence_outputs", [])) - KNOWN_EVIDENCE_OUTPUTS
        if unknown_outputs:
            raise RuntimeError(f"{identifier}: unknown evidence outputs {sorted(unknown_outputs)}")
    missing = EXPECTED_AUDIT_CLAIMS - identifiers
    if missing:
        raise RuntimeError(f"missing original audit claims: {sorted(missing)}")
    counts = {
        status: sum(claim["status"] == status for claim in claims)
        for status in sorted(ALLOWED_STATUSES)
    }
    return {
        "schema_version": "1.0",
        "passed": True,
        "claim_count": len(claims),
        "reference_count": references,
        "status_counts": counts,
        "original_audit_claims_complete": True,
    }


def verify_traceability() -> dict[str, object]:
    return validate_matrix(load_matrix(), collected_tests())


def main() -> None:
    print(json.dumps(verify_traceability(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
