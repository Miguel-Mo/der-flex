from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter, defaultdict
from threading import Lock

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("uvicorn.error")

LATENCY_BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1_000, 2_500, 5_000)


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: Counter[tuple[str, int]] = Counter()
        self._latency_buckets: Counter[tuple[str, int, int]] = Counter()
        self._latency_count: Counter[tuple[str, int]] = Counter()
        self._latency_sum_ms: defaultdict[tuple[str, int], float] = defaultdict(float)
        self._in_flight = 0
        self._shed = 0

    def request_started(self) -> None:
        with self._lock:
            self._in_flight += 1

    def observe(self, method: str, status_code: int, duration_ms: float) -> None:
        key = (method, status_code)
        with self._lock:
            self._in_flight -= 1
            self._requests[key] += 1
            self._latency_count[key] += 1
            self._latency_sum_ms[key] += duration_ms
            for bucket in LATENCY_BUCKETS_MS:
                if duration_ms <= bucket:
                    self._latency_buckets[(method, status_code, bucket)] += 1

    def observe_shed(self) -> None:
        with self._lock:
            self._shed += 1

    def render_prometheus(self) -> str:
        lines = [
            "# HELP der_flex_http_requests_total HTTP requests handled.",
            "# TYPE der_flex_http_requests_total counter",
        ]
        with self._lock:
            samples = sorted(self._requests.items())
            latency_buckets = sorted(self._latency_buckets.items())
            latency_counts = sorted(self._latency_count.items())
            latency_sums = sorted(self._latency_sum_ms.items())
            in_flight = self._in_flight
            shed = self._shed
        lines.extend(
            f'der_flex_http_requests_total{{method="{method}",status="{status}"}} {count}'
            for (method, status), count in samples
        )
        lines.extend(
            [
                "# HELP der_flex_http_request_duration_milliseconds HTTP request latency.",
                "# TYPE der_flex_http_request_duration_milliseconds histogram",
            ]
        )
        lines.extend(
            "der_flex_http_request_duration_milliseconds_bucket"
            f'{{method="{method}",status="{status}",le="{bucket}"}} {count}'
            for (method, status, bucket), count in latency_buckets
        )
        lines.extend(
            "der_flex_http_request_duration_milliseconds_bucket"
            f'{{method="{method}",status="{status}",le="+Inf"}} {count}'
            for (method, status), count in latency_counts
        )
        lines.extend(
            "der_flex_http_request_duration_milliseconds_sum"
            f'{{method="{method}",status="{status}"}} {total:.3f}'
            for (method, status), total in latency_sums
        )
        lines.extend(
            "der_flex_http_request_duration_milliseconds_count"
            f'{{method="{method}",status="{status}"}} {count}'
            for (method, status), count in latency_counts
        )
        lines.extend(
            [
                "# HELP der_flex_http_in_flight_requests Current HTTP requests.",
                "# TYPE der_flex_http_in_flight_requests gauge",
                f"der_flex_http_in_flight_requests {in_flight}",
                "# HELP der_flex_http_shed_total Requests rejected by admission control.",
                "# TYPE der_flex_http_shed_total counter",
                f"der_flex_http_shed_total {shed}",
            ]
        )
        return "\n".join(lines) + "\n"


class ConcurrencyLimitMiddleware:
    """Reject excess work immediately while leaving health and metrics observable."""

    _BYPASS_PATHS = frozenset({"/health/live", "/health/ready", "/metrics"})

    def __init__(self, app: ASGIApp, metrics: MetricsRegistry, max_in_flight: int) -> None:
        if max_in_flight < 1:
            raise ValueError("max_in_flight must be positive")
        self.app = app
        self.metrics = metrics
        self.max_in_flight = max_in_flight
        self._lock = Lock()
        self._active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._BYPASS_PATHS:
            await self.app(scope, receive, send)
            return
        with self._lock:
            admitted = self._active < self.max_in_flight
            if admitted:
                self._active += 1
        if not admitted:
            self.metrics.observe_shed()
            response = JSONResponse(
                status_code=503,
                headers={"Retry-After": "1"},
                media_type="application/problem+json",
                content={
                    "type": "about:blank",
                    "title": "Service overloaded",
                    "status": 503,
                    "detail": "request concurrency limit reached",
                },
            )
            await response(scope, _empty_receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            with self._lock:
                self._active -= 1


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = 32_768) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value for name, value in scope["headers"] if name == b"content-length"), None
        )
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                await self._problem(scope, send, 400, "Invalid Content-Length")
                return
            if declared_size > self.max_bytes:
                await self._problem(scope, send, 413, "Request body is too large")
                return

        received = 0
        buffered: list[Message] = []
        more_body = True
        while more_body:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    await self._problem(scope, send, 413, "Request body is too large")
                    return
                more_body = bool(message.get("more_body", False))
            else:
                more_body = False

        position = 0

        async def replay_receive() -> Message:
            nonlocal position
            if position < len(buffered):
                message = buffered[position]
                position += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _problem(scope: Scope, send: Send, status_code: int, detail: str) -> None:
        response = JSONResponse(
            status_code=status_code,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "Invalid request",
                "status": status_code,
                "detail": detail,
            },
        )
        await response(scope, _empty_receive, send)


class StructuredLoggingMiddleware:
    def __init__(self, app: ASGIApp, metrics: MetricsRegistry) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        started = time.perf_counter()
        status_code = 500
        self.metrics.request_started()

        async def capture_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            method = str(scope.get("method", "UNKNOWN"))
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            self.metrics.observe(method, status_code, duration_ms)
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": method,
                        "status": status_code,
                        "duration_ms": duration_ms,
                    },
                    separators=(",", ":"),
                )
            )


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}
