import gzip
import io
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

from scripts.verify_release import (
    RUNTIME_CONSTRAINTS,
    RUNTIME_LOCK,
    build_security_audit_report,
    cyclonedx_sbom,
    dependency_inventory,
    deterministic_gzip,
    normalize_sdist,
    normalize_wheel,
    prepare_output,
    sha256,
    source_manifest,
    verify_sdist_contents,
    wheelhouse_inventory,
)


def test_dev_extra_installs_every_no_isolation_build_requirement() -> None:
    configuration = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    build_requirements = set(configuration["build-system"]["requires"])
    development_requirements = set(configuration["project"]["optional-dependencies"]["dev"])

    assert build_requirements <= development_requirements


def test_runtime_constraints_mirror_hashed_lock() -> None:
    def versions(path: Path) -> set[str]:
        logical_lines = path.read_text(encoding="utf-8").replace("\\\n", " ").splitlines()
        return {
            line.split("--hash", 1)[0].strip()
            for line in logical_lines
            if line.strip() and not line.lstrip().startswith("#")
        }

    assert versions(RUNTIME_CONSTRAINTS) == versions(RUNTIME_LOCK)


def write_sdist(path: Path, *, timestamp: int, newline: bytes = b"\n") -> None:
    files = {
        "der-flex/source.txt": b"deterministic source\n",
        "der-flex/PKG-INFO": newline.join((b"Name: der-flex", b"Version: 1.0", b"")),
        "der-flex/der_flex.egg-info/PKG-INFO": newline.join(
            (b"Name: der-flex", b"Version: 1.0", b"")
        ),
        "der-flex/setup.cfg": newline.join((b"[egg_info]", b"tag_build =", b"")),
    }
    with (
        path.open("wb") as raw,
        gzip.GzipFile(filename="source.tar", fileobj=raw, mode="wb", mtime=timestamp) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        directory = tarfile.TarInfo("der-flex")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o777 if newline == b"\r\n" else 0o755
        directory.mtime = timestamp
        archive.addfile(directory)
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mode = 0o666 if newline == b"\r\n" else 0o644
            member.mtime = timestamp
            member.uid = timestamp % 100
            archive.addfile(member, io.BytesIO(payload))


def test_sdist_normalization_removes_archive_metadata_variation(tmp_path: Path) -> None:
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    write_sdist(first, timestamp=1_700_000_000, newline=b"\r\n")
    write_sdist(second, timestamp=1_800_000_000, newline=b"\n")

    normalize_sdist(first)
    normalize_sdist(second)

    assert sha256(first) == sha256(second)
    with tarfile.open(first, "r:gz") as archive:
        assert archive.getmember("der-flex").mode == 0o755
        assert all(member.mode == 0o644 for member in archive.getmembers() if member.isfile())
        for name in (
            "der-flex/PKG-INFO",
            "der-flex/der_flex.egg-info/PKG-INFO",
            "der-flex/setup.cfg",
        ):
            extracted = archive.extractfile(name)
            assert extracted is not None
            assert b"\r" not in extracted.read()


def test_deterministic_gzip_round_trips_without_zlib_compression() -> None:
    payload = (b"cross-platform bytes\n" * 10_000) + b"tail"

    first = deterministic_gzip(payload, mtime=1_789_257_600)
    second = deterministic_gzip(payload, mtime=1_789_257_600)

    assert first == second
    assert gzip.decompress(first) == payload


def test_security_audit_report_records_source_date_tool_and_lock() -> None:
    report = build_security_audit_report(
        {"dependencies": [{"name": "example", "version": "1", "vulns": []}]},
        queried_at="2030-01-01T00:00:00+00:00",
    )

    assert report["passed"] is True
    assert report["queried_at"] == "2030-01-01T00:00:00+00:00"
    assert report["vulnerability_service"] == "pypi"
    assert report["tool_version"]
    assert len(report["audited_lock_sha256"]) == 64
    assert report["database_snapshot_hash"] is None


def test_wheel_normalization_removes_line_endings_and_metadata_variation(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.whl"
    second = tmp_path / "second.whl"
    files = {
        "example/__init__.py": (b"value = 1\r\n", b"value = 1\n"),
        "example-1.0.dist-info/METADATA": (b"Name: example\r\n", b"Name: example\n"),
        "example-1.0.dist-info/RECORD": (b"old,hash,1\r\n", b"different,hash,2\n"),
    }
    for index, destination in enumerate((first, second)):
        with zipfile.ZipFile(destination, "w") as archive:
            for name, variants in files.items():
                info = zipfile.ZipInfo(name, (2020 + index, 1, 1, 0, 0, 0))
                info.create_system = 0 if index == 0 else 3
                info.external_attr = (0o100755 if index else 0o100600) << 16
                archive.writestr(info, variants[index])

    normalize_wheel(first)
    normalize_wheel(second)

    assert sha256(first) == sha256(second)
    with zipfile.ZipFile(first) as archive:
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
        assert all(info.create_system == 0 for info in archive.infolist())


def test_verification_output_cannot_escape_project_build_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside the project build directory"):
        prepare_output(tmp_path / "unsafe-output")


def test_source_manifest_excludes_generated_and_environment_files() -> None:
    paths = {entry["path"] for entry in source_manifest()}

    assert "pyproject.toml" in paths
    assert not any(str(path).startswith(("build/", ".venv/")) for path in paths)
    assert not any(".egg-info/" in str(path) for path in paths)


def test_sbom_serial_is_deterministic() -> None:
    components = [
        {
            "bom_ref": "pkg:pypi/example@1.0",
            "name": "example",
            "version": "1.0",
            "license": "MIT",
            "license_is_expression": True,
            "metadata_sha256": "a" * 64,
            "requested_extras": [],
        }
    ]
    graph = {"pkg:pypi/der-flex@0.1.0": ["pkg:pypi/example@1.0"]}

    assert (
        cyclonedx_sbom(components, graph)["serialNumber"]
        == cyclonedx_sbom(components, graph)["serialNumber"]
    )


def test_sbom_inventory_contains_transitive_runtime_closure_only() -> None:
    components, graph = dependency_inventory()
    names = {component["name"].lower() for component in components}

    assert {"fastapi", "starlette", "pydantic", "websockets"} <= names
    assert {"pytest", "build", "ruff", "mypy"}.isdisjoint(names)
    assert len(names) == len(components)
    assert all(component["bom_ref"].startswith("pkg:pypi/") for component in components)
    assert all(len(component["metadata_sha256"]) == 64 for component in components)
    assert (
        "pkg:pypi/websockets@13.1"
        in graph[
            next(reference for reference in graph if reference.startswith("pkg:pypi/s2-python@"))
        ]
    )


def test_cyclonedx_dependency_references_are_complete() -> None:
    components, graph = dependency_inventory()
    sbom = cyclonedx_sbom(components, graph)
    component_refs = {component["bom-ref"] for component in sbom["components"]}
    project_ref = sbom["metadata"]["component"]["bom-ref"]
    known_refs = component_refs | {project_ref}

    assert {dependency["ref"] for dependency in sbom["dependencies"]} == known_refs
    assert all(set(dependency["dependsOn"]) <= known_refs for dependency in sbom["dependencies"])


def test_sdist_content_check_rejects_incomplete_archive(tmp_path: Path) -> None:
    incomplete = tmp_path / "incomplete.tar.gz"
    write_sdist(incomplete, timestamp=1_700_000_000)

    with pytest.raises(RuntimeError, match="missing audit material"):
        verify_sdist_contents(incomplete)


def test_wheelhouse_matches_runtime_lock_and_supported_platforms() -> None:
    inventory = wheelhouse_inventory()

    assert inventory["wheel_count"] == 27
    assert inventory["locked_component_count"] == 22
    assert inventory["hashes_match_lock"] is True
    assert inventory["platforms"] == ["cp313-win_amd64", "cp313-manylinux-x86_64"]


def test_wheelhouse_rejects_a_tampered_lock(tmp_path: Path) -> None:
    lock = tmp_path / "tampered.lock"
    lock.write_text("example==1 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="hashes differ"):
        wheelhouse_inventory(lock_path=lock)
