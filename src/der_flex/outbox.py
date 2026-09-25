from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from der_flex.adapters.webhooks import WebhookDelivery, WebhookDispatcher

if TYPE_CHECKING:
    from der_flex.reservations.backend import OutboxTask, ReservationBackend

type ResourceAcceptor = Callable[[str, dict[str, object]], bool]


class OutboxProcessor:
    """Claims durable side effects and records every outcome before acknowledging it."""

    def __init__(
        self,
        backend: ReservationBackend,
        webhook_dispatcher: WebhookDispatcher,
        resource_acceptor: ResourceAcceptor,
        *,
        max_attempts: int = 8,
        lease_seconds: int = 30,
    ) -> None:
        self.backend = backend
        self.webhook_dispatcher = webhook_dispatcher
        self.resource_acceptor = resource_acceptor
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds

    def run_once(self, *, now: datetime | None = None, limit: int = 100) -> int:
        current = now or datetime.now(UTC)
        tasks = self.backend.claim_outbox(
            now=current,
            limit=limit,
            lease_seconds=self.lease_seconds,
        )
        for task in tasks:
            delivered, retryable, error = self._deliver(task)
            self.backend.resolve_outbox(
                task,
                delivered=delivered,
                retryable=retryable,
                error=error,
                now=current,
                max_attempts=self.max_attempts,
            )
            terminal = delivered or not retryable or task.attempts >= self.max_attempts
            if task.kind == "WEBHOOK" and terminal:
                self.webhook_dispatcher.deliveries.append(
                    WebhookDelivery(
                        task.event_type,
                        task.destination,
                        task.attempts,
                        delivered,
                    )
                )
        return len(tasks)

    def drain(self, *, now: datetime | None = None, max_batches: int = 10) -> int:
        processed = 0
        for _ in range(max_batches):
            count = self.run_once(now=now)
            processed += count
            if count == 0:
                break
        return processed

    def _deliver(self, task: OutboxTask) -> tuple[bool, bool, str | None]:
        if task.kind == "WEBHOOK":
            try:
                delivered, error = self.webhook_dispatcher.send_once(
                    task.destination,
                    task.event_type,
                    task.payload,
                    event_id=str(task.event_id),
                )
            except Exception as error:  # noqa: BLE001 - lease recovery needs persistence
                return False, True, f"{type(error).__name__}: {error}"
            return delivered, True, error
        try:
            delivered = self.resource_acceptor(task.destination, task.payload)
        except OSError as error:
            return False, True, f"{type(error).__name__}: {error}"
        except Exception as error:  # noqa: BLE001 - worker must persist the failure
            return False, True, f"{type(error).__name__}: {error}"
        return delivered, False, None if delivered else "resource rejected instruction"


def main() -> None:
    from der_flex.reservations.postgres import PostgresReservationBackend

    dsn = os.environ["DER_FLEX_DATABASE_URL"]
    backend = PostgresReservationBackend(dsn)
    backend.initialize()
    processor = OutboxProcessor(backend, WebhookDispatcher(), lambda _resource, _payload: True)
    while True:
        if processor.run_once() == 0:
            time.sleep(1)
