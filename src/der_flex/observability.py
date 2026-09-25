from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter
from threading import Lock

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("uvicorn.error")


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: Counter[tuple[str, int]] = Counter()

    def observe(self, method: str, status_code: int) -> None:
        with self._lock:
            self._requests[(method, status_code)] += 1

    def render_prometheus(self) -> str:
        lines = [
            "# HELP der_flex_http_requests_total HTTP requests handled.",
            "# TYPE der_flex_http_requests_total counter",
        ]
        with self._lock:
            samples = sorted(self._requests.items())
        lines.extend(
            f'der_flex_http_requests_total{{method="{method}",status="{status}"}} {count}'
            for (method, status), count in samples
        )
        return "\n".join(lines) + "\n"


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
            self.metrics.observe(method, status_code)
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
