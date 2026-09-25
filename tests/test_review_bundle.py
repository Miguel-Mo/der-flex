from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from scripts.build_review_bundle import write_deterministic_tar, write_deterministic_zip
from scripts.verify_release import sha256
from scripts.verify_review_bundle import file_sha256, verify_bundle


def test_deterministic_bundle_zip_ignores_source_mtime(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "payload.txt"
    payload.write_text("same bytes\n", encoding="utf-8")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    write_deterministic_zip(source, first)
    payload.touch()
    write_deterministic_zip(source, second)

    assert sha256(first) == sha256(second)
    with zipfile.ZipFile(first) as archive:
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
        assert all(info.create_system == 0 for info in archive.infolist())


def test_deterministic_bundle_tar_ignores_source_mtime(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "payload.py"
    payload.write_text("value = 1\n", encoding="utf-8")
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"

    write_deterministic_tar(source, first)
    payload.touch()
    write_deterministic_tar(source, second)

    assert sha256(first) == sha256(second)


def test_bundle_verifier_rejects_tampered_file(tmp_path: Path) -> None:
    payload = tmp_path / "payload.txt"
    payload.write_text("original\n", encoding="utf-8")
    (tmp_path / "BUNDLE-SHA256SUMS").write_text(
        f"{file_sha256(payload)}  payload.txt\n", encoding="utf-8"
    )
    payload.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        verify_bundle(tmp_path)


def test_bundle_verifier_rejects_unexpected_file(tmp_path: Path) -> None:
    (tmp_path / "BUNDLE-SHA256SUMS").write_text("", encoding="utf-8")
    (tmp_path / "unexpected.json").write_text(json.dumps({"x": 1}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="bundle file set differs"):
        verify_bundle(tmp_path)
