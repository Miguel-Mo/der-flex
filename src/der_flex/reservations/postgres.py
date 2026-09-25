from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from der_flex.domain.models import (
    Activation,
    FlexibilityDirection,
    FlexibilityOffer,
    Reservation,
)
from der_flex.reservations.backend import Allocation, ProductCell, Residual

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS der_flex_schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS der_flex_reservations (
    reservation_id uuid PRIMARY KEY,
    correlation_id uuid NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    request_fingerprint text NOT NULL,
    zone_id text NOT NULL,
    interval_start timestamptz NOT NULL,
    interval_end timestamptz NOT NULL,
    direction text NOT NULL,
    requested_power_kw double precision NOT NULL,
    allocated_power_kw double precision NOT NULL,
    participant_count integer NOT NULL,
    consequence_type text NOT NULL,
    product_class text NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    callback_url text,
    CONSTRAINT der_flex_reservation_interval CHECK (interval_end > interval_start),
    CONSTRAINT der_flex_reservation_direction CHECK (direction IN ('UPWARD', 'DOWNWARD')),
    CONSTRAINT der_flex_reservation_power CHECK (
        requested_power_kw > 0 AND requested_power_kw <= 1000000
        AND allocated_power_kw >= 0 AND allocated_power_kw <= requested_power_kw
    ),
    CONSTRAINT der_flex_reservation_participants CHECK (participant_count > 0),
    CONSTRAINT der_flex_reservation_consequence CHECK (
        consequence_type IN ('VANISH', 'DEFER')
    ),
    CONSTRAINT der_flex_reservation_product CHECK (
        product_class = 'BEST_EFFORT_PEBC'
    ),
    CONSTRAINT der_flex_reservation_status CHECK (
        status IN ('CONFIRMED', 'ACTIVATED', 'COMPLETED', 'CANCELLED', 'EXPIRED', 'FAILED')
    )
);
CREATE INDEX IF NOT EXISTS der_flex_reservation_product_idx
    ON der_flex_reservations (
        zone_id, interval_start, interval_end, consequence_type, direction, status
    );
CREATE TABLE IF NOT EXISTS der_flex_allocations (
    reservation_id uuid NOT NULL REFERENCES der_flex_reservations(reservation_id)
        ON DELETE CASCADE,
    allocation_index integer NOT NULL,
    resource_id text NOT NULL,
    power_kw double precision NOT NULL,
    baseline_power_kw double precision NOT NULL,
    source_version text NOT NULL CHECK (source_version <> ''),
    CONSTRAINT der_flex_allocation_power CHECK (power_kw > 0 AND power_kw <= 1000000),
    CONSTRAINT der_flex_allocation_baseline CHECK (
        baseline_power_kw >= -100000000 AND baseline_power_kw <= 100000000
    ),
    PRIMARY KEY (reservation_id, allocation_index)
);
CREATE INDEX IF NOT EXISTS der_flex_allocation_resource_idx
    ON der_flex_allocations (resource_id, reservation_id);
CREATE TABLE IF NOT EXISTS der_flex_activations (
    activation_id uuid PRIMARY KEY,
    reservation_id uuid NOT NULL UNIQUE REFERENCES der_flex_reservations(reservation_id),
    correlation_id uuid NOT NULL,
    status text NOT NULL,
    instruction_count integer NOT NULL,
    accepted_instruction_count integer NOT NULL,
    rejected_instruction_count integer NOT NULL,
    created_at timestamptz NOT NULL,
    completed_at timestamptz NOT NULL,
    CONSTRAINT der_flex_activation_status CHECK (status IN ('COMPLETED', 'FAILED')),
    CONSTRAINT der_flex_activation_counts CHECK (
        instruction_count >= 0 AND accepted_instruction_count >= 0
        AND rejected_instruction_count >= 0
        AND accepted_instruction_count + rejected_instruction_count = instruction_count
    ),
    CONSTRAINT der_flex_activation_time CHECK (completed_at >= created_at)
);
CREATE TABLE IF NOT EXISTS der_flex_instructions (
    activation_id uuid NOT NULL REFERENCES der_flex_activations(activation_id)
        ON DELETE CASCADE,
    instruction_index integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (activation_id, instruction_index)
);
CREATE TABLE IF NOT EXISTS der_flex_public_residuals (
    zone_id text NOT NULL,
    interval_start timestamptz NOT NULL,
    interval_end timestamptz NOT NULL,
    consequence_type text NOT NULL,
    upward_capacity_kw double precision NOT NULL,
    downward_capacity_kw double precision NOT NULL,
    upward_energy_kwh double precision NOT NULL,
    downward_energy_kwh double precision NOT NULL,
    suppressed boolean NOT NULL DEFAULT false,
    CONSTRAINT der_flex_public_residual_nonnegative CHECK (
        upward_capacity_kw >= 0 AND downward_capacity_kw >= 0
        AND upward_energy_kwh >= 0 AND downward_energy_kwh >= 0
    ),
    PRIMARY KEY (zone_id, interval_start, interval_end, consequence_type)
);
INSERT INTO der_flex_schema_migrations (version) VALUES (1)
ON CONFLICT (version) DO NOTHING;
"""

ACTIVE_STATUSES = ("CONFIRMED", "ACTIVATED", "COMPLETED", "FAILED")


def _reservation(row: dict[str, Any]) -> Reservation:
    return Reservation(
        reservation_id=row["reservation_id"],
        correlation_id=row["correlation_id"],
        zone_id=row["zone_id"],
        interval_start=row["interval_start"],
        interval_end=row["interval_end"],
        direction=row["direction"],
        requested_power_kw=row["requested_power_kw"],
        allocated_power_kw=row["allocated_power_kw"],
        participant_count=row["participant_count"],
        consequence_type=row["consequence_type"],
        product_class=row["product_class"],
        status=row["status"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


def _activation(row: dict[str, Any]) -> Activation:
    return Activation(
        activation_id=row["activation_id"],
        reservation_id=row["reservation_id"],
        correlation_id=row["correlation_id"],
        status=row["status"],
        instruction_count=row["instruction_count"],
        accepted_instruction_count=row["accepted_instruction_count"],
        rejected_instruction_count=row["rejected_instruction_count"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


class PostgresReservationUnitOfWork:
    def __init__(self, connection: psycopg.Connection[dict[str, Any]]) -> None:
        self.connection = connection

    def expire(self, now: datetime) -> None:
        self.connection.execute(
            """
            UPDATE der_flex_reservations
            SET status = 'EXPIRED'
            WHERE status = 'CONFIRMED' AND expires_at <= %s
            """,
            (now,),
        )

    def get_idempotency(self, key: str) -> tuple[str, uuid.UUID] | None:
        row = self.connection.execute(
            """
            SELECT request_fingerprint, reservation_id
            FROM der_flex_reservations WHERE idempotency_key = %s
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        return str(row["request_fingerprint"]), row["reservation_id"]

    def get_reservation(self, reservation_id: uuid.UUID) -> Reservation | None:
        row = self.connection.execute(
            "SELECT * FROM der_flex_reservations WHERE reservation_id = %s",
            (reservation_id,),
        ).fetchone()
        return _reservation(row) if row else None

    def get_allocations(self, reservation_id: uuid.UUID) -> tuple[Allocation, ...]:
        rows = self.connection.execute(
            """
            SELECT resource_id, power_kw, baseline_power_kw, source_version
            FROM der_flex_allocations
            WHERE reservation_id = %s ORDER BY allocation_index
            """,
            (reservation_id,),
        ).fetchall()
        return tuple(Allocation(**row) for row in rows)

    def reserved_for_offer(
        self, offer: FlexibilityOffer, direction: FlexibilityDirection
    ) -> float:
        row = self.connection.execute(
            """
            SELECT COALESCE(SUM(a.power_kw), 0.0) AS reserved
            FROM der_flex_allocations a
            JOIN der_flex_reservations r USING (reservation_id)
            WHERE a.resource_id = %s
              AND r.zone_id = %s
              AND r.interval_start = %s
              AND r.interval_end = %s
              AND r.direction = %s
              AND r.consequence_type = %s
              AND r.status = ANY(%s)
            """,
            (
                offer.resource_id,
                offer.zone_id,
                offer.interval_start,
                offer.interval_end,
                direction,
                offer.consequence_type,
                list(ACTIVE_STATUSES),
            ),
        ).fetchone()
        if row is None:
            return 0.0
        return float(row["reserved"])

    def save_reservation(
        self,
        reservation: Reservation,
        allocations: tuple[Allocation, ...],
        *,
        idempotency_key: str,
        fingerprint: str,
        callback_url: str | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO der_flex_reservations (
                reservation_id, correlation_id, idempotency_key, request_fingerprint,
                zone_id, interval_start, interval_end, direction, requested_power_kw,
                allocated_power_kw, participant_count, consequence_type, product_class,
                status, created_at, expires_at, callback_url
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                reservation.reservation_id,
                reservation.correlation_id,
                idempotency_key,
                fingerprint,
                reservation.zone_id,
                reservation.interval_start,
                reservation.interval_end,
                reservation.direction,
                reservation.requested_power_kw,
                reservation.allocated_power_kw,
                reservation.participant_count,
                reservation.consequence_type,
                reservation.product_class,
                reservation.status,
                reservation.created_at,
                reservation.expires_at,
                callback_url,
            ),
        )
        self._replace_allocations(reservation.reservation_id, allocations)

    def _replace_allocations(
        self, reservation_id: uuid.UUID, allocations: tuple[Allocation, ...]
    ) -> None:
        self.connection.execute(
            "DELETE FROM der_flex_allocations WHERE reservation_id = %s",
            (reservation_id,),
        )
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO der_flex_allocations (
                    reservation_id, allocation_index, resource_id, power_kw,
                    baseline_power_kw, source_version
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        reservation_id,
                        index,
                        item.resource_id,
                        item.power_kw,
                        item.baseline_power_kw,
                        item.source_version,
                    )
                    for index, item in enumerate(allocations)
                ],
            )

    def update_reservation(self, reservation: Reservation) -> None:
        self.connection.execute(
            """
            UPDATE der_flex_reservations
            SET status = %s, allocated_power_kw = %s
            WHERE reservation_id = %s
            """,
            (reservation.status, reservation.allocated_power_kw, reservation.reservation_id),
        )

    def callback_url(self, reservation_id: uuid.UUID) -> str | None:
        row = self.connection.execute(
            "SELECT callback_url FROM der_flex_reservations WHERE reservation_id = %s",
            (reservation_id,),
        ).fetchone()
        return str(row["callback_url"]) if row and row["callback_url"] else None

    def activation_for_reservation(self, reservation_id: uuid.UUID) -> Activation | None:
        row = self.connection.execute(
            "SELECT * FROM der_flex_activations WHERE reservation_id = %s",
            (reservation_id,),
        ).fetchone()
        return _activation(row) if row else None

    def get_activation(self, activation_id: uuid.UUID) -> Activation | None:
        row = self.connection.execute(
            "SELECT * FROM der_flex_activations WHERE activation_id = %s",
            (activation_id,),
        ).fetchone()
        return _activation(row) if row else None

    def save_activation(
        self,
        activation: Activation,
        reservation: Reservation,
        allocations: tuple[Allocation, ...],
        instructions: tuple[dict[str, object], ...],
    ) -> None:
        self._replace_allocations(reservation.reservation_id, allocations)
        self.update_reservation(reservation)
        self.connection.execute(
            """
            INSERT INTO der_flex_activations (
                activation_id, reservation_id, correlation_id, status,
                instruction_count, accepted_instruction_count,
                rejected_instruction_count, created_at, completed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                activation.activation_id,
                activation.reservation_id,
                activation.correlation_id,
                activation.status,
                activation.instruction_count,
                activation.accepted_instruction_count,
                activation.rejected_instruction_count,
                activation.created_at,
                activation.completed_at,
            ),
        )
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO der_flex_instructions (activation_id, instruction_index, payload)
                VALUES (%s, %s, %s::jsonb)
                """,
                [
                    (activation.activation_id, index, json.dumps(instruction))
                    for index, instruction in enumerate(instructions)
                ],
            )

    def instructions_for(self, activation_id: uuid.UUID) -> tuple[dict[str, object], ...]:
        rows = self.connection.execute(
            """
            SELECT payload FROM der_flex_instructions
            WHERE activation_id = %s ORDER BY instruction_index
            """,
            (activation_id,),
        ).fetchall()
        return tuple(dict(row["payload"]) for row in rows)

    def active_reservations(self) -> tuple[Reservation, ...]:
        rows = self.connection.execute(
            "SELECT * FROM der_flex_reservations WHERE status = ANY(%s)",
            (list(ACTIVE_STATUSES),),
        ).fetchall()
        return tuple(_reservation(row) for row in rows)

    @staticmethod
    def _cell_values(cell: ProductCell) -> tuple[object, ...]:
        return cell

    def published_residual(self, cell: ProductCell) -> Residual | None:
        row = self.connection.execute(
            """
            SELECT upward_capacity_kw, downward_capacity_kw,
                   upward_energy_kwh, downward_energy_kwh
            FROM der_flex_public_residuals
            WHERE zone_id = %s AND interval_start = %s AND interval_end = %s
              AND consequence_type = %s
            """,
            self._cell_values(cell),
        ).fetchone()
        if row is None:
            return None
        return (
            float(row["upward_capacity_kw"]),
            float(row["downward_capacity_kw"]),
            float(row["upward_energy_kwh"]),
            float(row["downward_energy_kwh"]),
        )

    def set_published_residual(self, cell: ProductCell, residual: Residual) -> None:
        self.connection.execute(
            """
            INSERT INTO der_flex_public_residuals (
                zone_id, interval_start, interval_end, consequence_type,
                upward_capacity_kw, downward_capacity_kw,
                upward_energy_kwh, downward_energy_kwh
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (zone_id, interval_start, interval_end, consequence_type)
            DO UPDATE SET
                upward_capacity_kw = EXCLUDED.upward_capacity_kw,
                downward_capacity_kw = EXCLUDED.downward_capacity_kw,
                upward_energy_kwh = EXCLUDED.upward_energy_kwh,
                downward_energy_kwh = EXCLUDED.downward_energy_kwh
            """,
            (*self._cell_values(cell), *residual),
        )

    def suppress_public_cell(self, cell: ProductCell) -> None:
        self.connection.execute(
            """
            UPDATE der_flex_public_residuals SET suppressed = true
            WHERE zone_id = %s AND interval_start = %s AND interval_end = %s
              AND consequence_type = %s
            """,
            self._cell_values(cell),
        )

    def public_cell_is_suppressed(self, cell: ProductCell) -> bool:
        row = self.connection.execute(
            """
            SELECT suppressed FROM der_flex_public_residuals
            WHERE zone_id = %s AND interval_start = %s AND interval_end = %s
              AND consequence_type = %s
            """,
            self._cell_values(cell),
        ).fetchone()
        return bool(row and row["suppressed"])


class PostgresReservationBackend:
    """Durable reservation state with transaction-scoped cross-process locks."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(SCHEMA_SQL)
            connection.commit()

    def is_ready(self) -> bool:
        try:
            with self._connect() as connection:
                return connection.execute("SELECT 1").fetchone() is not None
        except psycopg.Error:
            return False

    def reset_for_tests(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                TRUNCATE der_flex_instructions, der_flex_activations,
                         der_flex_allocations, der_flex_reservations,
                         der_flex_public_residuals CASCADE
                """
            )
            connection.commit()

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    @contextmanager
    def transaction(
        self, lock_keys: tuple[str, ...] = ()
    ) -> Iterator[PostgresReservationUnitOfWork]:
        with self._connect() as connection, connection.transaction():
            for key in sorted(set(lock_keys)):
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (key,),
                )
            yield PostgresReservationUnitOfWork(connection)
