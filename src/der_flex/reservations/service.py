from __future__ import annotations

import hashlib
import json
import math
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from der_flex.adapters.s2.dispatch import build_pebc_instruction
from der_flex.adapters.webhooks import WebhookDispatcher
from der_flex.domain.models import (
    Activation,
    ConsequenceType,
    FlexibilityAggregate,
    FlexibilityDirection,
    FlexibilityOffer,
    Reservation,
)
from der_flex.domain.store import InMemoryOfferStore

MAX_RESERVATION_POWER_KW = 1_000_000.0


class ReservationError(Exception):
    """Base exception for expected reservation failures."""


class InsufficientCapacity(ReservationError):
    pass


class IdempotencyConflict(ReservationError):
    pass


class ReservationNotFound(ReservationError):
    pass


class ReservationStateConflict(ReservationError):
    pass


@dataclass(frozen=True)
class Allocation:
    resource_id: str
    power_kw: float
    baseline_power_kw: float
    source_version: str


class ReservationService:
    def __init__(
        self,
        offer_store: InMemoryOfferStore,
        *,
        webhook_dispatcher: WebhookDispatcher | None = None,
        resource_acceptor: Callable[[str, dict[str, object]], bool] | None = None,
    ) -> None:
        self.offer_store = offer_store
        self._lock = threading.RLock()
        self._reservations: dict[uuid.UUID, Reservation] = {}
        self._allocations: dict[uuid.UUID, tuple[Allocation, ...]] = {}
        self._idempotency: dict[str, tuple[str, uuid.UUID]] = {}
        self._activations: dict[uuid.UUID, Activation] = {}
        self._activation_by_reservation: dict[uuid.UUID, uuid.UUID] = {}
        self._instructions: dict[uuid.UUID, tuple[dict[str, object], ...]] = {}
        self._callbacks: dict[uuid.UUID, str] = {}
        self._published_residuals: dict[
            tuple[str, datetime, datetime, ConsequenceType], tuple[float, float, float, float]
        ] = {}
        self._suppressed_public_cells: set[
            tuple[str, datetime, datetime, ConsequenceType]
        ] = set()
        self.webhook_dispatcher = webhook_dispatcher or WebhookDispatcher()
        self.resource_acceptor = resource_acceptor or (lambda _resource, _instruction: True)

    @staticmethod
    def _fingerprint(payload: dict[str, object]) -> str:
        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _expire(self, now: datetime) -> None:
        for reservation_id, reservation in tuple(self._reservations.items()):
            if reservation.status == "CONFIRMED" and reservation.expires_at <= now:
                self._reservations[reservation_id] = reservation.model_copy(
                    update={"status": "EXPIRED"}
                )

    def create(
        self,
        *,
        idempotency_key: str,
        zone_id: str,
        interval_start: datetime,
        interval_end: datetime,
        direction: FlexibilityDirection,
        power_kw: float,
        consequence_type: ConsequenceType = "DEFER",
        callback_url: str | None = None,
        now: datetime | None = None,
    ) -> Reservation:
        current = now or datetime.now(UTC)
        if (
            not math.isfinite(power_kw)
            or power_kw <= 0
            or power_kw > MAX_RESERVATION_POWER_KW
            or interval_end <= interval_start
        ):
            raise ValueError("invalid reservation quantity or interval")
        payload: dict[str, object] = {
            "zone_id": zone_id,
            "interval_start": interval_start,
            "interval_end": interval_end,
            "direction": direction,
            "power_kw": power_kw,
            "consequence_type": consequence_type,
            "callback_url": callback_url,
        }
        fingerprint = self._fingerprint(payload)

        with self._lock:
            self._expire(current)
            previous = self._idempotency.get(idempotency_key)
            if previous:
                previous_fingerprint, reservation_id = previous
                if previous_fingerprint != fingerprint:
                    raise IdempotencyConflict("idempotency key was used with a different request")
                return self._reservations[reservation_id]

            offers = self.offer_store.eligible_offers(
                zone_id=zone_id,
                interval_start=interval_start,
                interval_end=interval_end,
                consequence_type=consequence_type,
                now=current,
            )
            if len({offer.resource_id for offer in offers}) < self.offer_store.minimum_participants:
                raise InsufficientCapacity("the privacy threshold is not met")

            allocations: list[Allocation] = []
            remaining = power_kw
            for offer in offers:
                already_reserved = self._reserved_for_offer(offer, direction)
                capacity = (
                    offer.upward_capacity_kw
                    if direction == "UPWARD"
                    else offer.downward_capacity_kw
                )
                allocated = min(max(0.0, capacity - already_reserved), remaining)
                if allocated > 0:
                    allocations.append(
                        Allocation(
                            resource_id=offer.resource_id,
                            power_kw=allocated,
                            baseline_power_kw=offer.baseline_power_kw,
                            source_version=offer.source_version,
                        )
                    )
                    remaining -= allocated
                if remaining <= 1e-9:
                    break
            if remaining > 1e-9:
                raise InsufficientCapacity(f"missing {remaining:.6f} kW")

            reservation_id = uuid.uuid4()
            reservation = Reservation(
                reservation_id=reservation_id,
                correlation_id=uuid.uuid4(),
                zone_id=zone_id,
                interval_start=interval_start,
                interval_end=interval_end,
                direction=direction,
                requested_power_kw=power_kw,
                allocated_power_kw=sum(item.power_kw for item in allocations),
                # Report the eligible privacy cohort, never a small allocation subset.
                participant_count=len({offer.resource_id for offer in offers}),
                consequence_type=consequence_type,
                status="CONFIRMED",
                created_at=current,
                expires_at=min(interval_start, current + timedelta(minutes=5)),
            )
            self._reservations[reservation_id] = reservation
            self._allocations[reservation_id] = tuple(allocations)
            self._idempotency[idempotency_key] = (fingerprint, reservation_id)
            if callback_url:
                self._callbacks[reservation_id] = callback_url
            return reservation

    def _reserved_for_offer(
        self, offer: FlexibilityOffer, direction: FlexibilityDirection
    ) -> float:
        total = 0.0
        for reservation_id, reservation in self._reservations.items():
            if reservation.status not in {"CONFIRMED", "ACTIVATED", "COMPLETED", "FAILED"}:
                continue
            if (
                reservation.zone_id == offer.zone_id
                and reservation.interval_start == offer.interval_start
                and reservation.interval_end == offer.interval_end
                and reservation.direction == direction
                and reservation.consequence_type == offer.consequence_type
            ):
                total += sum(
                    item.power_kw
                    for item in self._allocations[reservation_id]
                    if item.resource_id == offer.resource_id
                )
        return total

    def get(self, reservation_id: uuid.UUID, *, now: datetime | None = None) -> Reservation:
        with self._lock:
            self._expire(now or datetime.now(UTC))
            try:
                return self._reservations[reservation_id]
            except KeyError as error:
                raise ReservationNotFound(str(reservation_id)) from error

    def cancel(self, reservation_id: uuid.UUID) -> Reservation:
        with self._lock:
            reservation = self.get(reservation_id)
            if reservation.status != "CONFIRMED":
                raise ReservationStateConflict(f"cannot cancel {reservation.status}")
            cancelled = reservation.model_copy(update={"status": "CANCELLED"})
            self._reservations[reservation_id] = cancelled
            return cancelled

    def activate(self, reservation_id: uuid.UUID) -> Activation:
        with self._lock:
            existing_id = self._activation_by_reservation.get(reservation_id)
            if existing_id:
                return self._activations[existing_id]
            reservation = self.get(reservation_id)
            if reservation.status != "CONFIRMED":
                raise ReservationStateConflict(f"cannot activate {reservation.status}")
            callback_url = self._callbacks.get(reservation_id)
            if callback_url:
                self._notify(callback_url, "activation.accepted", reservation)
                self._notify(callback_url, "activation.started", reservation)
            instruction_pairs = tuple(
                (item, build_pebc_instruction(allocation=item, reservation=reservation))
                for item in self._allocations[reservation_id]
            )
            accepted_pairs = tuple(
                pair
                for pair in instruction_pairs
                if self.resource_acceptor(pair[0].resource_id, pair[1])
            )
            rejected_count = len(instruction_pairs) - len(accepted_pairs)
            result_status: Literal["FAILED", "COMPLETED"] = (
                "FAILED" if rejected_count else "COMPLETED"
            )
            instructions = tuple(pair[1] for pair in instruction_pairs)
            self._allocations[reservation_id] = tuple(pair[0] for pair in accepted_pairs)
            now = datetime.now(UTC)
            activation = Activation(
                activation_id=uuid.uuid4(),
                reservation_id=reservation_id,
                correlation_id=reservation.correlation_id,
                status=result_status,
                instruction_count=len(instructions),
                accepted_instruction_count=len(accepted_pairs),
                rejected_instruction_count=rejected_count,
                created_at=now,
                completed_at=now,
            )
            self._instructions[activation.activation_id] = instructions
            self._activations[activation.activation_id] = activation
            self._activation_by_reservation[reservation_id] = activation.activation_id
            self._reservations[reservation_id] = reservation.model_copy(
                update={
                    "status": result_status,
                    "allocated_power_kw": sum(pair[0].power_kw for pair in accepted_pairs),
                }
            )
            if callback_url:
                event = "activation.failed" if rejected_count else "activation.completed"
                self._notify(callback_url, event, activation)
            return activation

    def _notify(self, callback_url: str, event: str, payload: Reservation | Activation) -> None:
        self.webhook_dispatcher.deliver(callback_url, event, payload.model_dump(mode="json"))

    def get_activation(self, activation_id: uuid.UUID) -> Activation:
        try:
            return self._activations[activation_id]
        except KeyError as error:
            raise ReservationNotFound(str(activation_id)) from error

    def instructions_for(self, activation_id: uuid.UUID) -> tuple[dict[str, object], ...]:
        return self._instructions[activation_id]

    def apply_residual_capacity(
        self, aggregates: list[FlexibilityAggregate]
    ) -> list[FlexibilityAggregate]:
        with self._lock:
            self._expire(datetime.now(UTC))
            adjusted: list[FlexibilityAggregate] = []
            for aggregate in aggregates:
                upward = 0.0
                downward = 0.0
                for reservation in self._reservations.values():
                    if reservation.status not in {
                        "CONFIRMED",
                        "ACTIVATED",
                        "COMPLETED",
                        "FAILED",
                    }:
                        continue
                    same_product = (
                        reservation.zone_id == aggregate.zone_id
                        and reservation.interval_start == aggregate.interval_start
                        and reservation.interval_end == aggregate.interval_end
                        and reservation.consequence_type == aggregate.consequence_type
                    )
                    if same_product and reservation.direction == "UPWARD":
                        upward += reservation.allocated_power_kw
                    elif same_product:
                        downward += reservation.allocated_power_kw
                hours = (aggregate.interval_end - aggregate.interval_start).total_seconds() / 3600
                adjusted.append(
                    aggregate.model_copy(
                        update={
                            "upward_capacity_kw": round(
                                max(0.0, aggregate.upward_capacity_kw - upward), 6
                            ),
                            "downward_capacity_kw": round(
                                max(0.0, aggregate.downward_capacity_kw - downward), 6
                            ),
                            "upward_energy_kwh": round(
                                max(0.0, aggregate.upward_energy_kwh - upward * hours), 6
                            ),
                            "downward_energy_kwh": round(
                                max(0.0, aggregate.downward_energy_kwh - downward * hours), 6
                            ),
                        }
                    )
                )
            return adjusted

    def public_residual_capacity(
        self, aggregates: list[FlexibilityAggregate]
    ) -> list[FlexibilityAggregate]:
        """Publish a stable residual cell or suppress it after reservation changes."""

        with self._lock:
            adjusted = self.apply_residual_capacity(aggregates)
            published: list[FlexibilityAggregate] = []
            for aggregate in adjusted:
                cell = (
                    aggregate.zone_id,
                    aggregate.interval_start,
                    aggregate.interval_end,
                    aggregate.consequence_type,
                )
                residual = (
                    aggregate.upward_capacity_kw,
                    aggregate.downward_capacity_kw,
                    aggregate.upward_energy_kwh,
                    aggregate.downward_energy_kwh,
                )
                previous = self._published_residuals.get(cell)
                if previous is not None and previous != residual:
                    self._suppressed_public_cells.add(cell)
                if cell in self._suppressed_public_cells:
                    continue
                self._published_residuals[cell] = residual
                published.append(aggregate)
            return published
