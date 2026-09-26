from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from der_flex.domain.models import ConsequenceType, FlexibilityAggregate, FlexibilityOffer
from der_flex.domain.resources import (
    DisabledResourceError,
    ProvisionedResource,
    ResourceProvisioningConflict,
    ResourceZoneMismatch,
    StaleProvisioningRecord,
    UnknownResourceError,
)
from der_flex.domain.store import OfferVersionConflict, StaleOfferError
from der_flex.locking import product_lock_key

DOMAIN_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS der_flex_schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS der_flex_resources (
    resource_id text PRIMARY KEY CHECK (resource_id <> ''),
    tenant_id text NOT NULL DEFAULT 'development' CHECK (tenant_id <> ''),
    zone_id text NOT NULL CHECK (zone_id <> ''),
    resource_type text NOT NULL CHECK (resource_type IN ('BATTERY', 'EVSE', 'HEAT_PUMP')),
    min_power_kw double precision NOT NULL,
    max_power_kw double precision NOT NULL,
    min_energy_kwh double precision NOT NULL,
    max_energy_kwh double precision NOT NULL,
    initial_energy_kwh double precision NOT NULL,
    ramp_rate_kw_per_min double precision NOT NULL,
    provisioning_version integer NOT NULL CHECK (provisioning_version >= 1),
    provisioned_at timestamptz NOT NULL,
    enabled boolean NOT NULL,
    CONSTRAINT der_flex_resource_power CHECK (
        min_power_kw >= -100000 AND max_power_kw <= 100000
        AND min_power_kw <= max_power_kw
    ),
    CONSTRAINT der_flex_resource_energy CHECK (
        min_energy_kwh >= 0 AND max_energy_kwh <= 1000000000
        AND min_energy_kwh <= initial_energy_kwh
        AND initial_energy_kwh <= max_energy_kwh
    ),
    CONSTRAINT der_flex_resource_ramp CHECK (
        ramp_rate_kw_per_min >= 0 AND ramp_rate_kw_per_min <= 100000
    )
);
CREATE TABLE IF NOT EXISTS der_flex_offer_sources (
    resource_id text PRIMARY KEY REFERENCES der_flex_resources(resource_id),
    source_epoch bigint NOT NULL CHECK (source_epoch >= 0),
    source_sequence bigint NOT NULL CHECK (source_sequence >= 0),
    disconnected boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS der_flex_offers (
    resource_id text NOT NULL REFERENCES der_flex_resources(resource_id),
    tenant_id text NOT NULL DEFAULT 'development' CHECK (tenant_id <> ''),
    zone_id text NOT NULL CHECK (zone_id <> ''),
    interval_start timestamptz NOT NULL,
    interval_end timestamptz NOT NULL,
    baseline_power_kw double precision NOT NULL,
    upward_capacity_kw double precision NOT NULL,
    downward_capacity_kw double precision NOT NULL,
    upward_energy_kwh double precision NOT NULL,
    downward_energy_kwh double precision NOT NULL,
    confidence double precision NOT NULL,
    product_class text NOT NULL,
    consequence_type text NOT NULL,
    source_version text NOT NULL,
    source_epoch bigint NOT NULL,
    source_sequence bigint NOT NULL,
    observed_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (resource_id, zone_id, interval_start, consequence_type),
    CONSTRAINT der_flex_offer_interval CHECK (interval_end > interval_start),
    CONSTRAINT der_flex_offer_baseline CHECK (
        baseline_power_kw >= -100000000 AND baseline_power_kw <= 100000000
    ),
    CONSTRAINT der_flex_offer_capacity CHECK (
        upward_capacity_kw >= 0 AND upward_capacity_kw <= 1000000
        AND downward_capacity_kw >= 0 AND downward_capacity_kw <= 1000000
        AND upward_energy_kwh >= 0 AND upward_energy_kwh <= 1000000000
        AND downward_energy_kwh >= 0 AND downward_energy_kwh <= 1000000000
    ),
    CONSTRAINT der_flex_offer_confidence CHECK (confidence >= 0 AND confidence <= 1),
    CONSTRAINT der_flex_offer_product CHECK (product_class = 'BEST_EFFORT_PEBC'),
    CONSTRAINT der_flex_offer_consequence CHECK (consequence_type IN ('VANISH', 'DEFER')),
    CONSTRAINT der_flex_offer_position CHECK (source_epoch >= 0 AND source_sequence >= 0),
    CONSTRAINT der_flex_offer_expiry CHECK (expires_at >= interval_end)
);
CREATE INDEX IF NOT EXISTS der_flex_offer_query_idx
    ON der_flex_offers (zone_id, interval_start, interval_end, consequence_type, expires_at);
CREATE TABLE IF NOT EXISTS der_flex_published_cohorts (
    tenant_id text NOT NULL DEFAULT 'development' CHECK (tenant_id <> ''),
    zone_id text NOT NULL,
    interval_start timestamptz NOT NULL,
    interval_end timestamptz NOT NULL,
    consequence_type text NOT NULL,
    cohort jsonb NOT NULL CHECK (jsonb_typeof(cohort) = 'array'),
    suppressed boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, zone_id, interval_start, interval_end, consequence_type),
    CONSTRAINT der_flex_cohort_consequence CHECK (consequence_type IN ('VANISH', 'DEFER'))
);
CREATE TABLE IF NOT EXISTS der_flex_privacy_query_budgets (
    tenant_id text NOT NULL CHECK (tenant_id <> ''),
    window_start timestamptz NOT NULL,
    query_count integer NOT NULL CHECK (query_count > 0),
    PRIMARY KEY (tenant_id, window_start)
);
CREATE TABLE IF NOT EXISTS der_flex_privacy_snapshots (
    tenant_id text NOT NULL CHECK (tenant_id <> ''),
    zone_id text NOT NULL CHECK (zone_id <> ''),
    publication_epoch timestamptz NOT NULL,
    interval_start timestamptz NOT NULL,
    interval_end timestamptz NOT NULL,
    consequence_type text NOT NULL CHECK (consequence_type IN ('VANISH', 'DEFER')),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (
        tenant_id, zone_id, publication_epoch, interval_start, interval_end,
        consequence_type
    )
);
INSERT INTO der_flex_schema_migrations (version) VALUES (6)
ON CONFLICT (version) DO NOTHING;
INSERT INTO der_flex_schema_migrations (version) VALUES (2)
ON CONFLICT (version) DO NOTHING;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM der_flex_schema_migrations WHERE version = 5
    ) THEN
        ALTER TABLE der_flex_resources
            ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'development';
        ALTER TABLE der_flex_offers
            ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'development';
        ALTER TABLE der_flex_published_cohorts
            ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT 'development';
        ALTER TABLE der_flex_published_cohorts
            DROP CONSTRAINT IF EXISTS der_flex_published_cohorts_pkey;
        ALTER TABLE der_flex_published_cohorts
            ADD PRIMARY KEY (
                tenant_id, zone_id, interval_start, interval_end, consequence_type
            );
    END IF;
END $$;
INSERT INTO der_flex_schema_migrations (version) VALUES (5)
ON CONFLICT (version) DO NOTHING;
CREATE INDEX IF NOT EXISTS der_flex_offer_tenant_query_idx
    ON der_flex_offers (
        tenant_id, zone_id, interval_start, interval_end, consequence_type, expires_at
    );
"""


def _offer(row: dict[str, Any]) -> FlexibilityOffer:
    return FlexibilityOffer(**row)


def _resource(row: dict[str, Any]) -> ProvisionedResource:
    return ProvisionedResource(**row)


class _PostgresDomainBase:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    def initialize(self) -> None:
        # Imported lazily to keep the domain module independent at import time while
        # still applying the complete ordered schema for standalone callers.
        from der_flex.reservations.postgres import SCHEMA_MIGRATION_LOCK, SCHEMA_SQL

        with self._connect() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (SCHEMA_MIGRATION_LOCK,),
            )
            connection.execute(SCHEMA_SQL)
            connection.execute(DOMAIN_SCHEMA_SQL)
            connection.commit()

    def is_ready(self) -> bool:
        try:
            with self._connect() as connection:
                return connection.execute("SELECT 1").fetchone() is not None
        except psycopg.Error:
            return False

    @contextmanager
    def _transaction(
        self, lock_keys: tuple[str, ...] = ()
    ) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with self._connect() as connection, connection.transaction():
            for key in sorted(set(lock_keys)):
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (key,),
                )
            yield connection


class PostgresResourceRegistry(_PostgresDomainBase):
    """Operator-owned physical envelopes persisted with monotonic versions."""

    def __init__(self, dsn: str, records: Iterable[ProvisionedResource] = ()) -> None:
        super().__init__(dsn)
        self.initialize()
        for record in records:
            self.register(record)

    def register(self, record: ProvisionedResource) -> None:
        with self._transaction(
            ("offer-store", f"resource-registry:{record.resource_id}")
        ) as connection:
            row = connection.execute(
                "SELECT * FROM der_flex_resources WHERE resource_id = %s",
                (record.resource_id,),
            ).fetchone()
            current = _resource(row) if row else None
            if current is not None:
                if record.provisioning_version < current.provisioning_version:
                    raise StaleProvisioningRecord("provisioning version is older than current")
                if record.provisioning_version == current.provisioning_version:
                    if record == current:
                        return
                    raise ResourceProvisioningConflict(
                        "same provisioning version has conflicting content"
                    )
                offer_rows = connection.execute(
                    "SELECT * FROM der_flex_offers WHERE resource_id = %s",
                    (record.resource_id,),
                ).fetchall()
                cells = [PostgresOfferStore._cell(_offer(row)) for row in offer_rows]
                PostgresOfferStore._lock_cells(connection, cells)
                for cell in cells:
                    PostgresOfferStore._suppress(connection, cell)
                connection.execute(
                    "DELETE FROM der_flex_offers WHERE resource_id = %s",
                    (record.resource_id,),
                )
                if offer_rows:
                    connection.execute(
                        """
                        UPDATE der_flex_offer_sources SET disconnected = true
                        WHERE resource_id = %s
                        """,
                        (record.resource_id,),
                    )
            connection.execute(
                """
                INSERT INTO der_flex_resources (
                    resource_id, tenant_id, zone_id, resource_type, min_power_kw, max_power_kw,
                    min_energy_kwh, max_energy_kwh, initial_energy_kwh,
                    ramp_rate_kw_per_min, provisioning_version, provisioned_at, enabled
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (resource_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    zone_id = EXCLUDED.zone_id,
                    resource_type = EXCLUDED.resource_type,
                    min_power_kw = EXCLUDED.min_power_kw,
                    max_power_kw = EXCLUDED.max_power_kw,
                    min_energy_kwh = EXCLUDED.min_energy_kwh,
                    max_energy_kwh = EXCLUDED.max_energy_kwh,
                    initial_energy_kwh = EXCLUDED.initial_energy_kwh,
                    ramp_rate_kw_per_min = EXCLUDED.ramp_rate_kw_per_min,
                    provisioning_version = EXCLUDED.provisioning_version,
                    provisioned_at = EXCLUDED.provisioned_at,
                    enabled = EXCLUDED.enabled
                """,
                (
                    record.resource_id,
                    record.tenant_id,
                    record.zone_id,
                    record.resource_type,
                    record.min_power_kw,
                    record.max_power_kw,
                    record.min_energy_kwh,
                    record.max_energy_kwh,
                    record.initial_energy_kwh,
                    record.ramp_rate_kw_per_min,
                    record.provisioning_version,
                    record.provisioned_at,
                    record.enabled,
                ),
            )

    def require(
        self, resource_id: str, zone_id: str, tenant_id: str = "development"
    ) -> ProvisionedResource:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM der_flex_resources WHERE resource_id = %s",
                (resource_id,),
            ).fetchone()
        if row is None:
            raise UnknownResourceError(f"resource {resource_id!r} is not provisioned")
        record = _resource(row)
        if not record.enabled:
            raise DisabledResourceError(f"resource {resource_id!r} is disabled")
        if record.tenant_id != tenant_id:
            raise UnknownResourceError(f"resource {resource_id!r} is not provisioned")
        if record.zone_id != zone_id:
            raise ResourceZoneMismatch(
                f"resource {resource_id!r} is not provisioned for zone {zone_id!r}"
            )
        return record


class PostgresOfferStore(_PostgresDomainBase):
    """Durable offer store preserving ordering and public-cohort privacy state."""

    def __init__(self, dsn: str, minimum_participants: int = 1) -> None:
        if minimum_participants < 1:
            raise ValueError("minimum_participants must be positive")
        super().__init__(dsn)
        self.minimum_participants = minimum_participants
        self.initialize()

    @staticmethod
    def _cell(
        offer: FlexibilityOffer,
    ) -> tuple[str, str, datetime, datetime, ConsequenceType]:
        return (
            offer.tenant_id,
            offer.zone_id,
            offer.interval_start,
            offer.interval_end,
            offer.consequence_type,
        )

    @staticmethod
    def _suppress(connection: psycopg.Connection[dict[str, Any]], cell: tuple[object, ...]) -> None:
        connection.execute(
            """
            UPDATE der_flex_published_cohorts SET suppressed = true
            WHERE tenant_id = %s AND zone_id = %s
              AND interval_start = %s AND interval_end = %s
              AND consequence_type = %s
            """,
            cell,
        )

    @staticmethod
    def _lock_cells(
        connection: psycopg.Connection[dict[str, Any]],
        cells: Iterable[tuple[str, str, datetime, datetime, ConsequenceType]],
    ) -> None:
        keys = {
            f"tenant:{tenant}:{product_lock_key(zone, start, end, consequence, direction)}"
            for tenant, zone, start, end, consequence in cells
            for direction in ("UPWARD", "DOWNWARD")
        }
        for key in sorted(keys):
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (key,),
            )

    def consume_privacy_query(self, tenant_id: str, window_start: datetime, limit: int) -> bool:
        with self._transaction(
            (f"privacy-budget:{tenant_id}:{window_start.isoformat()}",)
        ) as connection:
            row = connection.execute(
                """
                INSERT INTO der_flex_privacy_query_budgets (
                    tenant_id, window_start, query_count
                ) VALUES (%s, %s, 1)
                ON CONFLICT (tenant_id, window_start) DO UPDATE SET
                    query_count = der_flex_privacy_query_budgets.query_count + 1
                WHERE der_flex_privacy_query_budgets.query_count < %s
                RETURNING query_count
                """,
                (tenant_id, window_start, limit),
            ).fetchone()
            connection.execute(
                "DELETE FROM der_flex_privacy_query_budgets WHERE window_start < %s",
                (window_start - timedelta(days=7),),
            )
            return row is not None

    def publish_privacy_snapshot(
        self,
        *,
        tenant_id: str,
        zone_id: str,
        publication_epoch: datetime,
        start: datetime,
        end: datetime,
        candidates: list[FlexibilityAggregate],
    ) -> list[FlexibilityAggregate]:
        lock_key = f"privacy-snapshot:{tenant_id}:{zone_id}:{publication_epoch.isoformat()}"
        with self._transaction((lock_key,)) as connection:
            for candidate in candidates:
                connection.execute(
                    """
                    INSERT INTO der_flex_privacy_snapshots (
                        tenant_id, zone_id, publication_epoch, interval_start,
                        interval_end, consequence_type, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        tenant_id,
                        zone_id,
                        publication_epoch,
                        candidate.interval_start,
                        candidate.interval_end,
                        candidate.consequence_type,
                        Jsonb(
                            {
                                **candidate.model_dump(mode="json"),
                                "tenant_id": tenant_id,
                            }
                        ),
                    ),
                )
            rows = connection.execute(
                """
                SELECT payload FROM der_flex_privacy_snapshots
                WHERE tenant_id = %s AND zone_id = %s AND publication_epoch = %s
                  AND interval_start >= %s AND interval_end <= %s
                ORDER BY interval_start, interval_end, consequence_type
                """,
                (tenant_id, zone_id, publication_epoch, start, end),
            ).fetchall()
            connection.execute(
                "DELETE FROM der_flex_privacy_snapshots WHERE publication_epoch < %s",
                (publication_epoch - timedelta(days=7),),
            )
            return [FlexibilityAggregate.model_validate(row["payload"]) for row in rows]

    def upsert(self, offer: FlexibilityOffer) -> None:
        with self._transaction(("offer-store",)) as connection:
            self._lock_cells(connection, (self._cell(offer),))
            position_row = connection.execute(
                "SELECT * FROM der_flex_offer_sources WHERE resource_id = %s",
                (offer.resource_id,),
            ).fetchone()
            incoming_position = (offer.source_epoch, offer.source_sequence)
            source_position = (
                (int(position_row["source_epoch"]), int(position_row["source_sequence"]))
                if position_row
                else None
            )
            if (
                position_row
                and position_row["disconnected"]
                and source_position is not None
                and incoming_position <= source_position
            ):
                raise StaleOfferError("a disconnected resource must advance its source position")
            if source_position is not None and incoming_position < source_position:
                raise StaleOfferError(
                    f"offer position {incoming_position} is older than {source_position}"
                )
            if source_position is not None and incoming_position > source_position:
                obsolete_rows = connection.execute(
                    """
                    SELECT * FROM der_flex_offers
                    WHERE resource_id = %s
                      AND (source_epoch, source_sequence) < (%s, %s)
                    """,
                    (offer.resource_id, *incoming_position),
                ).fetchall()
                for obsolete_row in obsolete_rows:
                    self._suppress(connection, self._cell(_offer(obsolete_row)))
                connection.execute(
                    """
                    DELETE FROM der_flex_offers
                    WHERE resource_id = %s
                      AND (source_epoch, source_sequence) < (%s, %s)
                    """,
                    (offer.resource_id, *incoming_position),
                )
            connection.execute(
                """
                INSERT INTO der_flex_offer_sources (
                    resource_id, source_epoch, source_sequence, disconnected
                ) VALUES (%s, %s, %s, false)
                ON CONFLICT (resource_id) DO UPDATE SET
                    source_epoch = EXCLUDED.source_epoch,
                    source_sequence = EXCLUDED.source_sequence,
                    disconnected = false
                """,
                (offer.resource_id, *incoming_position),
            )
            previous_row = connection.execute(
                """
                SELECT * FROM der_flex_offers
                WHERE resource_id = %s AND zone_id = %s AND interval_start = %s
                  AND consequence_type = %s
                """,
                (
                    offer.resource_id,
                    offer.zone_id,
                    offer.interval_start,
                    offer.consequence_type,
                ),
            ).fetchone()
            previous = _offer(previous_row) if previous_row else None
            if previous == offer:
                return
            if previous is not None and (
                offer.source_epoch,
                offer.source_sequence,
            ) == (previous.source_epoch, previous.source_sequence):
                raise OfferVersionConflict(
                    f"offer position {incoming_position} has conflicting content"
                )
            self._suppress(connection, self._cell(offer))
            connection.execute(
                """
                INSERT INTO der_flex_offers (
                    resource_id, tenant_id, zone_id, interval_start, interval_end,
                    baseline_power_kw, upward_capacity_kw, downward_capacity_kw,
                    upward_energy_kwh, downward_energy_kwh, confidence, product_class,
                    consequence_type, source_version, source_epoch, source_sequence,
                    observed_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (resource_id, zone_id, interval_start, consequence_type)
                DO UPDATE SET
                    interval_end = EXCLUDED.interval_end,
                    baseline_power_kw = EXCLUDED.baseline_power_kw,
                    upward_capacity_kw = EXCLUDED.upward_capacity_kw,
                    downward_capacity_kw = EXCLUDED.downward_capacity_kw,
                    upward_energy_kwh = EXCLUDED.upward_energy_kwh,
                    downward_energy_kwh = EXCLUDED.downward_energy_kwh,
                    confidence = EXCLUDED.confidence,
                    product_class = EXCLUDED.product_class,
                    source_version = EXCLUDED.source_version,
                    source_epoch = EXCLUDED.source_epoch,
                    source_sequence = EXCLUDED.source_sequence,
                    observed_at = EXCLUDED.observed_at,
                    expires_at = EXCLUDED.expires_at
                """,
                (
                    offer.resource_id,
                    offer.tenant_id,
                    offer.zone_id,
                    offer.interval_start,
                    offer.interval_end,
                    offer.baseline_power_kw,
                    offer.upward_capacity_kw,
                    offer.downward_capacity_kw,
                    offer.upward_energy_kwh,
                    offer.downward_energy_kwh,
                    offer.confidence,
                    offer.product_class,
                    offer.consequence_type,
                    offer.source_version,
                    offer.source_epoch,
                    offer.source_sequence,
                    offer.observed_at,
                    offer.expires_at,
                ),
            )

    def remove_resource(self, resource_id: str, *, tenant_id: str = "development") -> int:
        with self._transaction(("offer-store",)) as connection:
            rows = connection.execute(
                "SELECT * FROM der_flex_offers WHERE resource_id = %s AND tenant_id = %s",
                (resource_id, tenant_id),
            ).fetchall()
            self._lock_cells(connection, (self._cell(_offer(row)) for row in rows))
            for row in rows:
                self._suppress(connection, self._cell(_offer(row)))
            connection.execute(
                "DELETE FROM der_flex_offers WHERE resource_id = %s AND tenant_id = %s",
                (resource_id, tenant_id),
            )
            if rows:
                connection.execute(
                    """
                    UPDATE der_flex_offer_sources SET disconnected = true
                    WHERE resource_id = %s
                    """,
                    (resource_id,),
                )
            return len(rows)

    def zones(self, *, tenant_id: str = "development", now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT zone_id FROM der_flex_offers
                WHERE tenant_id = %s AND expires_at >= %s
                GROUP BY zone_id
                HAVING COUNT(DISTINCT resource_id) >= %s
                ORDER BY zone_id
                """,
                (tenant_id, current, self.minimum_participants),
            ).fetchall()
        return [str(row["zone_id"]) for row in rows]

    def eligible_offers(
        self,
        *,
        zone_id: str,
        interval_start: datetime,
        interval_end: datetime,
        consequence_type: ConsequenceType,
        tenant_id: str = "development",
        now: datetime | None = None,
    ) -> list[FlexibilityOffer]:
        current = now or datetime.now(UTC)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM der_flex_offers
                WHERE tenant_id = %s AND zone_id = %s
                  AND interval_start = %s AND interval_end = %s
                  AND consequence_type = %s AND expires_at >= %s
                ORDER BY resource_id
                """,
                (
                    tenant_id,
                    zone_id,
                    interval_start,
                    interval_end,
                    consequence_type,
                    current,
                ),
            ).fetchall()
        return [_offer(row) for row in rows]

    def query(
        self,
        zone_id: str,
        start: datetime,
        end: datetime,
        *,
        tenant_id: str = "development",
        now: datetime | None = None,
    ) -> list[FlexibilityAggregate]:
        generated_at = now or datetime.now(UTC)
        with self._transaction(("offer-store",)) as connection:
            rows = connection.execute(
                """
                SELECT * FROM der_flex_offers
                WHERE tenant_id = %s AND zone_id = %s AND expires_at >= %s
                  AND interval_start >= %s AND interval_end <= %s
                ORDER BY interval_start, interval_end, consequence_type, resource_id
                """,
                (tenant_id, zone_id, generated_at, start, end),
            ).fetchall()
            groups: dict[tuple[datetime, datetime, ConsequenceType], list[FlexibilityOffer]] = (
                defaultdict(list)
            )
            for row in rows:
                offer = _offer(row)
                groups[(offer.interval_start, offer.interval_end, offer.consequence_type)].append(
                    offer
                )
            published_rows = connection.execute(
                """
                SELECT * FROM der_flex_published_cohorts
                WHERE tenant_id = %s AND zone_id = %s
                """,
                (tenant_id, zone_id),
            ).fetchall()
            published = {
                (
                    row["tenant_id"],
                    row["zone_id"],
                    row["interval_start"],
                    row["interval_end"],
                    row["consequence_type"],
                ): (frozenset(row["cohort"]), bool(row["suppressed"]))
                for row in published_rows
            }
            result: list[FlexibilityAggregate] = []
            for (interval_start, interval_end, consequence_type), offers in sorted(groups.items()):
                cell = (
                    tenant_id,
                    zone_id,
                    interval_start,
                    interval_end,
                    consequence_type,
                )
                cohort = frozenset(offer.resource_id for offer in offers)
                previous = published.get(cell)
                if previous is not None and previous[0] != cohort:
                    self._suppress(connection, cell)
                    previous = (previous[0], True)
                adjacent_change = any(
                    published_tenant == tenant_id
                    and published_zone == zone_id
                    and published_consequence == consequence_type
                    and (published_end == interval_start or interval_end == published_start)
                    and previous_cohort != cohort
                    for (
                        published_tenant,
                        published_zone,
                        published_start,
                        published_end,
                        published_consequence,
                    ), (previous_cohort, _suppressed) in published.items()
                )
                if adjacent_change:
                    self._suppress(connection, cell)
                participants = len(cohort)
                suppressed = (previous is not None and previous[1]) or adjacent_change
                if suppressed or participants < self.minimum_participants:
                    continue
                total_weight = sum(
                    item.upward_capacity_kw + item.downward_capacity_kw for item in offers
                )
                confidence = (
                    sum(
                        item.confidence * (item.upward_capacity_kw + item.downward_capacity_kw)
                        for item in offers
                    )
                    / total_weight
                    if total_weight
                    else min(item.confidence for item in offers)
                )
                result.append(
                    FlexibilityAggregate(
                        tenant_id=tenant_id,
                        zone_id=zone_id,
                        interval_start=interval_start,
                        interval_end=interval_end,
                        baseline_power_kw=round(sum(item.baseline_power_kw for item in offers), 6),
                        upward_capacity_kw=round(
                            sum(item.upward_capacity_kw for item in offers), 6
                        ),
                        downward_capacity_kw=round(
                            sum(item.downward_capacity_kw for item in offers), 6
                        ),
                        upward_energy_kwh=round(sum(item.upward_energy_kwh for item in offers), 6),
                        downward_energy_kwh=round(
                            sum(item.downward_energy_kwh for item in offers), 6
                        ),
                        participant_count=participants,
                        confidence=round(confidence, 6),
                        consequence_type=consequence_type,
                        generated_at=generated_at,
                    )
                )
                connection.execute(
                    """
                    INSERT INTO der_flex_published_cohorts (
                        tenant_id, zone_id, interval_start, interval_end,
                        consequence_type, cohort
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (
                        tenant_id, zone_id, interval_start, interval_end, consequence_type
                    )
                    DO UPDATE SET cohort = EXCLUDED.cohort
                    """,
                    (*cell, Jsonb(sorted(cohort))),
                )
            return result
