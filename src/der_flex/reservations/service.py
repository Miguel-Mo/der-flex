from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from der_flex.adapters.s2.dispatch import build_pebc_instruction
from der_flex.adapters.webhooks import WebhookDispatcher
from der_flex.domain.models import (
    Activation,
    ConsequenceType,
    FlexibilityAggregate,
    FlexibilityDirection,
    Reservation,
)
from der_flex.domain.store import OfferStore
from der_flex.locking import product_lock_key
from der_flex.outbox import OutboxProcessor
from der_flex.reservations.backend import (
    Allocation,
    InMemoryReservationBackend,
    OutboxTask,
    ReservationBackend,
)

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


class ReservationService:
    def __init__(
        self,
        offer_store: OfferStore,
        *,
        backend: ReservationBackend | None = None,
        webhook_dispatcher: WebhookDispatcher | None = None,
        resource_acceptor: Callable[[str, dict[str, object]], bool] | None = None,
        process_outbox_on_activate: bool = True,
    ) -> None:
        self.offer_store = offer_store
        self.backend = backend or InMemoryReservationBackend()
        self.webhook_dispatcher = webhook_dispatcher or WebhookDispatcher()
        self.resource_acceptor = resource_acceptor or (lambda _resource, _instruction: True)
        self.process_outbox_on_activate = process_outbox_on_activate
        self.outbox_processor = OutboxProcessor(
            self.backend,
            self.webhook_dispatcher,
            self.resource_acceptor,
        )

    def is_ready(self) -> bool:
        return self.backend.is_ready()

    @staticmethod
    def _fingerprint(payload: dict[str, object]) -> str:
        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

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

        product_key = product_lock_key(
            zone_id,
            interval_start,
            interval_end,
            consequence_type,
            direction,
        )
        with self.backend.transaction(
            (product_key, f"idempotency:{idempotency_key}")
        ) as state:
            state.expire(current)
            previous = state.get_idempotency(idempotency_key)
            if previous:
                previous_fingerprint, reservation_id = previous
                if previous_fingerprint != fingerprint:
                    raise IdempotencyConflict("idempotency key was used with a different request")
                reservation = state.get_reservation(reservation_id)
                if reservation is None:
                    raise ReservationNotFound(str(reservation_id))
                return reservation

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
                already_reserved = state.reserved_for_offer(offer, direction)
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
            state.save_reservation(
                reservation,
                tuple(allocations),
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                callback_url=callback_url,
            )
            return reservation

    def get(self, reservation_id: uuid.UUID, *, now: datetime | None = None) -> Reservation:
        with self.backend.transaction((f"reservation:{reservation_id}",)) as state:
            state.expire(now or datetime.now(UTC))
            reservation = state.get_reservation(reservation_id)
            if reservation is None:
                raise ReservationNotFound(str(reservation_id))
            return reservation

    def cancel(self, reservation_id: uuid.UUID) -> Reservation:
        with self.backend.transaction((f"reservation:{reservation_id}",)) as state:
            state.expire(datetime.now(UTC))
            reservation = state.get_reservation(reservation_id)
            if reservation is None:
                raise ReservationNotFound(str(reservation_id))
            if reservation.status != "CONFIRMED":
                raise ReservationStateConflict(f"cannot cancel {reservation.status}")
            cancelled = reservation.model_copy(update={"status": "CANCELLED"})
            state.update_reservation(cancelled)
            return cancelled

    def activate(self, reservation_id: uuid.UUID) -> Activation:
        if self.backend.durable_outbox:
            return self._activate_durable(reservation_id)
        with self.backend.transaction((f"reservation:{reservation_id}",)) as state:
            existing = state.activation_for_reservation(reservation_id)
            if existing:
                return existing
            state.expire(datetime.now(UTC))
            reservation = state.get_reservation(reservation_id)
            if reservation is None:
                raise ReservationNotFound(str(reservation_id))
            if reservation.status != "CONFIRMED":
                raise ReservationStateConflict(f"cannot activate {reservation.status}")
            callback_url = state.callback_url(reservation_id)
            if callback_url:
                self._notify(callback_url, "activation.accepted", reservation)
                self._notify(callback_url, "activation.started", reservation)
            instruction_pairs = tuple(
                (item, build_pebc_instruction(allocation=item, reservation=reservation))
                for item in state.get_allocations(reservation_id)
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
            updated_reservation = reservation.model_copy(
                update={
                    "status": result_status,
                    "allocated_power_kw": sum(pair[0].power_kw for pair in accepted_pairs),
                }
            )
            state.save_activation(
                activation,
                updated_reservation,
                tuple(pair[0] for pair in accepted_pairs),
                instructions,
            )
            if callback_url:
                event = "activation.failed" if rejected_count else "activation.completed"
                self._notify(callback_url, event, activation)
            return activation

    @staticmethod
    def _outbox_event_id(activation_id: uuid.UUID, suffix: str) -> uuid.UUID:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"der-flex:{activation_id}:{suffix}")

    def _activate_durable(self, reservation_id: uuid.UUID) -> Activation:
        activation_id: uuid.UUID
        with self.backend.transaction((f"reservation:{reservation_id}",)) as state:
            existing = state.activation_for_reservation(reservation_id)
            if existing:
                activation_id = existing.activation_id
            else:
                now = datetime.now(UTC)
                state.expire(now)
                reservation = state.get_reservation(reservation_id)
                if reservation is None:
                    raise ReservationNotFound(str(reservation_id))
                if reservation.status != "CONFIRMED":
                    raise ReservationStateConflict(f"cannot activate {reservation.status}")
                allocations = state.get_allocations(reservation_id)
                instructions = tuple(
                    build_pebc_instruction(allocation=item, reservation=reservation)
                    for item in allocations
                )
                activation_id = uuid.uuid4()
                activation = Activation(
                    activation_id=activation_id,
                    reservation_id=reservation_id,
                    correlation_id=reservation.correlation_id,
                    status="PENDING",
                    instruction_count=len(instructions),
                    accepted_instruction_count=0,
                    rejected_instruction_count=0,
                    created_at=now,
                    completed_at=None,
                )
                activated_reservation = reservation.model_copy(update={"status": "ACTIVATED"})
                state.save_activation(
                    activation,
                    activated_reservation,
                    allocations,
                    instructions,
                )
                callback_url = state.callback_url(reservation_id)
                if callback_url:
                    reservation_payload = activated_reservation.model_dump(mode="json")
                    for event in ("activation.accepted", "activation.started"):
                        state.enqueue_outbox(
                            OutboxTask(
                                event_id=self._outbox_event_id(
                                    activation_id, f"webhook:{event}"
                                ),
                                kind="WEBHOOK",
                                destination=callback_url,
                                event_type=event,
                                payload=reservation_payload,
                                attempts=0,
                                activation_id=activation_id,
                                reservation_id=reservation_id,
                            ),
                            available_at=now,
                        )
                for index, (allocation, instruction) in enumerate(
                    zip(allocations, instructions, strict=True)
                ):
                    state.enqueue_outbox(
                        OutboxTask(
                            event_id=self._outbox_event_id(
                                activation_id, f"resource:{index}"
                            ),
                            kind="RESOURCE",
                            destination=allocation.resource_id,
                            event_type="pebc.instruction",
                            payload=instruction,
                            attempts=0,
                            activation_id=activation_id,
                            reservation_id=reservation_id,
                            allocation_index=index,
                        ),
                        available_at=now,
                    )
        if self.process_outbox_on_activate:
            self.outbox_processor.drain()
        return self.get_activation(activation_id)

    def _notify(self, callback_url: str, event: str, payload: Reservation | Activation) -> None:
        self.webhook_dispatcher.deliver(callback_url, event, payload.model_dump(mode="json"))

    def get_activation(self, activation_id: uuid.UUID) -> Activation:
        with self.backend.transaction() as state:
            activation = state.get_activation(activation_id)
            if activation is None:
                raise ReservationNotFound(str(activation_id))
            return activation

    def instructions_for(self, activation_id: uuid.UUID) -> tuple[dict[str, object], ...]:
        with self.backend.transaction() as state:
            return state.instructions_for(activation_id)

    def apply_residual_capacity(
        self, aggregates: list[FlexibilityAggregate]
    ) -> list[FlexibilityAggregate]:
        with self.backend.transaction() as state:
            state.expire(datetime.now(UTC))
            return self._apply_residual_capacity(aggregates, state.active_reservations())

    @staticmethod
    def _apply_residual_capacity(
        aggregates: list[FlexibilityAggregate], reservations: tuple[Reservation, ...]
    ) -> list[FlexibilityAggregate]:
        adjusted: list[FlexibilityAggregate] = []
        for aggregate in aggregates:
            upward = 0.0
            downward = 0.0
            for reservation in reservations:
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

        lock_keys = tuple(
            product_lock_key(
                item.zone_id,
                item.interval_start,
                item.interval_end,
                item.consequence_type,
                direction,
            )
            for item in aggregates
            for direction in ("UPWARD", "DOWNWARD")
        )
        with self.backend.transaction(lock_keys) as state:
            state.expire(datetime.now(UTC))
            adjusted = self._apply_residual_capacity(aggregates, state.active_reservations())
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
                previous = state.published_residual(cell)
                if previous is not None and previous != residual:
                    state.suppress_public_cell(cell)
                if state.public_cell_is_suppressed(cell):
                    continue
                state.set_published_residual(cell, residual)
                published.append(aggregate)
            return published
