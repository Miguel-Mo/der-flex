from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from der_flex.domain.models import (
    Activation,
    FlexibilityDirection,
    FlexibilityOffer,
    Reservation,
)
from der_flex.reservations.backend import Allocation, OutboxTask, ProductCell, Residual

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS der_flex_schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS der_flex_reservations (
    reservation_id uuid PRIMARY KEY,
    tenant_id text NOT NULL DEFAULT 'development' CHECK (tenant_id <> ''),
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
    completed_at timestamptz,
    CONSTRAINT der_flex_activation_status CHECK (
        status IN ('PENDING', 'COMPLETED', 'FAILED')
    ),
    CONSTRAINT der_flex_activation_counts CHECK (
        instruction_count >= 0 AND accepted_instruction_count >= 0
        AND rejected_instruction_count >= 0
        AND accepted_instruction_count + rejected_instruction_count <= instruction_count
    ),
    CONSTRAINT der_flex_activation_completion CHECK (
        (status = 'PENDING' AND completed_at IS NULL)
        OR (
            status IN ('COMPLETED', 'FAILED') AND completed_at >= created_at
            AND accepted_instruction_count + rejected_instruction_count = instruction_count
        )
    )
);
CREATE TABLE IF NOT EXISTS der_flex_instructions (
    activation_id uuid NOT NULL REFERENCES der_flex_activations(activation_id)
        ON DELETE CASCADE,
    instruction_index integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (activation_id, instruction_index)
);
CREATE TABLE IF NOT EXISTS der_flex_outbox (
    event_id uuid PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('RESOURCE', 'WEBHOOK')),
    destination text NOT NULL CHECK (destination <> ''),
    event_type text NOT NULL CHECK (event_type <> ''),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    activation_id uuid NOT NULL REFERENCES der_flex_activations(activation_id)
        ON DELETE CASCADE,
    reservation_id uuid NOT NULL REFERENCES der_flex_reservations(reservation_id)
        ON DELETE CASCADE,
    allocation_index integer,
    status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PROCESSING', 'RETRY', 'DELIVERED', 'DEAD')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL,
    locked_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    last_error text,
    CONSTRAINT der_flex_outbox_allocation CHECK (
        (kind = 'RESOURCE' AND allocation_index IS NOT NULL)
        OR (kind = 'WEBHOOK' AND allocation_index IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS der_flex_outbox_claim_idx
    ON der_flex_outbox (status, available_at, created_at);
CREATE TABLE IF NOT EXISTS der_flex_public_residuals (
    tenant_id text NOT NULL DEFAULT 'development' CHECK (tenant_id <> ''),
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
    PRIMARY KEY (tenant_id, zone_id, interval_start, interval_end, consequence_type)
);
INSERT INTO der_flex_schema_migrations (version) VALUES (1)
ON CONFLICT (version) DO NOTHING;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM der_flex_schema_migrations WHERE version = 3
    ) THEN
        ALTER TABLE der_flex_activations
            ALTER COLUMN completed_at DROP NOT NULL;
        ALTER TABLE der_flex_activations
            DROP CONSTRAINT IF EXISTS der_flex_activation_status;
        ALTER TABLE der_flex_activations
            DROP CONSTRAINT IF EXISTS der_flex_activation_counts;
        ALTER TABLE der_flex_activations
            DROP CONSTRAINT IF EXISTS der_flex_activation_time;
        ALTER TABLE der_flex_activations
            DROP CONSTRAINT IF EXISTS der_flex_activation_completion;
        ALTER TABLE der_flex_activations
            ADD CONSTRAINT der_flex_activation_status CHECK (
                status IN ('PENDING', 'COMPLETED', 'FAILED')
            );
        ALTER TABLE der_flex_activations
            ADD CONSTRAINT der_flex_activation_counts CHECK (
                instruction_count >= 0 AND accepted_instruction_count >= 0
                AND rejected_instruction_count >= 0
                AND accepted_instruction_count + rejected_instruction_count
                    <= instruction_count
            );
        ALTER TABLE der_flex_activations
            ADD CONSTRAINT der_flex_activation_completion CHECK (
                (status = 'PENDING' AND completed_at IS NULL)
                OR (
                    status IN ('COMPLETED', 'FAILED') AND completed_at >= created_at
                    AND accepted_instruction_count + rejected_instruction_count
                        = instruction_count
                )
            );
    END IF;
END $$;
INSERT INTO der_flex_schema_migrations (version) VALUES (3)
ON CONFLICT (version) DO NOTHING;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM der_flex_schema_migrations WHERE version = 4
    ) THEN
        ALTER TABLE der_flex_reservations
            ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'development';
        ALTER TABLE der_flex_public_residuals
            ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'development';
        ALTER TABLE der_flex_public_residuals
            DROP CONSTRAINT IF EXISTS der_flex_public_residuals_pkey;
        ALTER TABLE der_flex_public_residuals
            ADD PRIMARY KEY (
                tenant_id, zone_id, interval_start, interval_end, consequence_type
            );
    END IF;
END $$;
INSERT INTO der_flex_schema_migrations (version) VALUES (4)
ON CONFLICT (version) DO NOTHING;
"""

ACTIVE_STATUSES = ("CONFIRMED", "ACTIVATED", "COMPLETED", "FAILED")


def _reservation(row: dict[str, Any]) -> Reservation:
    return Reservation(
        tenant_id=row["tenant_id"],
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
              AND r.tenant_id = %s
              AND r.zone_id = %s
              AND r.interval_start = %s
              AND r.interval_end = %s
              AND r.direction = %s
              AND r.consequence_type = %s
              AND r.status = ANY(%s)
            """,
            (
                offer.resource_id,
                offer.tenant_id,
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
                reservation_id, tenant_id, correlation_id, idempotency_key, request_fingerprint,
                zone_id, interval_start, interval_end, direction, requested_power_kw,
                allocated_power_kw, participant_count, consequence_type, product_class,
                status, created_at, expires_at, callback_url
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                reservation.reservation_id,
                reservation.tenant_id,
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

    def enqueue_outbox(self, task: OutboxTask, *, available_at: datetime) -> None:
        self.connection.execute(
            """
            INSERT INTO der_flex_outbox (
                event_id, kind, destination, event_type, payload,
                activation_id, reservation_id, allocation_index, available_at
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING
            """,
            (
                task.event_id,
                task.kind,
                task.destination,
                task.event_type,
                json.dumps(task.payload, sort_keys=True, default=str),
                task.activation_id,
                task.reservation_id,
                task.allocation_index,
                available_at,
            ),
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
            WHERE tenant_id = %s AND zone_id = %s
              AND interval_start = %s AND interval_end = %s
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
                tenant_id, zone_id, interval_start, interval_end, consequence_type,
                upward_capacity_kw, downward_capacity_kw,
                upward_energy_kwh, downward_energy_kwh
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, zone_id, interval_start, interval_end, consequence_type)
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
            WHERE tenant_id = %s AND zone_id = %s
              AND interval_start = %s AND interval_end = %s
              AND consequence_type = %s
            """,
            self._cell_values(cell),
        )

    def public_cell_is_suppressed(self, cell: ProductCell) -> bool:
        row = self.connection.execute(
            """
            SELECT suppressed FROM der_flex_public_residuals
            WHERE tenant_id = %s AND zone_id = %s
              AND interval_start = %s AND interval_end = %s
              AND consequence_type = %s
            """,
            self._cell_values(cell),
        ).fetchone()
        return bool(row and row["suppressed"])


class PostgresReservationBackend:
    """Durable reservation state with transaction-scoped cross-process locks."""

    durable_outbox = True

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
                TRUNCATE der_flex_outbox, der_flex_instructions, der_flex_activations,
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

    def claim_outbox(
        self, *, now: datetime, limit: int, lease_seconds: int
    ) -> tuple[OutboxTask, ...]:
        locked_until = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection, connection.transaction():
            rows = connection.execute(
                """
                WITH candidates AS (
                    SELECT event_id
                    FROM der_flex_outbox
                    WHERE (
                        status IN ('PENDING', 'RETRY') AND available_at <= %s
                    ) OR (
                        status = 'PROCESSING' AND locked_until <= %s
                    )
                    ORDER BY created_at,
                        CASE event_type
                            WHEN 'activation.accepted' THEN 0
                            WHEN 'activation.started' THEN 1
                            WHEN 'pebc.instruction' THEN 2
                            ELSE 3
                        END,
                        event_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE der_flex_outbox AS outbox
                SET status = 'PROCESSING', attempts = outbox.attempts + 1,
                    locked_until = %s
                FROM candidates
                WHERE outbox.event_id = candidates.event_id
                RETURNING outbox.event_id, outbox.kind, outbox.destination,
                          outbox.event_type, outbox.payload, outbox.attempts,
                          outbox.activation_id, outbox.reservation_id,
                          outbox.allocation_index
                """,
                (now, now, limit, locked_until),
            ).fetchall()
        return tuple(
            OutboxTask(
                event_id=row["event_id"],
                kind=row["kind"],
                destination=row["destination"],
                event_type=row["event_type"],
                payload=dict(row["payload"]),
                attempts=row["attempts"],
                activation_id=row["activation_id"],
                reservation_id=row["reservation_id"],
                allocation_index=row["allocation_index"],
            )
            for row in rows
        )

    def resolve_outbox(
        self,
        task: OutboxTask,
        *,
        delivered: bool,
        retryable: bool,
        error: str | None,
        now: datetime,
        max_attempts: int,
    ) -> None:
        with self._connect() as connection, connection.transaction():
            row = connection.execute(
                """
                SELECT status, attempts FROM der_flex_outbox
                WHERE event_id = %s FOR UPDATE
                """,
                (task.event_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "PROCESSING"
                or row["attempts"] != task.attempts
            ):
                return

            terminal = delivered or not retryable or row["attempts"] >= max_attempts
            if delivered:
                status = "DELIVERED"
                available_at = now
            elif terminal:
                status = "DEAD"
                available_at = now
            else:
                status = "RETRY"
                delay_seconds = min(300, 2 ** max(0, row["attempts"] - 1))
                available_at = now + timedelta(seconds=delay_seconds)
            connection.execute(
                """
                UPDATE der_flex_outbox
                SET status = %s, available_at = %s, locked_until = NULL,
                    delivered_at = CASE WHEN %s THEN %s ELSE NULL END,
                    last_error = %s
                WHERE event_id = %s
                """,
                (
                    status,
                    available_at,
                    delivered,
                    now,
                    None if delivered else (error or "delivery rejected")[:1000],
                    task.event_id,
                ),
            )
            if task.kind == "RESOURCE" and terminal:
                self._finalize_activation(connection, task.activation_id, now)

    @staticmethod
    def _finalize_activation(
        connection: psycopg.Connection[dict[str, Any]],
        activation_id: uuid.UUID,
        now: datetime,
    ) -> None:
        activation_row = connection.execute(
            """
            SELECT a.*, r.callback_url
            FROM der_flex_activations a
            JOIN der_flex_reservations r USING (reservation_id)
            WHERE a.activation_id = %s FOR UPDATE OF a, r
            """,
            (activation_id,),
        ).fetchone()
        if activation_row is None or activation_row["status"] != "PENDING":
            return
        tasks = connection.execute(
            """
            SELECT allocation_index, status FROM der_flex_outbox
            WHERE activation_id = %s AND kind = 'RESOURCE'
            ORDER BY allocation_index
            """,
            (activation_id,),
        ).fetchall()
        if not tasks or any(row["status"] not in {"DELIVERED", "DEAD"} for row in tasks):
            return
        accepted_indices = [
            row["allocation_index"] for row in tasks if row["status"] == "DELIVERED"
        ]
        accepted_count = len(accepted_indices)
        rejected_count = len(tasks) - accepted_count
        result_status = "FAILED" if rejected_count else "COMPLETED"
        power_row = connection.execute(
            """
            SELECT COALESCE(SUM(power_kw), 0.0) AS accepted_power
            FROM der_flex_allocations
            WHERE reservation_id = %s AND allocation_index = ANY(%s)
            """,
            (activation_row["reservation_id"], accepted_indices),
        ).fetchone()
        connection.execute(
            """
            UPDATE der_flex_activations
            SET status = %s, accepted_instruction_count = %s,
                rejected_instruction_count = %s, completed_at = %s
            WHERE activation_id = %s
            """,
            (result_status, accepted_count, rejected_count, now, activation_id),
        )
        connection.execute(
            """
            UPDATE der_flex_reservations
            SET status = %s, allocated_power_kw = %s
            WHERE reservation_id = %s
            """,
            (
                result_status,
                float(power_row["accepted_power"]) if power_row else 0.0,
                activation_row["reservation_id"],
            ),
        )
        connection.execute(
            """
            DELETE FROM der_flex_allocations
            WHERE reservation_id = %s AND NOT (allocation_index = ANY(%s))
            """,
            (activation_row["reservation_id"], accepted_indices),
        )
        if activation_row["callback_url"]:
            completed = dict(activation_row)
            completed.update(
                status=result_status,
                accepted_instruction_count=accepted_count,
                rejected_instruction_count=rejected_count,
                completed_at=now,
            )
            payload = _activation(completed).model_dump(mode="json")
            event_type = (
                "activation.failed" if result_status == "FAILED" else "activation.completed"
            )
            event_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"der-flex:{activation_id}:webhook:{event_type}",
            )
            connection.execute(
                """
                INSERT INTO der_flex_outbox (
                    event_id, kind, destination, event_type, payload,
                    activation_id, reservation_id, allocation_index, available_at
                ) VALUES (%s, 'WEBHOOK', %s, %s, %s::jsonb, %s, %s, NULL, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event_id,
                    activation_row["callback_url"],
                    event_type,
                    json.dumps(payload, sort_keys=True),
                    activation_id,
                    activation_row["reservation_id"],
                    now,
                ),
            )
