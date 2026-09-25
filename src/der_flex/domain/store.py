from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from threading import RLock

from der_flex.domain.models import ConsequenceType, FlexibilityAggregate, FlexibilityOffer


class StaleOfferError(ValueError):
    """An older source sequence attempted to replace a newer offer."""


class OfferVersionConflict(ValueError):
    """The same source sequence was reused with different offer content."""


class InMemoryOfferStore:
    """MVP storage boundary. Replacing it must not change domain or API models."""

    def __init__(self, minimum_participants: int = 1) -> None:
        if minimum_participants < 1:
            raise ValueError("minimum_participants must be positive")
        self.minimum_participants = minimum_participants
        self._lock = RLock()
        self._offers: dict[tuple[str, str, datetime, str], FlexibilityOffer] = {}
        self._source_positions: dict[str, tuple[int, int]] = {}
        self._disconnected_resources: set[str] = set()
        self._published_cohorts: dict[
            tuple[str, datetime, datetime, ConsequenceType], frozenset[str]
        ] = {}
        self._suppressed_cells: set[
            tuple[str, datetime, datetime, ConsequenceType]
        ] = set()

    def upsert(self, offer: FlexibilityOffer) -> None:
        key = (
            offer.resource_id,
            offer.zone_id,
            offer.interval_start,
            offer.consequence_type,
        )
        cell = (
            offer.zone_id,
            offer.interval_start,
            offer.interval_end,
            offer.consequence_type,
        )
        with self._lock:
            incoming_position = (offer.source_epoch, offer.source_sequence)
            source_position = self._source_positions.get(offer.resource_id)
            if (
                offer.resource_id in self._disconnected_resources
                and source_position is not None
                and incoming_position <= source_position
            ):
                raise StaleOfferError(
                    "a disconnected resource must advance its source position"
                )
            if source_position is not None and incoming_position < source_position:
                raise StaleOfferError(
                    f"offer position {incoming_position} is older than {source_position}"
                )
            if source_position is not None and incoming_position > source_position:
                obsolete_keys = [
                    existing_key
                    for existing_key, existing_offer in self._offers.items()
                    if existing_offer.resource_id == offer.resource_id
                    and (existing_offer.source_epoch, existing_offer.source_sequence)
                    < incoming_position
                ]
                for obsolete_key in obsolete_keys:
                    obsolete = self._offers.pop(obsolete_key)
                    obsolete_cell = (
                        obsolete.zone_id,
                        obsolete.interval_start,
                        obsolete.interval_end,
                        obsolete.consequence_type,
                    )
                    if obsolete_cell in self._published_cohorts:
                        self._suppressed_cells.add(obsolete_cell)
            self._source_positions[offer.resource_id] = incoming_position
            self._disconnected_resources.discard(offer.resource_id)
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

    def remove_resource(self, resource_id: str) -> int:
        with self._lock:
            keys = [key for key in self._offers if key[0] == resource_id]
            for key in keys:
                offer = self._offers[key]
                cell = (
                    offer.zone_id,
                    offer.interval_start,
                    offer.interval_end,
                    offer.consequence_type,
                )
                if cell in self._published_cohorts:
                    self._suppressed_cells.add(cell)
                del self._offers[key]
            if keys:
                self._disconnected_resources.add(resource_id)
            return len(keys)

    def zones(self, *, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        counts: dict[str, set[str]] = defaultdict(set)
        with self._lock:
            offers = tuple(self._offers.values())
        for offer in offers:
            if offer.expires_at >= current:
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
        now: datetime | None = None,
    ) -> list[FlexibilityAggregate]:
        generated_at = now or datetime.now(UTC)
        groups: dict[tuple[datetime, datetime, ConsequenceType], list[FlexibilityOffer]] = (
            defaultdict(list)
        )

        with self._lock:
            for offer in self._offers.values():
                if offer.zone_id != zone_id or offer.expires_at < generated_at:
                    continue
                if offer.interval_start < start or offer.interval_end > end:
                    continue
                groups[(offer.interval_start, offer.interval_end, offer.consequence_type)].append(
                    offer
                )

            result: list[FlexibilityAggregate] = []
            for (interval_start, interval_end, consequence_type), offers in sorted(groups.items()):
                cell = (zone_id, interval_start, interval_end, consequence_type)
                cohort = frozenset(offer.resource_id for offer in offers)
                previous_cohort = self._published_cohorts.get(cell)
                if previous_cohort is not None and previous_cohort != cohort:
                    self._suppressed_cells.add(cell)
                if any(
                    published_zone == zone_id
                    and published_consequence == consequence_type
                    and (
                        published_end == interval_start
                        or interval_end == published_start
                    )
                    and published_cohort != cohort
                    for (
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
                        offer.confidence
                        * (offer.upward_capacity_kw + offer.downward_capacity_kw)
                        for offer in offers
                    )
                    / total_weight
                    if total_weight
                    else min(offer.confidence for offer in offers)
                )
                result.append(
                    FlexibilityAggregate(
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
