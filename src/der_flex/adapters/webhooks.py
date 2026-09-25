from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

type WebhookSender = Callable[[str, bytes, dict[str, str]], int]


@dataclass(frozen=True)
class WebhookDelivery:
    event: str
    callback_url: str
    attempts: int
    delivered: bool


def _http_sender(url: str, body: bytes, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirectHandler)
    with opener.open(request, timeout=3) as response:  # noqa: S310
        return int(response.status)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


class WebhookDispatcher:
    def __init__(
        self,
        *,
        secret: str | None = None,
        max_attempts: int = 3,
        sender: WebhookSender = _http_sender,
    ) -> None:
        configured_secret = secret or os.getenv("DER_FLEX_WEBHOOK_SECRET")
        self.secret = configured_secret.encode() if configured_secret else secrets.token_bytes(32)
        self.max_attempts = max_attempts
        self.sender = sender
        self.deliveries: list[WebhookDelivery] = []

    def send_once(
        self,
        callback_url: str,
        event: str,
        payload: dict[str, object],
        *,
        event_id: str | None = None,
    ) -> tuple[bool, str | None]:
        body = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
        signature = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-DER-Flex-Event": event,
            "X-DER-Flex-Signature": f"sha256={signature}",
        }
        if event_id:
            headers["X-DER-Flex-Event-ID"] = event_id
        try:
            status = self.sender(callback_url, body, headers)
        except OSError as error:
            return False, f"{type(error).__name__}: {error}"
        if 200 <= status < 300:
            return True, None
        return False, f"HTTP {status}"

    def deliver(self, callback_url: str, event: str, payload: dict[str, object]) -> None:
        delivered = False
        attempts = 0
        for attempt_number in range(1, self.max_attempts + 1):
            attempts = attempt_number
            delivered, _error = self.send_once(callback_url, event, payload)
            if delivered:
                break
        self.deliveries.append(WebhookDelivery(event, callback_url, attempts, delivered))
