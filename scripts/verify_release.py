"""Build and verify reproducible local release artifacts without network access."""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import uuid
import venv
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build" / "local-verification"
WHEELHOUSE = ROOT / "vendor" / "wheelhouse"
RUNTIME_LOCK = ROOT / "requirements-runtime.lock"
RUNTIME_CONSTRAINTS = ROOT / "requirements-runtime.constraints"
SOURCE_DATE_EPOCH = "1789257600"
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
}
TEXT_ARCHIVE_SUFFIXES = {
    ".cfg",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_ARCHIVE_NAMES = {
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "MANIFEST.in",
    "METADATA",
    "PKG-INFO",
    "README.md",
    "SECURITY.md",
    "WHEEL",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deterministic_gzip(payload: bytes, *, mtime: int) -> bytes:
    """Encode gzip with uncompressed DEFLATE blocks, independent of zlib."""

    output = bytearray(b"\x1f\x8b\x08\x00")
    output.extend(struct.pack("<I", mtime))
    output.extend(b"\x00\xff")
    chunks = [payload[index : index + 65_535] for index in range(0, len(payload), 65_535)]
    if not chunks:
        chunks = [b""]
    for index, chunk in enumerate(chunks):
        output.append(1 if index == len(chunks) - 1 else 0)
        length = len(chunk)
        output.extend(struct.pack("<HH", length, 0xFFFF ^ length))
        output.extend(chunk)
    output.extend(struct.pack("<II", binascii.crc32(payload), len(payload) & 0xFFFFFFFF))
    return bytes(output)


def run(
    command: list[str], *, cwd: Path = ROOT, extra_env: dict[str, str] | None = None
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
            **(extra_env or {}),
        },
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def build_artifacts(output: Path) -> dict[str, Path]:
    output.mkdir(parents=True)
    run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--wheel",
            "--sdist",
            "--outdir",
            str(output),
            ".",
        ]
    )
    wheels = list(output.glob("*.whl"))
    sdists = list(output.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError("expected exactly one wheel and one sdist")
    normalize_wheel(wheels[0])
    normalize_sdist(sdists[0])
    return {"wheel": wheels[0], "sdist": sdists[0]}


def _normalize_text_payload(name: str, payload: bytes) -> bytes:
    path = Path(name)
    if path.suffix.lower() in TEXT_ARCHIVE_SUFFIXES or path.name in TEXT_ARCHIVE_NAMES:
        return payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return payload


def normalize_wheel(path: Path) -> None:
    """Normalize wheel text, metadata, RECORD, ordering, timestamps and modes."""

    epoch = datetime.fromtimestamp(int(SOURCE_DATE_EPOCH), tz=UTC)
    zip_time = (epoch.year, epoch.month, epoch.day, epoch.hour, 0, 0)
    normalized = path.with_suffix(path.suffix + ".normalized")
    with zipfile.ZipFile(path) as source:
        contents = {
            name: _normalize_text_payload(name, source.read(name))
            for name in source.namelist()
            if not name.endswith("/") and not name.endswith(".dist-info/RECORD")
        }
        record_candidates = [
            name for name in source.namelist() if name.endswith(".dist-info/RECORD")
        ]
    if len(record_candidates) != 1:
        raise RuntimeError("wheel must contain exactly one RECORD")
    record_name = record_candidates[0]
    rows: list[tuple[str, str, str]] = []
    for name, payload in sorted(contents.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
        rows.append((name, f"sha256={digest.decode()}", str(len(payload))))
    rows.append((record_name, "", ""))
    record_stream = io.StringIO(newline="")
    csv.writer(record_stream, lineterminator="\n").writerows(rows)
    contents[record_name] = record_stream.getvalue().encode()
    with zipfile.ZipFile(normalized, "w", compression=zipfile.ZIP_STORED) as destination:
        for name, payload in sorted(contents.items()):
            info = zipfile.ZipInfo(name, zip_time)
            info.create_system = 0
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            destination.writestr(info, payload)
    normalized.replace(path)


def normalize_sdist(path: Path) -> None:
    """Repack the standard sdist with stable gzip and tar metadata."""

    epoch = int(SOURCE_DATE_EPOCH)
    normalized = path.with_suffix(path.suffix + ".normalized")
    tar_buffer = io.BytesIO()
    with tarfile.open(path, "r:gz") as source:
        members = sorted(source.getmembers(), key=lambda item: item.name)
        with tarfile.open(
            fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT
        ) as destination:
            for member in members:
                stable = copy.copy(member)
                stable.uid = 0
                stable.gid = 0
                stable.uname = ""
                stable.gname = ""
                stable.mtime = epoch
                stable.pax_headers = {}
                stable.mode = 0o755 if member.isdir() else 0o644
                payload = None
                if member.isfile():
                    extracted = source.extractfile(member)
                    if extracted is None:
                        raise RuntimeError(f"could not read sdist member {member.name}")
                    normalized_payload = _normalize_text_payload(member.name, extracted.read())
                    stable.size = len(normalized_payload)
                    payload = io.BytesIO(normalized_payload)
                destination.addfile(stable, payload)
    normalized.write_bytes(deterministic_gzip(tar_buffer.getvalue(), mtime=epoch))
    normalized.replace(path)


def verify_sdist_contents(path: Path) -> list[str]:
    required_suffixes = {
        ".gitattributes",
        "LICENSE",
        "README.md",
        "pyproject.toml",
        "requirements-runtime.lock",
        "requirements-runtime.constraints",
        "scripts/verify_release.py",
        "scripts/verify_container.py",
        "scripts/mutation_gate.py",
        "scripts/verify_traceability.py",
        "scripts/build_review_bundle.py",
        "scripts/verify_review_bundle.py",
        "tests/test_release_verification.py",
        "docs/architecture.md",
        "docs/traceability.json",
        "docs/traceability.md",
        "docs/reviewer-start-here.md",
        "docs/external-review-prompt-v4.md",
        "docs/expected-results-v4.md",
        "vendor/wheelhouse/fastapi-0.141.1-py3-none-any.whl",
    }
    with tarfile.open(path, "r:gz") as archive:
        names = sorted(member.name for member in archive.getmembers() if member.isfile())
    missing = [
        suffix
        for suffix in sorted(required_suffixes)
        if not any(name.endswith(suffix) for name in names)
    ]
    if missing:
        raise RuntimeError(f"sdist is missing audit material: {missing}")
    return names


def source_manifest() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(item for item in ROOT.rglob("*") if item.is_file()):
        relative = path.relative_to(ROOT)
        if any(
            part in EXCLUDED_PARTS or part.endswith(".egg-info")
            for part in relative.parts
        ):
            continue
        entries.append(
            {
                "path": relative.as_posix(),
                "sha256": sha256(path),
                "size": path.stat().st_size,
            }
        )
    return entries


def _requirement_applies(requirement: Requirement, extras: set[str]) -> bool:
    if requirement.marker is None:
        return True
    contexts = extras or {""}
    return any(requirement.marker.evaluate({"extra": extra}) for extra in contexts)


def _metadata_sha256(distribution: importlib.metadata.Distribution) -> str:
    metadata_path = distribution.locate_file(
        next(
            path
            for path in distribution.files or ()
            if Path(path).name == "METADATA" and ".dist-info" in str(path)
        )
    )
    return sha256(Path(str(metadata_path)))


def _component_ref(name: str, version: str) -> str:
    normalized = str(canonicalize_name(name))
    return f"pkg:pypi/{quote(normalized, safe='')}@{quote(version, safe='')}"


def dependency_inventory() -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Resolve the installed production dependency closure and its graph.

    This deliberately follows only the project's production requirements and the
    extras they request.  Development-only extras from this project or from an
    upstream package are therefore excluded.
    """

    project = importlib.metadata.distribution("der-flex")
    project_name = str(project.metadata["Name"])
    root_name = str(canonicalize_name(project_name))
    project_ref = _component_ref(project_name, project.version)
    selected_extras: dict[str, set[str]] = {root_name: set()}
    distributions: dict[str, importlib.metadata.Distribution] = {
        root_name: project
    }
    pending = [root_name]
    graph_names: dict[str, set[str]] = {}

    while pending:
        parent_name = pending.pop(0)
        parent = distributions[parent_name]
        children = graph_names.setdefault(parent_name, set())
        for raw_requirement in parent.requires or ():
            requirement = Requirement(raw_requirement)
            if not _requirement_applies(requirement, selected_extras[parent_name]):
                continue
            child_name = str(canonicalize_name(requirement.name))
            distributions.setdefault(
                child_name, importlib.metadata.distribution(requirement.name)
            )
            children.add(child_name)
            previous_extras = selected_extras.setdefault(child_name, set())
            expanded = set(requirement.extras) - previous_extras
            if child_name not in graph_names or expanded:
                previous_extras.update(requirement.extras)
                if child_name not in pending:
                    pending.append(child_name)

    dependency_names = sorted(name for name in distributions if name != root_name)
    components: list[dict[str, Any]] = []
    refs: dict[str, str] = {}
    for name in dependency_names:
        distribution = distributions[name]
        display_name = str(distribution.metadata["Name"])
        component_ref = _component_ref(display_name, distribution.version)
        refs[name] = component_ref
        license_expression = distribution.metadata.get("License-Expression")
        license_name = license_expression or distribution.metadata.get("License") or "UNKNOWN"
        components.append(
            {
                "bom_ref": component_ref,
                "name": display_name,
                "version": distribution.version,
                "license": str(license_name),
                "license_is_expression": license_expression is not None,
                "metadata_sha256": _metadata_sha256(distribution),
                "requested_extras": sorted(selected_extras[name]),
            }
        )

    refs[root_name] = project_ref
    graph: dict[str, list[str]] = {}
    for graph_parent in sorted(graph_names):
        parent_ref = refs[graph_parent]
        graph[parent_ref] = sorted(refs[child] for child in graph_names[graph_parent])
    for component_ref in refs.values():
        graph.setdefault(component_ref, [])
    return components, dict(sorted(graph.items()))


def verify_project_checks() -> dict[str, str]:
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", "."],
        "mypy": [sys.executable, "-m", "mypy", "src", "scripts", "tests"],
        "pip_check": [sys.executable, "-m", "pip", "check"],
        "s2_smoke": [sys.executable, "scripts/s2_pebc_smoke.py"],
        "mutation_gate": [sys.executable, "scripts/mutation_gate.py"],
        "traceability": [sys.executable, "scripts/verify_traceability.py"],
    }
    return {name: run(command) for name, command in commands.items()}


def build_security_audit_report(
    result: dict[str, Any], *, queried_at: str
) -> dict[str, Any]:
    dependencies = result.get("dependencies", [])
    vulnerability_count = sum(len(item.get("vulns", [])) for item in dependencies)
    return {
        "schema_version": "1.0",
        "passed": vulnerability_count == 0,
        "queried_at": queried_at,
        "tool": "pip-audit",
        "tool_version": importlib.metadata.version("pip-audit"),
        "vulnerability_service": "pypi",
        "source": "PyPI vulnerability service selected by pip-audit",
        "database_snapshot_hash": None,
        "database_snapshot_note": "The remote service does not expose a snapshot hash.",
        "audited_lock": RUNTIME_LOCK.name,
        "audited_lock_sha256": sha256(RUNTIME_LOCK),
        "dependency_count": len(dependencies),
        "vulnerability_count": vulnerability_count,
        "result": result,
    }


def verify_security_audit() -> dict[str, Any]:
    raw = run(
        [
            sys.executable,
            "-m",
            "pip_audit",
            "-r",
            str(RUNTIME_LOCK),
            "--progress-spinner",
            "off",
            "--format",
            "json",
            "--vulnerability-service",
            "pypi",
        ]
    )
    report = build_security_audit_report(
        json.loads(raw), queried_at=datetime.now(UTC).isoformat()
    )
    if not report["passed"]:
        raise RuntimeError("the runtime lock contains known vulnerabilities")
    return report


def cyclonedx_sbom(
    components: list[dict[str, Any]], graph: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    dependency_graph = graph or {}
    serial_material = json.dumps(
        {"components": components, "graph": dependency_graph},
        sort_keys=True,
        separators=(",", ":"),
    )
    serial = uuid.uuid5(uuid.NAMESPACE_URL, f"der-flex-sbom:{serial_material}")
    project_version = importlib.metadata.version("der-flex")
    project_ref = _component_ref("der-flex", project_version)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": project_ref,
                "name": "der-flex",
                "version": project_version,
            }
        },
        "components": [
            {
                "type": "library",
                "bom-ref": item["bom_ref"],
                "name": item["name"],
                "version": item["version"],
                "purl": item["bom_ref"],
                "licenses": [
                    {
                        "expression" if item["license_is_expression"] else "license": (
                            item["license"]
                            if item["license_is_expression"]
                            else {"name": item["license"]}
                        )
                    }
                ],
                "properties": [
                    {
                        "name": "der-flex:installed-metadata:sha256",
                        "value": item["metadata_sha256"],
                    },
                    {
                        "name": "der-flex:requested-extras",
                        "value": ",".join(item["requested_extras"]),
                    },
                ],
            }
            for item in components
        ],
        "dependencies": [
            {"ref": reference, "dependsOn": dependencies}
            for reference, dependencies in sorted(dependency_graph.items())
        ],
    }


def build_wheel_from_sdist(sdist: Path, workspace: Path) -> Path:
    extracted = workspace / "sdist-source"
    extracted.mkdir()
    with tarfile.open(sdist, "r:gz") as archive:
        archive.extractall(extracted, filter="data")
    roots = [item for item in extracted.iterdir() if item.is_dir()]
    if len(roots) != 1:
        raise RuntimeError("sdist must contain exactly one source root")
    output = workspace / "sdist-wheel"
    output.mkdir()
    run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--wheel",
            "--outdir",
            str(output),
            ".",
        ],
        cwd=roots[0],
    )
    wheels = list(output.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("sdist rebuild did not produce exactly one wheel")
    normalize_wheel(wheels[0])
    return wheels[0]


def verify_installed_wheel(wheel: Path, workspace: Path) -> dict[str, str]:
    installed_target = workspace / "isolated-install"
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--target",
            str(installed_target),
            str(wheel),
        ],
        cwd=workspace,
    )
    probe = (
        "import json, pathlib, der_flex; "
        "from der_flex.demo import build_demo_app; "
        "location=pathlib.Path(der_flex.__file__).resolve(); "
        f"assert pathlib.Path({str(ROOT)!r}) not in location.parents; "
        "app=build_demo_app(); "
        "print(json.dumps({'version': der_flex.__version__, "
        "'location': str(location), 'routes': len(app.routes)}))"
    )
    raw_installed: object = json.loads(
        run(
            [sys.executable, "-c", probe],
            cwd=workspace,
            extra_env={"PYTHONPATH": str(installed_target)},
        )
    )
    if not isinstance(raw_installed, dict):
        raise RuntimeError("installed package probe returned an invalid result")
    installed = {str(key): str(value) for key, value in raw_installed.items()}
    run([sys.executable, "-m", "pip", "check"], cwd=workspace)
    return installed


def wheelhouse_inventory(
    wheelhouse: Path = WHEELHOUSE, lock_path: Path = RUNTIME_LOCK
) -> dict[str, object]:
    wheels = sorted(wheelhouse.glob("*.whl"))
    lock_content = lock_path.read_text(encoding="utf-8")
    locked_hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", lock_content))
    wheel_hashes = {sha256(path) for path in wheels}
    if locked_hashes != wheel_hashes:
        raise RuntimeError(
            "wheelhouse and runtime lock hashes differ: "
            f"missing={sorted(locked_hashes - wheel_hashes)}, "
            f"unexpected={sorted(wheel_hashes - locked_hashes)}"
        )
    logical_lines = lock_content.replace("\\\n", " ").splitlines()
    locked_requirements = [
        Requirement(line.split("--hash", 1)[0].strip())
        for line in logical_lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    components, _graph = dependency_inventory()
    expected_versions = {
        canonicalize_name(str(component["name"])): str(component["version"])
        for component in components
    }
    locked_versions = {
        canonicalize_name(requirement.name): str(next(iter(requirement.specifier)).version)
        for requirement in locked_requirements
    }
    constrained_versions = {
        canonicalize_name(requirement.name): str(next(iter(requirement.specifier)).version)
        for requirement in (
            Requirement(line.strip())
            for line in RUNTIME_CONSTRAINTS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    }
    if constrained_versions != locked_versions:
        raise RuntimeError("runtime constraints do not match the hashed runtime lock")
    if locked_versions != expected_versions:
        raise RuntimeError("runtime lock does not match the installed production closure")
    filenames = [path.name for path in wheels]
    for distribution_name in ("pydantic_core", "psycopg_binary", "websockets"):
        matching = [name for name in filenames if name.startswith(f"{distribution_name}-")]
        if not any("win_amd64" in name for name in matching) or not any(
            "manylinux" in name for name in matching
        ):
            raise RuntimeError(f"{distribution_name} lacks Windows or Linux wheel coverage")
    return {
        "wheel_count": len(wheels),
        "locked_component_count": len(locked_requirements),
        "total_size": sum(path.stat().st_size for path in wheels),
        "platforms": ["cp313-win_amd64", "cp313-manylinux-x86_64"],
        "hashes_match_lock": True,
    }


def verify_offline_wheelhouse_install(wheel: Path, workspace: Path) -> dict[str, object]:
    inventory = wheelhouse_inventory()
    environment = workspace / "offline-venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    offline_environment = {"PIP_NO_INDEX": "1", "PYTHONPATH": ""}
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(WHEELHOUSE),
            "--require-hashes",
            "--no-deps",
            "-r",
            str(RUNTIME_LOCK),
        ],
        cwd=workspace,
        extra_env=offline_environment,
    )
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            str(wheel.resolve()),
        ],
        cwd=workspace,
        extra_env=offline_environment,
    )
    dependency_check = run(
        [str(python), "-m", "pip", "check"], cwd=workspace, extra_env=offline_environment
    )
    probe = (
        "import json, pathlib, der_flex; "
        "from der_flex.demo import build_demo_app; "
        "location=pathlib.Path(der_flex.__file__).resolve(); "
        "app=build_demo_app(); "
        "print(json.dumps({'version': der_flex.__version__, "
        "'location': str(location), 'routes': len(app.routes)}))"
    )
    installed = json.loads(
        run([str(python), "-c", probe], cwd=workspace, extra_env=offline_environment)
    )
    return {**inventory, "pip_check": dependency_check, "installed": installed}


def prepare_output(output: Path) -> None:
    resolved = output.resolve()
    build_root = (ROOT / "build").resolve()
    if resolved != build_root and build_root not in resolved.parents:
        raise ValueError("output must be inside the project build directory")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def verify(output: Path) -> dict[str, Any]:
    prepare_output(output)
    project_checks = verify_project_checks()
    security_audit = verify_security_audit()
    with tempfile.TemporaryDirectory(prefix="der-flex-release-") as temporary:
        workspace = Path(temporary)
        first = build_artifacts(workspace / "first")
        second = build_artifacts(workspace / "second")
        comparisons = {
            kind: {
                "first_sha256": sha256(first[kind]),
                "second_sha256": sha256(second[kind]),
                "identical": sha256(first[kind]) == sha256(second[kind]),
            }
            for kind in ("wheel", "sdist")
        }
        if not all(item["identical"] for item in comparisons.values()):
            raise RuntimeError(
                "release artifacts are not reproducible: "
                + json.dumps(comparisons, sort_keys=True)
            )

        sdist_contents = verify_sdist_contents(first["sdist"])
        rebuilt_wheel = build_wheel_from_sdist(first["sdist"], workspace)
        if sha256(rebuilt_wheel) != sha256(first["wheel"]):
            raise RuntimeError("wheel rebuilt from sdist differs from direct wheel")
        installed = verify_installed_wheel(rebuilt_wheel, workspace)
        offline_install = verify_offline_wheelhouse_install(rebuilt_wheel, workspace)
        artifacts: list[dict[str, object]] = []
        for artifact in first.values():
            destination = output / artifact.name
            shutil.copy2(artifact, destination)
            artifacts.append(
                {
                    "path": destination.name,
                    "sha256": sha256(destination),
                    "size": destination.stat().st_size,
                }
            )

    manifest = source_manifest()
    components, dependency_graph = dependency_inventory()
    mutation_report: dict[str, Any] = json.loads(project_checks["mutation_gate"])
    traceability_report: dict[str, Any] = json.loads(project_checks["traceability"])
    evidence: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "external_checks": {
            "openadr_official_schema": "NOT_RUN: official YAML not bundled",
            "docker": "NOT_RUN: external packaging gate deferred",
            "security_advisory_query": "PASS: see security-audit.json",
        },
        "python": sys.version,
        "passed": True,
        "project_checks": project_checks,
        "artifact_reproducibility": comparisons,
        "sdist_rebuild_matches_wheel": True,
        "sdist_file_count": len(sdist_contents),
        "isolated_sdist_install": installed,
        "offline_wheelhouse_install": offline_install,
        "artifacts": artifacts,
        "source_file_count": len(manifest),
        "dependency_count": len(components),
        "dependency_edge_count": sum(len(items) for items in dependency_graph.values()),
        "mutation_gate": {
            "passed": mutation_report["passed"],
            "killed_count": mutation_report["killed_count"],
            "mutation_count": mutation_report["mutation_count"],
            "score_percent": mutation_report["score_percent"],
        },
        "traceability": traceability_report,
        "security_audit": {
            "passed": security_audit["passed"],
            "queried_at": security_audit["queried_at"],
            "tool_version": security_audit["tool_version"],
            "vulnerability_service": security_audit["vulnerability_service"],
            "audited_lock_sha256": security_audit["audited_lock_sha256"],
            "vulnerability_count": security_audit["vulnerability_count"],
        },
    }
    (output / "source-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "sbom.cdx.json").write_text(
        json.dumps(cyclonedx_sbom(components, dependency_graph), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (output / "mutation-report.json").write_text(
        json.dumps(mutation_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "traceability-report.json").write_text(
        json.dumps(traceability_report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "security-audit.json").write_text(
        json.dumps(security_audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    shutil.copy2(RUNTIME_LOCK, output / RUNTIME_LOCK.name)
    shutil.copytree(WHEELHOUSE, output / "wheelhouse")
    (output / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    checksums = "\n".join(
        f"{sha256(path)}  {path.relative_to(output).as_posix()}"
        for path in sorted(item for item in output.rglob("*") if item.is_file())
        if path.name != "SHA256SUMS"
    )
    (output / "SHA256SUMS").write_text(checksums + "\n", encoding="utf-8")
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    evidence = verify(arguments.output)
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
