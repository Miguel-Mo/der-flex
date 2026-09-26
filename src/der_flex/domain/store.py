from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol

from der_flex.domain.models import ConsequenceType, FlexibilityAggregate, FlexibilityOffer


class StaleOfferError(ValueError):
    """An older source sequence attempted to replace a newer offer."""


class OfferVersionConflict(ValueError):
    """The same source sequence was reused with different offer content."""


class OfferStore(Protocol):
    minimum_participants: int

    def upsert(self, offer: FlexibilityOffer) -> None: ...

    def remove_resource(self, resource_id: str, *, tenant_id: str = "development") -> int: ...

    def zones(
        self, *, tenant_id: str = "development", now: datetime | None = None
    ) -> list[str]: ...

    def eligible_offers(
        self,
        *,
        zone_id: str,
        interval_start: datetime,
        interval_end: datetime,
        consequence_type: ConsequenceType,
        tenant_id: str = "development",
        now: datetime | None = None,
    ) -> list[FlexibilityOffer]: ...

    def query(
        self,
        zone_id: str,
        start: datetime,
        end: datetime,
        *,
        tenant_id: str = "development",
        now: datetime | None = None,
    ) -> list[FlexibilityAggregate]: ...

    def consume_privacy_query(self, tenant_id: str, window_start: datetime, limit: int) -> bool: ...

    def publish_privacy_snapshot(
        self,
        *,
        tenant_id: str,
        zone_id: str,
        publication_epoch: datetime,
        start: datetime,
        end: datetime,
        candidates: list[FlexibilityAggregate],
    ) -> list[FlexibilityAggregate]: ...


class InMemoryOfferStore:
    """MVP storage boundary. Replacing it must not change domain or API models."""

    def __init__(self, minimum_participants: int = 1) -> None:
        if minimum_participants < 1:
            raise ValueError("minimum_participants must be positive")
        self.minimum_participants = minimum_participants
        self._lock = RLock()
        self._offers: dict[tuple[str, str, str, datetime, str], FlexibilityOffer] = {}
        self._source_positions: dict[tuple[str, str], tuple[int, int]] = {}
        self._disconnected_resources: set[tuple[str, str]] = set()
        self._published_cohorts: dict[
            tuple[str, str, datetime, datetime, ConsequenceType], frozenset[str]
        ] = {}
        self._suppressed_cells: set[tuple[str, str, datetime, datetime, ConsequenceType]] = set()
        self._privacy_query_counts: dict[tuple[str, datetime], int] = {}
        self._privacy_snapshots: dict[
            tuple[str, str, datetime, datetime, datetime, ConsequenceType],
            FlexibilityAggregate,
        ] = {}

    def consume_privacy_query(self, tenant_id: str, window_start: datetime, limit: int) -> bool:
        key = (tenant_id, window_start)
        with self._lock:
            current = self._privacy_query_counts.get(key, 0)
            if current >= limit:
                return False
            self._privacy_query_counts[key] = current + 1
            return True

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
        with self._lock:
            for candidate in candidates:
                key = (
                    tenant_id,
                    zone_id,
                    publication_epoch,
                    candidate.interval_start,
                    candidate.interval_end,
                    candidate.consequence_type,
                )
                self._privacy_snapshots.setdefault(key, candidate)
            return sorted(
                (
                    aggregate
                    for (
                        snapshot_tenant,
                        snapshot_zone,
                        snapshot_epoch,
                        interval_start,
                        interval_end,
                        _consequence,
                    ), aggregate in self._privacy_snapshots.items()
                    if snapshot_tenant == tenant_id
                    and snapshot_zone == zone_id
                    and snapshot_epoch == publication_epoch
                    and interval_start >= start
                    and interval_end <= end
                ),
                key=lambda item: (
                    item.interval_start,
                    item.interval_end,
                    item.consequence_type,
                ),
            )

    def upsert(self, offer: FlexibilityOffer) -> None:
        key = (
            offer.tenant_id,
            offer.resource_id,
            offer.zone_id,
            offer.interval_start,
            offer.consequence_type,
        )
        cell = (
            offer.tenant_id,
            offer.zone_id,
            offer.interval_start,
            offer.interval_end,
            offer.consequence_type,
        )
        with self._lock:
            incoming_position = (offer.source_epoch, offer.source_sequence)
            resource_key = (offer.tenant_id, offer.resource_id)
            source_position = self._source_positions.get(resource_key)
            if (
                resource_key in self._disconnected_resources
                and source_position is not None
                and incoming_position <= source_position
            ):
                raise StaleOfferError("a disconnected resource must advance its source position")
            if source_position is not None and incoming_position < source_position:
                raise StaleOfferError(
                    f"offer position {incoming_position} is older than {source_position}"
                )
            if source_position is not None and incoming_position > source_position:
                obsolete_keys = [
                    existing_key
                    for existing_key, existing_offer in self._offers.items()
                    if existing_offer.resource_id == offer.resource_id
                    and existing_offer.tenant_id == offer.tenant_id
                    and (existing_offer.source_epoch, existing_offer.source_sequence)
                    < incoming_position
                ]
                for obsolete_key in obsolete_keys:
                    obsolete = self._offers.pop(obsolete_key)
                    obsolete_cell = (
                        obsolete.tenant_id,
                        obsolete.zone_id,
                        obsolete.interval_start,
                        obsolete.interval_end,
                        obsolete.consequence_type,
                    )
                    if obsolete_cell in self._published_cohorts:
                        self._suppressed_cells.add(obsolete_cell)
            self._source_positions[resource_key] = incoming_position
            self._disconnected_resources.discard(resource_key)
            previous = self._offers.get(key)
            if previous == offer:
                return
            if previous is not None and (
                offer.source_epoch,
                offer.source_sequence,
            ) == (previous.source_epoch, previous.source_sequence):
                raise OfferVersionConflict(
                    f"offer position {incoming_position} has conflicting content"
                )
            if cell in self._published_cohorts:
                self._suppressed_cells.add(cell)
            self._offers[key] = offer

    def remove_resource(self, resource_id: str, *, tenant_id: str = "development") -> int:
        with self._lock:
            keys = [
                key
                for key, offer in self._offers.items()
                if offer.resource_id == resource_id and offer.tenant_id == tenant_id
            ]
            for key in keys:
                offer = self._offers[key]
                cell = (
                    offer.tenant_id,
                    offer.zone_id,
                    offer.interval_start,
                    offer.interval_end,
                    offer.consequence_type,
                )
                if cell in self._published_cohorts:
                    self._suppressed_cells.add(cell)
                del self._offers[key]
            if keys:
                self._disconnected_resources.add((tenant_id, resource_id))
            return len(keys)

    def zones(self, *, tenant_id: str = "development", now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        counts: dict[str, set[str]] = defaultdict(set)
        with self._lock:
            offers = tuple(self._offers.values())
        for offer in offers:
            if offer.tenant_id == tenant_id and offer.expires_at >= current:
                counts[offer.zone_id].add(offer.resource_id)
        return sorted(
            zone
            for zone, participants in counts.items()
            if len(participants) >= self.minimum_participants
        )

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
        with self._lock:
            offers = tuple(self._offers.values())
        return sorted(
            (
                offer
                for offer in offers
                if offer.zone_id == zone_id
                and offer.tenant_id == tenant_id
                and offer.interval_start == interval_start
                and offer.interval_end == interval_end
                and offer.consequence_type == consequence_type
                and offer.expires_at >= current
            ),
            key=lambda offer: offer.resource_id,
        )

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
        groups: dict[tuple[datetime, datetime, ConsequenceType], list[FlexibilityOffer]] = (
            defaultdict(list)
        )

        with self._lock:
            for offer in self._offers.values():
                if (
                    offer.tenant_id != tenant_id
                    or offer.zone_id != zone_id
                    or offer.expires_at < generated_at
                ):
                    continue
                if offer.interval_start < start or offer.interval_end > end:
                    continue
                groups[(offer.interval_start, offer.interval_end, offer.consequence_type)].append(
                    offer
                )

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
                previous_cohort = self._published_cohorts.get(cell)
                if previous_cohort is not None and previous_cohort != cohort:
                    self._suppressed_cells.add(cell)
                if any(
                    published_tenant == tenant_id
                    and published_zone == zone_id
                    and published_consequence == consequence_type
                    and (published_end == interval_start or interval_end == published_start)
                    and published_cohort != cohort
                    for (
                        published_tenant,
                        published_zone,
                        published_start,
                        published_end,
                        published_consequence,
                    ), published_cohort in self._published_cohorts.items()
                ):
                    self._suppressed_cells.add(cell)
                participants = len(cohort)
                if cell in self._suppressed_cells or participants < self.minimum_participants:
                    continue
                total_weight = sum(
                    offer.upward_capacity_kw + offer.downward_capacity_kw for offer in offers
                )
                confidence = (
                    sum(
                        offer.confidence * (offer.upward_capacity_kw + offer.downward_capacity_kw)
                        for offer in offers
                    )
                    / total_weight
                    if total_weight
                    else min(offer.confidence for offer in offers)
                )
                result.append(
                    FlexibilityAggregate(
                        tenant_id=tenant_id,
                        zone_id=zone_id,
                        interval_start=interval_start,
                        interval_end=interval_end,
                        baseline_power_kw=round(
                            sum(offer.baseline_power_kw for offer in offers), 6
                        ),
                        upward_capacity_kw=round(
                            sum(offer.upward_capacity_kw for offer in offers), 6
                        ),
                        downward_capacity_kw=round(
                            sum(offer.downward_capacity_kw for offer in offers), 6
                        ),
                        upward_energy_kwh=round(
                            sum(offer.upward_energy_kwh for offer in offers), 6
                        ),
                        downward_energy_kwh=round(
                            sum(offer.downward_energy_kwh for offer in offers), 6
                        ),
                        participant_count=participants,
                        confidence=round(confidence, 6),
                        consequence_type=consequence_type,
                        generated_at=generated_at,
                    )
                )
                self._published_cohorts[cell] = cohort
            return result
