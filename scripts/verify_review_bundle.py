"""Verify a DER Flex review bundle using only the Python standard library."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_bundle(root: Path) -> dict[str, Any]:
    checksum_path = root / "BUNDLE-SHA256SUMS"
    expected: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        if relative in expected:
            raise RuntimeError(f"duplicate checksum entry: {relative}")
        expected[relative] = digest
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != checksum_path
    }
    if actual_paths != set(expected):
        raise RuntimeError(
            f"bundle file set differs: missing={sorted(set(expected) - actual_paths)}, "
            f"unexpected={sorted(actual_paths - set(expected))}"
        )
    for relative, digest in expected.items():
        if file_sha256(root / relative) != digest:
            raise RuntimeError(f"checksum mismatch: {relative}")

    evidence = json.loads((root / "evidence" / "evidence.json").read_text(encoding="utf-8"))
    if evidence.get("passed") is not True:
        raise RuntimeError("embedded release evidence is not passing")
    source_manifest = json.loads(
        (root / "evidence" / "source-manifest.json").read_text(encoding="utf-8")
    )
    source_paths = {entry["path"] for entry in source_manifest}
    actual_source_paths = {
        path.relative_to(root / "source").as_posix()
        for path in (root / "source").rglob("*")
        if path.is_file()
    }
    if source_paths != actual_source_paths:
        raise RuntimeError("source tree does not match the embedded source manifest")
    for entry in source_manifest:
        path = root / "source" / entry["path"]
        if path.stat().st_size != entry["size"] or file_sha256(path) != entry["sha256"]:
            raise RuntimeError(f"source manifest mismatch: {entry['path']}")
    return {
        "passed": True,
        "bundle_file_count": len(expected),
        "source_file_count": len(source_manifest),
        "release_passed": True,
        "mutation_score_percent": evidence["mutation_gate"]["score_percent"],
        "traceability_claim_count": evidence["traceability"]["claim_count"],
        "offline_wheel_count": evidence["offline_wheelhouse_install"]["wheel_count"],
    }


def main() -> None:
    root = Path(__file__).resolve().parent
    try:
        report = verify_bundle(root)
    except Exception as error:
        print(f"BUNDLE VERIFICATION FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print("BUNDLE VERIFICATION PASSED")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
