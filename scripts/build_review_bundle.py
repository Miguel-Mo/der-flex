"""Build the deterministic, self-contained DER Flex external-review bundle."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_release import (  # noqa: E402
    DEFAULT_OUTPUT,
    ROOT,
    SOURCE_DATE_EPOCH,
    deterministic_gzip,
    sha256,
)

BUNDLE_NAME = "der-flex-0.2.0-review-bundle-v8.0"
DEFAULT_BUNDLE = ROOT / "build" / f"{BUNDLE_NAME}.zip"
DEFAULT_TAR = ROOT / "build" / f"{BUNDLE_NAME}.tar.gz"
PILOT_SLO_REPORT = ROOT / "build" / "pilot-slo-report.json"


def write_deterministic_zip(
    source: Path, destination: Path, *, prefix: str | None = BUNDLE_NAME
) -> None:
    timestamp = datetime.fromtimestamp(int(SOURCE_DATE_EPOCH), tz=UTC)
    zip_time = (timestamp.year, timestamp.month, timestamp.day, timestamp.hour, 0, 0)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative_path = path.relative_to(source)
            relative = (
                (Path(prefix) / relative_path).as_posix()
                if prefix
                else relative_path.as_posix()
            )
            info = zipfile.ZipInfo(relative, zip_time)
            info.create_system = 0
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def write_deterministic_tar(source: Path, destination: Path) -> None:
    epoch = int(SOURCE_DATE_EPOCH)
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = (Path(BUNDLE_NAME) / path.relative_to(source)).as_posix()
            info = tarfile.TarInfo(relative)
            info.size = path.stat().st_size
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = epoch
            with path.open("rb") as stream:
                archive.addfile(info, stream)
    destination.write_bytes(deterministic_gzip(tar_buffer.getvalue(), mtime=epoch))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_bundle(
    destination: Path = DEFAULT_BUNDLE, tar_destination: Path = DEFAULT_TAR
) -> dict[str, object]:
    evidence = load_json(DEFAULT_OUTPUT / "evidence.json")
    source_manifest = load_json(DEFAULT_OUTPUT / "source-manifest.json")
    if evidence.get("passed") is not True:
        raise RuntimeError("release evidence must pass before bundling")
    if not PILOT_SLO_REPORT.is_file():
        raise RuntimeError("pilot SLO report must be generated before bundling")
    pilot_slo = load_json(PILOT_SLO_REPORT)
    if pilot_slo.get("passed") is not True:
        raise RuntimeError("pilot SLO evidence must pass before bundling")
    for entry in source_manifest:
        source = ROOT / entry["path"]
        if (
            not source.is_file()
            or source.stat().st_size != entry["size"]
            or sha256(source) != entry["sha256"]
        ):
            raise RuntimeError(f"release evidence is stale for {entry['path']}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="der-flex-bundle-", dir=destination.parent
    ) as temporary:
        stage = Path(temporary) / "payload"
        stage.mkdir()
        for entry in source_manifest:
            source = ROOT / entry["path"]
            target = stage / "source" / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        shutil.copytree(DEFAULT_OUTPUT, stage / "evidence")
        shutil.copy2(PILOT_SLO_REPORT, stage / "evidence" / PILOT_SLO_REPORT.name)
        top_level = {
            "START-HERE.md": ROOT / "docs" / "reviewer-start-here.md",
            "REVIEW-PROMPT.md": ROOT / "docs" / "external-review-prompt-v4.md",
            "EXPECTED-RESULTS.md": ROOT / "docs" / "expected-results-v4.md",
            "TRACEABILITY.md": ROOT / "docs" / "traceability.md",
            "verify_bundle.py": ROOT / "scripts" / "verify_review_bundle.py",
        }
        for name, source in top_level.items():
            shutil.copy2(source, stage / name)
        manifest = {
            "schema_version": "1.0",
            "bundle": BUNDLE_NAME,
            "package_version": "0.2.0",
            "purpose": "independent adversarial review without website access",
            "source_file_count": len(source_manifest),
            "release_artifacts": evidence["artifacts"],
            "external_not_run": evidence["external_checks"],
            "entrypoint": "START-HERE.md",
            "verifier": "verify_bundle.py",
        }
        (stage / "BUNDLE-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        files = sorted(item for item in stage.rglob("*") if item.is_file())
        (stage / "BUNDLE-SHA256SUMS").write_text(
            "\n".join(
                f"{sha256(path)}  {path.relative_to(stage).as_posix()}" for path in files
            )
            + "\n",
            encoding="utf-8",
        )
        temporary_tar = Path(temporary) / tar_destination.name
        write_deterministic_tar(stage, temporary_tar)
        shutil.copy2(temporary_tar, tar_destination)

        transport = Path(temporary) / "transport"
        transport.mkdir()
        shutil.copy2(temporary_tar, transport / temporary_tar.name)
        (transport / "TAR-SHA256.txt").write_text(
            f"{sha256(temporary_tar)}  {temporary_tar.name}\n", encoding="utf-8"
        )
        (transport / "START-HERE.txt").write_text(
            "DER Flex review bundle v8.0 transport container.\n"
            f"1. Verify TAR-SHA256.txt.\n2. Extract {temporary_tar.name}.\n"
            "3. Enter the extracted directory.\n4. Run: python verify_bundle.py\n"
            "The TAR is canonical; this ZIP intentionally has only three files to avoid "
            "upload systems filtering nested source files.\n",
            encoding="utf-8",
        )
        temporary_zip = Path(temporary) / destination.name
        write_deterministic_zip(transport, temporary_zip, prefix=None)
        shutil.copy2(temporary_zip, destination)
    return {
        "zip_path": str(destination),
        "zip_sha256": sha256(destination),
        "zip_size": destination.stat().st_size,
        "tar_path": str(tar_destination),
        "tar_sha256": sha256(tar_destination),
        "tar_size": tar_destination.stat().st_size,
        "source_file_count": len(source_manifest),
    }


def main() -> None:
    print(json.dumps(build_bundle(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
