from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from der_flex.domain.models import (
    Activation,
    ConsequenceType,
    FlexibilityDirection,
    FlexibilityOffer,
    Reservation,
)

type ProductCell = tuple[str, datetime, datetime, ConsequenceType]
type Residual = tuple[float, float, float, float]


@dataclass(frozen=True)
class Allocation:
    resource_id: str
    power_kw: float
    baseline_power_kw: float
    source_version: str


class ReservationUnitOfWork(Protocol):
    def expire(self, now: datetime) -> None: ...

    def get_idempotency(self, key: str) -> tuple[str, uuid.UUID] | None: ...

    def get_reservation(self, reservation_id: uuid.UUID) -> Reservation | None: ...

    def get_allocations(self, reservation_id: uuid.UUID) -> tuple[Allocation, ...]: ...

    def reserved_for_offer(
        self, offer: FlexibilityOffer, direction: FlexibilityDirection
    ) -> float: ...

    def save_reservation(
        self,
        reservation: Reservation,
        allocations: tuple[Allocation, ...],
        *,
        idempotency_key: str,
        fingerprint: str,
        callback_url: str | None,
    ) -> None: ...

    def update_reservation(self, reservation: Reservation) -> None: ...

    def callback_url(self, reservation_id: uuid.UUID) -> str | None: ...

    def activation_for_reservation(self, reservation_id: uuid.UUID) -> Activation | None: ...

    def get_activation(self, activation_id: uuid.UUID) -> Activation | None: ...

    def save_activation(
        self,
        activation: Activation,
        reservation: Reservation,
        allocations: tuple[Allocation, ...],
        instructions: tuple[dict[str, object], ...],
    ) -> None: ...

    def instructions_for(self, activation_id: uuid.UUID) -> tuple[dict[str, object], ...]: ...

    def active_reservations(self) -> tuple[Reservation, ...]: ...

    def published_residual(self, cell: ProductCell) -> Residual | None: ...

    def set_published_residual(self, cell: ProductCell, residual: Residual) -> None: ...

    def suppress_public_cell(self, cell: ProductCell) -> None: ...

    def public_cell_is_suppressed(self, cell: ProductCell) -> bool: ...


class ReservationBackend(Protocol):
    def is_ready(self) -> bool: ...

    @contextmanager
    def transaction(
        self, lock_keys: tuple[str, ...] = ()
    ) -> Iterator[ReservationUnitOfWork]: ...


class InMemoryReservationBackend:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reservations: dict[uuid.UUID, Reservation] = {}
        self._allocations: dict[uuid.UUID, tuple[Allocation, ...]] = {}
        self._idempotency: dict[str, tuple[str, uuid.UUID]] = {}
        self._activations: dict[uuid.UUID, Activation] = {}
        self._activation_by_reservation: dict[uuid.UUID, uuid.UUID] = {}
        self._instructions: dict[uuid.UUID, tuple[dict[str, object], ...]] = {}
        self._callbacks: dict[uuid.UUID, str] = {}
        self._published_residuals: dict[ProductCell, Residual] = {}
        self._suppressed_public_cells: set[ProductCell] = set()

    def is_ready(self) -> bool:
        return True

    @contextmanager
    def transaction(
        self, lock_keys: tuple[str, ...] = ()
    ) -> Iterator[InMemoryReservationBackend]:
        del lock_keys
        with self._lock:
            yield self

    def expire(self, now: datetime) -> None:
        for reservation_id, reservation in tuple(self._reservations.items()):
            if reservation.status == "CONFIRMED" and reservation.expires_at <= now:
                self._reservations[reservation_id] = reservation.model_copy(
                    update={"status": "EXPIRED"}
                )

    def get_idempotency(self, key: str) -> tuple[str, uuid.UUID] | None:
        return self._idempotency.get(key)

    def get_reservation(self, reservation_id: uuid.UUID) -> Reservation | None:
        return self._reservations.get(reservation_id)

    def get_allocations(self, reservation_id: uuid.UUID) -> tuple[Allocation, ...]:
        return self._allocations[reservation_id]

    def reserved_for_offer(
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

    def save_reservation(
        self,
        reservation: Reservation,
        allocations: tuple[Allocation, ...],
        *,
        idempotency_key: str,
        fingerprint: str,
        callback_url: str | None,
    ) -> None:
        self._reservations[reservation.reservation_id] = reservation
        self._allocations[reservation.reservation_id] = allocations
        self._idempotency[idempotency_key] = (fingerprint, reservation.reservation_id)
        if callback_url:
            self._callbacks[reservation.reservation_id] = callback_url

    def update_reservation(self, reservation: Reservation) -> None:
        self._reservations[reservation.reservation_id] = reservation

    def callback_url(self, reservation_id: uuid.UUID) -> str | None:
        return self._callbacks.get(reservation_id)

    def activation_for_reservation(self, reservation_id: uuid.UUID) -> Activation | None:
        activation_id = self._activation_by_reservation.get(reservation_id)
        return self._activations.get(activation_id) if activation_id else None

    def get_activation(self, activation_id: uuid.UUID) -> Activation | None:
        return self._activations.get(activation_id)

    def save_activation(
        self,
        activation: Activation,
        reservation: Reservation,
        allocations: tuple[Allocation, ...],
        instructions: tuple[dict[str, object], ...],
    ) -> None:
        self._allocations[reservation.reservation_id] = allocations
        self._instructions[activation.activation_id] = instructions
        self._activations[activation.activation_id] = activation
        self._activation_by_reservation[reservation.reservation_id] = activation.activation_id
        self._reservations[reservation.reservation_id] = reservation

    def instructions_for(self, activation_id: uuid.UUID) -> tuple[dict[str, object], ...]:
        return self._instructions[activation_id]

    def active_reservations(self) -> tuple[Reservation, ...]:
        return tuple(
            reservation
            for reservation in self._reservations.values()
            if reservation.status in {"CONFIRMED", "ACTIVATED", "COMPLETED", "FAILED"}
        )

    def published_residual(self, cell: ProductCell) -> Residual | None:
        return self._published_residuals.get(cell)

    def set_published_residual(self, cell: ProductCell, residual: Residual) -> None:
        self._published_residuals[cell] = residual

    def suppress_public_cell(self, cell: ProductCell) -> None:
        self._suppressed_public_cells.add(cell)

    def public_cell_is_suppressed(self, cell: ProductCell) -> bool:
        return cell in self._suppressed_public_cells
