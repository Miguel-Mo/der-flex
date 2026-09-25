from __future__ import annotations

from datetime import UTC, datetime, timedelta

import uvicorn
from fastapi import FastAPI

from der_flex.adapters.s2 import normalize_pebc_offer
from der_flex.api import create_app
from der_flex.domain import InMemoryOfferStore
from der_flex.simulators import build_demo_fleet, build_simulator_registry


def build_demo_app() -> FastAPI:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    start = now + timedelta(minutes=15 - now.minute % 15)
    store = InMemoryOfferStore(minimum_participants=10)
    fleet = build_demo_fleet(per_profile_per_zone=12)
    resource_registry = build_simulator_registry(fleet)
    for resource in fleet:
        constraints, forecast = resource.s2_offer_messages(start, interval_count=4)
        for offer in normalize_pebc_offer(
            resource_id=resource.resource_id,
            zone_id=resource.zone_id,
            constraints_message=constraints,
            forecast_message=forecast,
            resource_registry=resource_registry,
            source_epoch=resource.source_epoch,
            source_sequence=resource.source_sequence,
            observed_at=now,
            received_at=now,
        ):
            store.upsert(offer)
    app = create_app(store)
    app.state.demo_interval = (start, start + timedelta(hours=1))
    return app


app = build_demo_app()


def main() -> None:
    start, end = app.state.demo_interval
    print("Demo zone: ES-MA-29700")
    print(f"Demo interval: {start.isoformat()} to {end.isoformat()}")
    print("Swagger UI: http://127.0.0.1:8000/docs")
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)


if __name__ == "__main__":
    main()
