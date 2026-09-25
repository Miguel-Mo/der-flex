"""Repeatable end-to-end HTTP performance check for the documented MVP target."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from datetime import UTC, datetime, timedelta

import httpx2 as httpx

from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.api import create_app
from der_flex.domain import InMemoryOfferStore
from der_flex.simulators import ZONES, build_demo_fleet, build_simulator_registry


async def run_benchmark(
    resources_per_profile_zone: int, intervals: int, queries: int
) -> tuple[int, int, int, float, float]:
    start = datetime(2030, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=15 * intervals)
    fleet = build_demo_fleet(resources_per_profile_zone)
    resource_registry = build_simulator_registry(fleet)
    store = InMemoryOfferStore(minimum_participants=10)

    load_started = time.perf_counter()
    for resource in fleet:
        constraints, forecast = resource.s2_offer_messages(start, interval_count=intervals)
        for offer in normalize_pebc_offer(
            resource_id=resource.resource_id,
            zone_id=resource.zone_id,
            constraints_message=constraints,
            forecast_message=forecast,
            resource_registry=resource_registry,
            source_epoch=resource.source_epoch,
            source_sequence=resource.source_sequence,
            observed_at=start,
            received_at=start,
        ):
            store.upsert(offer)
    load_seconds = time.perf_counter() - load_started

    durations_ms: list[float] = []
    aggregates: list[object] = []
    transport = httpx.ASGITransport(app=create_app(store=store))
    async with httpx.AsyncClient(transport=transport, base_url="http://benchmark") as client:
        params = {
            "zone_id": ZONES[0],
            "from": start.isoformat(),
            "to": end.isoformat(),
        }
        for _ in range(queries):
            query_started = time.perf_counter()
            response = await client.get("/api/v1/flexibility", params=params)
            response.raise_for_status()
            aggregates = response.json()["data"]
            durations_ms.append((time.perf_counter() - query_started) * 1000)

    p95_ms = statistics.quantiles(durations_ms, n=20)[18]
    return len(fleet), len(fleet) * intervals, len(aggregates), load_seconds, p95_ms


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources-per-profile-zone", type=int, default=112)
    parser.add_argument("--intervals", type=int, default=96)
    parser.add_argument("--queries", type=int, default=20)
    args = parser.parse_args()
    if args.queries < 2:
        parser.error("--queries must be at least 2")

    resource_count, offer_count, interval_count, load_seconds, p95_ms = asyncio.run(
        run_benchmark(args.resources_per_profile_zone, args.intervals, args.queries)
    )
    print(
        f"resources={resource_count} offers={offer_count} intervals={interval_count} "
        f"load_s={load_seconds:.3f} http_query_p95_ms={p95_ms:.3f}"
    )
    if p95_ms >= 500:
        raise SystemExit("documented query p95 target was not met")


if __name__ == "__main__":
    main()
