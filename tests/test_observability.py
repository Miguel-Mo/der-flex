from __future__ import annotations

import asyncio

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from der_flex.api import create_app
from der_flex.domain import InMemoryOfferStore
from der_flex.observability import ConcurrencyLimitMiddleware, MetricsRegistry


def test_metrics_publish_latency_saturation_and_anonymous_labels() -> None:
    metrics = MetricsRegistry()
    metrics.request_started()
    metrics.observe("GET", 200, 12.5)
    metrics.observe_shed()

    payload = metrics.render_prometheus()

    assert 'der_flex_http_requests_total{method="GET",status="200"} 1' in payload
    assert (
        'der_flex_http_request_duration_milliseconds_bucket'
        '{method="GET",status="200",le="25"} 1'
    ) in payload
    assert (
        'der_flex_http_request_duration_milliseconds_sum'
        '{method="GET",status="200"} 12.500'
    ) in payload
    assert "der_flex_http_in_flight_requests 0" in payload
    assert "der_flex_http_shed_total 1" in payload
    assert "resource_id" not in payload
    assert "tenant_id" not in payload


def test_concurrency_limit_sheds_without_blocking_health_or_metrics() -> None:
    asyncio.run(_exercise_concurrency_limit())


async def _exercise_concurrency_limit() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    metrics = MetricsRegistry()

    async def app(_scope: Scope, _receive: Receive, send: Send) -> None:
        started.set()
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = ConcurrencyLimitMiddleware(app, metrics, max_in_flight=1)
    first_messages: list[Message] = []
    first_send = _collector(first_messages)
    first = asyncio.create_task(
        middleware(_scope("/api/v1/flexibility"), _receive, first_send)
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    shed_messages: list[Message] = []
    await middleware(
        _scope("/api/v1/reservations"),
        _receive,
        _collector(shed_messages),
    )
    assert shed_messages[0]["status"] == 503
    assert (b"retry-after", b"1") in shed_messages[0]["headers"]

    health_messages: list[Message] = []
    health = asyncio.create_task(
        middleware(_scope("/health/ready"), _receive, _collector(health_messages))
    )
    await asyncio.sleep(0)
    assert not health.done()

    release.set()
    await asyncio.gather(first, health)
    assert first_messages[0]["status"] == 200
    assert health_messages[0]["status"] == 200
    assert "der_flex_http_shed_total 1" in metrics.render_prometheus()


def test_concurrency_limit_rejects_invalid_capacity() -> None:
    async def app(_scope: Scope, _receive: Receive, _send: Send) -> None:
        return None

    with pytest.raises(ValueError, match="positive"):
        ConcurrencyLimitMiddleware(app, MetricsRegistry(), max_in_flight=0)


def test_database_errors_are_sanitized_as_retryable_unavailability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryOfferStore(minimum_participants=10)

    def unavailable(_tenant_id: str, _window_start: object, _limit: int) -> bool:
        raise psycopg.OperationalError("password=secret host=private.internal")

    monkeypatch.setattr(store, "consume_privacy_query", unavailable)
    with TestClient(create_app(store=store)) as client:
        response = client.get("/api/v1/flexibility/zones")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json() == {"detail": "service dependency unavailable"}
    assert "secret" not in response.text
    assert "private.internal" not in response.text


def _scope(path: str) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 443),
    }


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


def _collector(messages: list[Message]) -> Send:
    async def send(message: Message) -> None:
        messages.append(message)

    return send
