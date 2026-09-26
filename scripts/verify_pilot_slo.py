"""Exercise the deployed stack through TLS, PostgreSQL and multiple API workers."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx
import psycopg
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "build" / "pilot-slo-report.json"
POSTGRES_IMAGE = (
    "postgres@sha256:"
    "b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24"
)
SLO_MS = {
    "flexibility_p95": 750.0,
    "reservation_p95": 1_500.0,
    "activation_p95": 1_500.0,
    "dependency_failure_detection": 6_500.0,
}


@dataclass(frozen=True)
class Sample:
    operation: str
    status: int
    duration_ms: float


@dataclass(frozen=True)
class Product:
    zone_id: str
    interval_start: datetime
    interval_end: datetime
    consequence_type: str
    capacity_kw: float


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile without samples")
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be in (0, 1]")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def summarize(samples: list[Sample], operation: str) -> dict[str, object]:
    selected = [sample for sample in samples if sample.operation == operation]
    successful = [sample.duration_ms for sample in selected if sample.status < 400]
    if not selected or not successful:
        raise RuntimeError(f"{operation} produced no successful samples")
    return {
        "requests": len(selected),
        "successful": len(successful),
        "statuses": {
            str(status): sum(sample.status == status for sample in selected)
            for status in sorted({sample.status for sample in selected})
        },
        "p50_ms": round(percentile(successful, 0.50), 3),
        "p95_ms": round(percentile(successful, 0.95), 3),
        "p99_ms": round(percentile(successful, 0.99), 3),
        "max_ms": round(max(successful), 3),
    }


def reserve_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


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
            f"command failed ({result.returncode}): {command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def generate_certificate(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path = directory / "tls-key.pem"
    certificate_path = directory / "tls-cert.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return key_path, certificate_path


def start_process(command: list[str], env: dict[str, str], log: Any) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env={**os.environ, **env},
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        process.wait(timeout=10)
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def wait_for_postgres(container: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", container, "pg_isready", "-U", "der_flex", "-d", "der_flex"],
            capture_output=True,
        )
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("PostgreSQL did not become ready within 30 seconds")


def wait_for_api(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 45
    with httpx.Client(verify=False, timeout=2) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"API process exited with {process.returncode}")
            try:
                if client.get(f"{base_url}/health/ready").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise RuntimeError("API did not become ready within 45 seconds")


def select_product(dsn: str) -> Product:
    with psycopg.connect(dsn) as connection:
        row = connection.execute(
            """
            SELECT zone_id, interval_start, interval_end, consequence_type,
                   SUM(upward_capacity_kw) AS capacity_kw
            FROM der_flex_offers
            WHERE tenant_id = 'development'
            GROUP BY zone_id, interval_start, interval_end, consequence_type
            HAVING COUNT(*) >= 10
            ORDER BY interval_start, zone_id, consequence_type
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("the deployed demo did not seed an eligible product")
    return Product(
        zone_id=str(row[0]),
        interval_start=row[1],
        interval_end=row[2],
        consequence_type=str(row[3]),
        capacity_kw=float(row[4]),
    )


async def timed_request(
    client: httpx.AsyncClient,
    operation: str,
    method: str,
    path: str,
    **kwargs: Any,
) -> tuple[Sample, httpx.Response]:
    started = time.perf_counter()
    response = await client.request(method, path, **kwargs)
    duration_ms = (time.perf_counter() - started) * 1_000
    return Sample(operation, response.status_code, duration_ms), response


async def exercise_workload(
    base_url: str, product: Product
) -> tuple[list[Sample], list[str], object, int]:
    samples: list[Sample] = []
    reservation_ids: list[str] = []
    params = {
        "zone_id": product.zone_id,
        "from": product.interval_start.isoformat(),
        "to": product.interval_end.isoformat(),
    }
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=16)
    async with httpx.AsyncClient(
        base_url=base_url,
        verify=False,
        timeout=10,
        limits=limits,
    ) as client:
        warmup = await client.get("/health/ready")
        warmup.raise_for_status()

        query_limit = asyncio.Semaphore(4)

        async def bounded_query() -> tuple[Sample, httpx.Response]:
            async with query_limit:
                return await timed_request(
                    client,
                    "flexibility",
                    "GET",
                    "/api/v1/flexibility",
                    params=params,
                )

        query_tasks = [
            bounded_query() for _ in range(24)
        ]
        query_results = await asyncio.gather(*query_tasks)
        samples.extend(sample for sample, _response in query_results)
        if any(response.status_code != 200 for _sample, response in query_results):
            raise RuntimeError("the sustained flexibility workload returned an error")
        snapshots = [response.json() for _sample, response in query_results]
        if any(snapshot != snapshots[0] for snapshot in snapshots[1:]):
            raise RuntimeError("sticky privacy snapshots changed during the load window")

        payload = {
            "zone_id": product.zone_id,
            "interval_start": product.interval_start.isoformat(),
            "interval_end": product.interval_end.isoformat(),
            "direction": "UPWARD",
            "power_kw": 0.1,
            "consequence_type": product.consequence_type,
        }
        burst_tasks = [
            timed_request(
                client,
                "reservation",
                "POST",
                "/api/v1/reservations",
                headers={"Idempotency-Key": f"pilot-slo-{uuid.uuid4()}"},
                json=payload,
            )
            for _ in range(32)
        ]
        burst_results = await asyncio.gather(*burst_tasks)
        samples.extend(sample for sample, _response in burst_results)
        shed_count = sum(response.status_code == 503 for _sample, response in burst_results)
        for _sample, response in burst_results:
            if response.status_code == 201:
                reservation_ids.append(str(response.json()["reservation_id"]))
            elif response.status_code != 503:
                raise RuntimeError(f"unexpected reservation response {response.status_code}")
        if shed_count == 0:
            raise RuntimeError("the deployed admission-control gate was not exercised")

        while len(reservation_ids) < 16:
            sample, response = await timed_request(
                client,
                "reservation",
                "POST",
                "/api/v1/reservations",
                headers={"Idempotency-Key": f"pilot-slo-retry-{uuid.uuid4()}"},
                json=payload,
            )
            samples.append(sample)
            if response.status_code == 201:
                reservation_ids.append(str(response.json()["reservation_id"]))
            elif response.status_code != 503:
                raise RuntimeError(f"reservation retry returned {response.status_code}")
            await asyncio.sleep(0.02)

        activation_tasks = [
            timed_request(
                client,
                "activation",
                "POST",
                f"/api/v1/reservations/{reservation_id}/activate",
            )
            for reservation_id in reservation_ids[:8]
        ]
        activation_results = await asyncio.gather(*activation_tasks)
        samples.extend(sample for sample, _response in activation_results)
        if any(response.status_code != 200 for _sample, response in activation_results):
            raise RuntimeError("an activation failed during the concurrent workload")

        _sample, after = await timed_request(
            client, "flexibility", "GET", "/api/v1/flexibility", params=params
        )
        if after.status_code != 200 or after.json() != snapshots[0]:
            raise RuntimeError("load changed the public snapshot inside its fixed epoch")

    return samples, reservation_ids, snapshots[0], shed_count


def outbox_state(dsn: str) -> dict[str, int]:
    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            "SELECT status, COUNT(*) FROM der_flex_outbox GROUP BY status ORDER BY status"
        ).fetchall()
    return {str(status): int(count) for status, count in rows}


def wait_for_outbox(dsn: str) -> dict[str, int]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = outbox_state(dsn)
        if sum(state.get(status, 0) for status in ("PENDING", "PROCESSING", "RETRY")) == 0:
            return state
        time.sleep(0.1)
    raise RuntimeError("outbox backlog did not drain within 30 seconds")


def verify_database_invariants(dsn: str, capacity_kw: float) -> dict[str, object]:
    with psycopg.connect(dsn) as connection:
        allocated_row = connection.execute(
                """
                SELECT COALESCE(SUM(allocated_power_kw), 0)
                FROM der_flex_reservations
                WHERE status IN ('CONFIRMED', 'ACTIVATED', 'COMPLETED')
                """
            ).fetchone()
        duplicate_row = connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT event_id FROM der_flex_outbox
                    GROUP BY event_id HAVING COUNT(*) > 1
                ) duplicates
                """
            ).fetchone()
        pending_row = connection.execute(
                "SELECT COUNT(*) FROM der_flex_activations WHERE status = 'PENDING'"
            ).fetchone()
    if allocated_row is None or duplicate_row is None or pending_row is None:
        raise RuntimeError("database invariant query returned no row")
    allocated = float(allocated_row[0])
    duplicate_events = int(duplicate_row[0])
    pending_activations = int(pending_row[0])
    if allocated > capacity_kw + 1e-9:
        raise RuntimeError("the load test oversold the selected product")
    if duplicate_events or pending_activations:
        raise RuntimeError("outbox recovery left duplicate events or pending activations")
    return {
        "capacity_kw": capacity_kw,
        "allocated_kw": allocated,
        "oversold": False,
        "duplicate_event_ids": duplicate_events,
        "pending_activations": pending_activations,
    }


def exercise_database_outage(
    container: str, base_url: str, product: Product, expected_snapshot: object
) -> dict[str, object]:
    params = {
        "zone_id": product.zone_id,
        "from": product.interval_start.isoformat(),
        "to": product.interval_end.isoformat(),
    }
    run(["docker", "pause", container])
    try:
        with httpx.Client(base_url=base_url, verify=False, timeout=8) as client:
            live = client.get("/health/live")
            started = time.perf_counter()
            ready = client.get("/health/ready")
            detection_ms = (time.perf_counter() - started) * 1_000
            business = client.get("/api/v1/flexibility", params=params)
    finally:
        run(["docker", "unpause", container], check=False)
    if live.status_code != 200 or ready.status_code != 503 or business.status_code != 503:
        raise RuntimeError("database outage did not produce live=200 and dependency 503 responses")
    wait_for_postgres(container)
    deadline = time.monotonic() + 30
    with httpx.Client(base_url=base_url, verify=False, timeout=5) as client:
        while time.monotonic() < deadline:
            recovered_ready = client.get("/health/ready")
            if recovered_ready.status_code == 200:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("API did not recover after PostgreSQL resumed")
        recovered = client.get("/api/v1/flexibility", params=params)
        metrics = client.get("/metrics")
    if recovered.status_code != 200 or recovered.json() != expected_snapshot:
        raise RuntimeError("privacy snapshot was not stable after dependency recovery")
    required_metrics = {
        "der_flex_http_request_duration_milliseconds",
        "der_flex_http_in_flight_requests",
        "der_flex_http_shed_total",
    }
    missing_metrics = sorted(item for item in required_metrics if item not in metrics.text)
    if missing_metrics:
        raise RuntimeError(f"deployed metrics are incomplete: {missing_metrics}")
    return {
        "live_status": live.status_code,
        "ready_status": ready.status_code,
        "business_status": business.status_code,
        "detection_ms": round(detection_ms, 3),
        "recovered_status": recovered.status_code,
        "snapshot_stable": True,
    }


def assert_slos(report: dict[str, Any]) -> None:
    operations = report["operations"]
    checks = {
        "flexibility_p95": float(operations["flexibility"]["p95_ms"]),
        "reservation_p95": float(operations["reservation"]["p95_ms"]),
        "activation_p95": float(operations["activation"]["p95_ms"]),
        "dependency_failure_detection": float(report["fault_injection"]["detection_ms"]),
    }
    failures = {
        name: {"observed_ms": observed, "limit_ms": SLO_MS[name]}
        for name, observed in checks.items()
        if observed > SLO_MS[name]
    }
    if failures:
        raise RuntimeError(f"pilot SLO gate failed: {failures}")


def verify_pilot_slo(report_path: Path = DEFAULT_REPORT) -> dict[str, object]:
    run(["docker", "version", "--format", "{{.Server.Version}}"])
    suffix = uuid.uuid4().hex[:10]
    container = f"der-flex-slo-{suffix}"
    postgres_port = reserve_port()
    api_port = reserve_port()
    dsn = f"postgresql://der_flex:der_flex_slo@127.0.0.1:{postgres_port}/der_flex"
    base_url = f"https://127.0.0.1:{api_port}"
    processes: list[subprocess.Popen[str]] = []
    paused = False
    with tempfile.TemporaryDirectory(prefix="der-flex-slo-") as temporary:
        temp = Path(temporary)
        key_path, certificate_path = generate_certificate(temp)
        log_path = temp / "processes.log"
        with log_path.open("w", encoding="utf-8") as log:
            try:
                run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--detach",
                        "--name",
                        container,
                        "--env",
                        "POSTGRES_DB=der_flex",
                        "--env",
                        "POSTGRES_USER=der_flex",
                        "--env",
                        "POSTGRES_PASSWORD=der_flex_slo",
                        "--publish",
                        f"127.0.0.1:{postgres_port}:5432",
                        POSTGRES_IMAGE,
                    ]
                )
                wait_for_postgres(container)
                environment = {
                    "DER_FLEX_DATABASE_URL": dsn,
                    "DER_FLEX_AUTH_MODE": "development",
                    "DER_FLEX_MAX_IN_FLIGHT": "4",
                    "DER_FLEX_PROCESS_OUTBOX_ON_ACTIVATE": "0",
                }
                api = start_process(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "der_flex.demo:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(api_port),
                        "--workers",
                        "2",
                        "--ssl-keyfile",
                        str(key_path),
                        "--ssl-certfile",
                        str(certificate_path),
                        "--no-access-log",
                    ],
                    environment,
                    log,
                )
                processes.append(api)
                wait_for_api(base_url, api)
                product = select_product(dsn)
                workload_started = time.perf_counter()
                samples, _reservation_ids, snapshot, shed_count = asyncio.run(
                    exercise_workload(base_url, product)
                )
                workload_seconds = time.perf_counter() - workload_started
                before_workers = outbox_state(dsn)
                if before_workers.get("PENDING", 0) == 0:
                    raise RuntimeError("activation load did not create a durable outbox backlog")
                for _ in range(2):
                    worker = start_process(
                        [
                            sys.executable,
                            "-c",
                            "from der_flex.outbox import main; main()",
                        ],
                        environment,
                        log,
                    )
                    processes.append(worker)
                after_workers = wait_for_outbox(dsn)
                invariants = verify_database_invariants(dsn, product.capacity_kw)
                paused = True
                fault = exercise_database_outage(container, base_url, product, snapshot)
                paused = False
                operations = {
                    operation: summarize(samples, operation)
                    for operation in ("flexibility", "reservation", "activation")
                }
                report: dict[str, Any] = {
                    "schema_version": "1.0",
                    "passed": True,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "topology": {
                        "transport": "TLS with an ephemeral self-signed certificate",
                        "api_workers": 2,
                        "outbox_workers": 2,
                        "database": "PostgreSQL in a separate Linux container",
                        "max_in_flight_per_api_worker": 4,
                    },
                    "slo_limits_ms": SLO_MS,
                    "operations": operations,
                    "workload": {
                        "seconds": round(workload_seconds, 3),
                        "requests": len(samples),
                        "throughput_requests_per_second": round(
                            len(samples) / workload_seconds, 3
                        ),
                        "shed_requests": shed_count,
                    },
                    "outbox": {
                        "before_workers": before_workers,
                        "after_workers": after_workers,
                        "backlog_recovered": True,
                    },
                    "invariants": invariants,
                    "fault_injection": fault,
                    "scope_limits": [
                        "The certificate and identity provider are local test fixtures.",
                        "The workload is a bounded gate, not a capacity forecast.",
                        "Network partitions are represented by a database pause on one host.",
                    ],
                }
                assert_slos(report)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                return report
            except Exception as error:
                log.flush()
                diagnostics = log_path.read_text(encoding="utf-8", errors="replace")[-8_000:]
                raise RuntimeError(
                    f"pilot SLO verification failed: {error}\n{diagnostics}"
                ) from error
            finally:
                if paused:
                    run(["docker", "unpause", container], check=False)
                for process in reversed(processes):
                    stop_process(process)
                run(["docker", "rm", "--force", container], check=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    print(json.dumps(verify_pilot_slo(args.report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
