"""Protocol-independent domain model."""

from der_flex.domain.models import Activation, FlexibilityAggregate, FlexibilityOffer, Reservation
from der_flex.domain.postgres import PostgresOfferStore, PostgresResourceRegistry
from der_flex.domain.resources import (
    DisabledResourceError,
    InMemoryResourceRegistry,
    ProvisionedResource,
    ResourceProvisioningConflict,
    ResourceRegistry,
    ResourceRegistryError,
    ResourceZoneMismatch,
    StaleProvisioningRecord,
    UnknownResourceError,
)
from der_flex.domain.store import (
    InMemoryOfferStore,
    OfferStore,
    OfferVersionConflict,
    StaleOfferError,
)

__all__ = [
    "Activation",
    "FlexibilityAggregate",
    "FlexibilityOffer",
    "DisabledResourceError",
    "InMemoryOfferStore",
    "InMemoryResourceRegistry",
    "OfferVersionConflict",
    "OfferStore",
    "PostgresOfferStore",
    "PostgresResourceRegistry",
    "ProvisionedResource",
    "Reservation",
    "ResourceProvisioningConflict",
    "ResourceRegistry",
    "ResourceRegistryError",
    "ResourceZoneMismatch",
    "StaleOfferError",
    "StaleProvisioningRecord",
    "UnknownResourceError",
]
