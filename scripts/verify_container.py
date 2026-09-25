"""Build and exercise the DER Flex production container, then record PKG01 evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_release import DEFAULT_OUTPUT, ROOT, sha256

IMAGE_TAG = "der-flex:pkg01-v6.0"


def run(command: list[str], *, check: bool = True) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({' '.join(command)}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout.strip() if result.returncode == 0 else ""


def validate_probe(probe: dict[str, Any], pip_check: str) -> None:
    if probe.get("version") != "0.2.0":
        raise RuntimeError("container installed an unexpected DER Flex version")
    location = str(probe.get("location", "")).replace("\\", "/")
    if "/site-packages/der_flex/__init__.py" not in location:
        raise RuntimeError("DER Flex was not imported from site-packages")
    if probe.get("ready") != {"status": "ready"}:
        raise RuntimeError("container readiness endpoint did not pass")
    if probe.get("uid") != 10001:
        raise RuntimeError("container is not running as the dedicated unprivileged user")
    if probe.get("source_tree_present") is not False:
        raise RuntimeError("source tree leaked into the runtime image")
    if probe.get("root_filesystem_write_blocked") is not True:
        raise RuntimeError("container root filesystem is not read-only")
    if "No broken requirements found" not in pip_check:
        raise RuntimeError("pip check did not pass inside the container")


def verify_container() -> dict[str, Any]:
    server = json.loads(run(["docker", "info", "--format", "{{json .}}"]))
    if server.get("OSType") != "linux":
        raise RuntimeError("PKG01 requires a Linux Docker engine")

    run(["docker", "build", "--tag", IMAGE_TAG, "."])
    container_name = f"der-flex-pkg01-{uuid4().hex[:12]}"
    container_id = run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            container_name,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            IMAGE_TAG,
        ]
    )
    probe_code = (
        "import importlib.metadata as m\n"
        "import json, os, pathlib, urllib.request, der_flex\n"
        "blocked = False\n"
        "try:\n"
        "    pathlib.Path('/app/.write-probe').write_text('unexpected')\n"
        "except OSError:\n"
        "    blocked = True\n"
        "print(json.dumps({'version': m.version('der-flex'), "
        "'location': str(pathlib.Path(der_flex.__file__).resolve()), "
        "'ready': json.load(urllib.request.urlopen("
        "'http://localhost:8000/health/ready')), "
        "'uid': os.getuid(), 'source_tree_present': pathlib.Path('/app/src').exists(), "
        "'root_filesystem_write_blocked': blocked}))"
    )
    try:
        probe_text = ""
        for _ in range(30):
            probe_text = run(
                ["docker", "exec", container_name, "python", "-c", probe_code],
                check=False,
            )
            if probe_text:
                break
            time.sleep(1)
        if not probe_text:
            raise RuntimeError("container did not become ready within 30 seconds")
        probe = json.loads(probe_text)
        pip_check = run(["docker", "exec", container_name, "python", "-m", "pip", "check"])
        image = json.loads(run(["docker", "image", "inspect", IMAGE_TAG]))[0]
        container = json.loads(run(["docker", "inspect", container_name]))[0]
        validate_probe(probe, pip_check)
        host_config = container["HostConfig"]
        if host_config.get("ReadonlyRootfs") is not True:
            raise RuntimeError("Docker did not enable a read-only root filesystem")
        if "ALL" not in (host_config.get("CapDrop") or []):
            raise RuntimeError("Docker did not drop all Linux capabilities")
        if "no-new-privileges:true" not in (host_config.get("SecurityOpt") or []):
            raise RuntimeError("Docker did not enable no-new-privileges")
        if image["Config"].get("User") != "10001:10001":
            raise RuntimeError("image does not declare the unprivileged runtime user")
        if not image["Config"].get("Healthcheck"):
            raise RuntimeError("image does not declare a healthcheck")
    finally:
        run(["docker", "stop", container_name], check=False)

    return {
        "schema_version": "1.0",
        "passed": True,
        "verified_at": datetime.now(UTC).isoformat(),
        "docker_server": {
            "os": server["OSType"],
            "architecture": server["Architecture"],
            "version": server["ServerVersion"],
        },
        "image": {
            "tag": IMAGE_TAG,
            "id": image["Id"],
            "os": image["Os"],
            "architecture": image["Architecture"],
            "size": image["Size"],
        },
        "container_id": container_id,
        "dockerfile_sha256": sha256(ROOT / "Dockerfile"),
        "installed": probe,
        "pip_check": pip_check,
        "runtime_security": {
            "user": image["Config"]["User"],
            "read_only_root_filesystem": container["HostConfig"]["ReadonlyRootfs"],
            "capabilities_dropped": container["HostConfig"]["CapDrop"],
            "security_options": container["HostConfig"]["SecurityOpt"],
            "healthcheck_declared": True,
            "source_tree_present": probe["source_tree_present"],
        },
    }


def build_offline_provenance(
    evidence: dict[str, Any], report: dict[str, Any], output: Path
) -> dict[str, Any]:
    """Create an unsigned, content-addressed offline provenance statement."""

    subjects = [
        {"name": item["path"], "sha256": item["sha256"], "size": item["size"]}
        for item in evidence["artifacts"]
    ]
    subjects.append(
        {"name": report["image"]["tag"], "digest": report["image"]["id"]}
    )
    return {
        "schema_version": "1.0",
        "predicate_type": "der-flex/offline-content-provenance/v1",
        "created_at": report["verified_at"],
        "scope": "offline local verification without public identity or timestamp authority",
        "subjects": subjects,
        "materials": {
            "source_manifest_sha256": sha256(output / "source-manifest.json"),
            "runtime_lock_sha256": sha256(output / "requirements-runtime.lock"),
            "dockerfile_sha256": report["dockerfile_sha256"],
        },
        "verification": {
            "release_passed": evidence["passed"],
            "container_passed": report["passed"],
            "traceability_claim_count": evidence["traceability"]["claim_count"],
        },
        "signed": False,
        "limitations": [
            "No public repository, signed tag, CI identity or trusted timestamp.",
            "Integrity is content-addressed; authorship is not cryptographically asserted.",
            "The image digest identifies the tested build; byte-for-byte reproducible "
            "Docker image construction is not claimed.",
        ],
    }


def record_report(report: dict[str, Any], output: Path) -> None:
    evidence_path = output / "evidence.json"
    if not evidence_path.is_file():
        raise RuntimeError("run verify_release.py before verify_container.py")
    (output / "container-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["external_checks"]["docker"] = (
        "PASS_WITH_SCOPE_LIMIT: local Linux engine; see container-report.json"
    )
    evidence["container_validation"] = {
        "passed": True,
        "verified_at": report["verified_at"],
        "image_id": report["image"]["id"],
        "version": report["installed"]["version"],
        "location": report["installed"]["location"],
        "runtime_security": report["runtime_security"],
    }
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    provenance = build_offline_provenance(evidence, report, output)
    (output / "offline-provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    checksums = "\n".join(
        f"{sha256(path)}  {path.relative_to(output).as_posix()}"
        for path in sorted(item for item in output.rglob("*") if item.is_file())
        if path.name != "SHA256SUMS"
    )
    (output / "SHA256SUMS").write_text(checksums + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    report = verify_container()
    record_report(report, arguments.output)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
