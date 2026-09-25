from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.verify_container import build_offline_provenance, validate_probe


def test_container_probe_requires_installed_package_readiness_and_clean_dependencies() -> None:
    valid = {
        "version": "0.1.0",
        "location": "/usr/local/lib/python3.13/site-packages/der_flex/__init__.py",
        "ready": {"status": "ready"},
        "uid": 10001,
        "source_tree_present": False,
        "root_filesystem_write_blocked": True,
    }
    validate_probe(valid, "No broken requirements found.")

    for field, invalid_value in (
        ("version", "0.2.0"),
        ("location", "/app/src/der_flex/__init__.py"),
        ("ready", {"status": "starting"}),
        ("uid", 0),
        ("source_tree_present", True),
        ("root_filesystem_write_blocked", False),
    ):
        invalid = {**valid, field: invalid_value}
        with pytest.raises(RuntimeError):
            validate_probe(invalid, "No broken requirements found.")
    with pytest.raises(RuntimeError):
        validate_probe(valid, "der-flex has broken requirements")


def test_offline_provenance_links_release_container_and_materials(tmp_path: Path) -> None:
    (tmp_path / "source-manifest.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "requirements-runtime.lock").write_text("locked\n", encoding="utf-8")
    evidence = {
        "passed": True,
        "artifacts": [{"path": "package.whl", "sha256": "a" * 64, "size": 10}],
        "traceability": {"claim_count": 17},
    }
    report = {
        "passed": True,
        "verified_at": "2030-01-01T00:00:00+00:00",
        "dockerfile_sha256": "b" * 64,
        "image": {"tag": "example:1", "id": "sha256:" + "c" * 64},
    }

    provenance = build_offline_provenance(evidence, report, tmp_path)

    assert provenance["signed"] is False
    assert provenance["verification"]["container_passed"] is True
    assert provenance["subjects"][1]["digest"] == "sha256:" + "c" * 64
    assert len(provenance["materials"]["source_manifest_sha256"]) == 64
    assert any(
        "reproducible Docker image" in limitation
        for limitation in provenance["limitations"]
    )
    json.dumps(provenance)
