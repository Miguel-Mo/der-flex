from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from der_flex.api.app import create_app
from der_flex.domain.models import FlexibilityAggregate, FlexibilityOffer
from der_flex.domain.store import InMemoryOfferStore
from der_flex.privacy import PrivacyPublicationPolicy, PrivacyQueryRejected
from der_flex.security import AccessPrincipal, StaticBearerAuthenticator

START = datetime(2030, 1, 1, tzinfo=UTC)


def populated_store() -> InMemoryOfferStore:
    store = InMemoryOfferStore(minimum_participants=10)
    for index in range(12):
        for offset in (0, 15):
            interval_start = START + timedelta(minutes=offset)
            store.upsert(
                FlexibilityOffer(
                    tenant_id="tenant-a",
                    resource_id=f"resource-{index:02d}",
                    zone_id="north",
                    interval_start=interval_start,
                    interval_end=interval_start + timedelta(minutes=15),
                    baseline_power_kw=0.13,
                    upward_capacity_kw=1.19,
                    downward_capacity_kw=1.19,
                    upward_energy_kwh=0.31,
                    downward_energy_kwh=0.31,
                    confidence=0.97,
                    consequence_type="DEFER",
                    source_version="1",
                    source_epoch=1,
                    source_sequence=1,
                    observed_at=START,
                    expires_at=START + timedelta(hours=1),
                )
            )
    return store


def client(policy: PrivacyPublicationPolicy) -> TestClient:
    scopes = frozenset({"flexibility:read"})
    zones = frozenset({"north"})
    auth = StaticBearerAuthenticator(
        {
            "identity-one": AccessPrincipal("one", "tenant-a", scopes, zones),
            "identity-two": AccessPrincipal("two", "tenant-a", scopes, zones),
        }
    )
    return TestClient(create_app(populated_store(), authenticator=auth, privacy_policy=policy))


def tenant_authenticator() -> StaticBearerAuthenticator:
    scopes = frozenset({"flexibility:read"})
    zones = frozenset({"north"})
    return StaticBearerAuthenticator(
        {
            "identity-one": AccessPrincipal("one", "tenant-a", scopes, zones),
            "identity-two": AccessPrincipal("two", "tenant-a", scopes, zones),
        }
    )


def query(
    api: TestClient,
    token: str,
    start: datetime = START,
    end: datetime | None = None,
) -> Any:
    return api.get(
        "/api/v1/flexibility",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "zone_id": "north",
            "from": start.isoformat(),
            "to": (end or start + timedelta(minutes=30)).isoformat(),
        },
    )


def test_overlapping_windows_and_multiple_identities_receive_same_sticky_cells() -> None:
    policy = PrivacyPublicationPolicy(queries_per_interval=10)
    with client(policy) as api:
        broad = query(api, "identity-one")
        narrow = query(
            api,
            "identity-two",
            START + timedelta(minutes=15),
            START + timedelta(minutes=30),
        )
    assert broad.status_code == narrow.status_code == 200
    broad_second = broad.json()["data"][1]
    narrow_only = narrow.json()["data"][0]
    assert broad_second == narrow_only
    assert broad_second["participant_count"] == 10
    assert broad_second["upward_capacity_kw"] == 14.0
    assert broad_second["upward_energy_kwh"] == 3.5
    assert broad_second["confidence"] == 0.95
    generated = datetime.fromisoformat(broad_second["generated_at"])
    assert generated.minute % 15 == generated.second == generated.microsecond == 0


def test_change_within_publication_epoch_cannot_reveal_its_timing() -> None:
    policy = PrivacyPublicationPolicy(queries_per_interval=10)
    store = populated_store()
    app = create_app(
        store,
        authenticator=tenant_authenticator(),
        privacy_policy=policy,
    )
    with TestClient(app) as api:
        before = query(api, "identity-one")
        assert store.remove_resource("resource-00", tenant_id="tenant-a") == 2
        after = query(api, "identity-two")
    assert before.status_code == after.status_code == 200
    assert before.json() == after.json()


def test_query_budget_is_shared_by_all_identities_of_a_tenant() -> None:
    policy = PrivacyPublicationPolicy(queries_per_interval=2)
    with client(policy) as api:
        assert query(api, "identity-one").status_code == 200
        assert query(api, "identity-two").status_code == 200
        exhausted = query(api, "identity-one")
    assert exhausted.status_code == 429
    assert exhausted.headers["retry-after"] == "900"


def test_zone_catalog_does_not_reveal_current_sparse_or_missing_cohorts() -> None:
    policy = PrivacyPublicationPolicy(queries_per_interval=5)
    scopes = frozenset({"flexibility:read"})
    auth = StaticBearerAuthenticator(
        {
            "catalog-reader": AccessPrincipal(
                "reader", "tenant-a", scopes, frozenset({"north", "empty-zone"})
            )
        }
    )
    store = InMemoryOfferStore(minimum_participants=10)
    with TestClient(create_app(store, authenticator=auth, privacy_policy=policy)) as api:
        response = api.get(
            "/api/v1/flexibility/zones",
            headers={"Authorization": "Bearer catalog-reader"},
        )
    assert response.status_code == 200
    assert response.json() == {"data": ["empty-zone", "north"]}


def test_publication_windows_are_fixed_and_bounded_without_spending_budget() -> None:
    policy = PrivacyPublicationPolicy(queries_per_interval=1)
    with client(policy) as api:
        unaligned = query(api, "identity-one", START + timedelta(minutes=1))
        valid = query(api, "identity-two")
    assert unaligned.status_code == 422
    assert valid.status_code == 200


def test_quantisation_never_increases_nonnegative_capacity_or_energy() -> None:
    policy = PrivacyPublicationPolicy()
    raw = FlexibilityAggregate(
        tenant_id="tenant-a",
        zone_id="north",
        interval_start=START,
        interval_end=START + timedelta(minutes=15),
        baseline_power_kw=-1.6,
        upward_capacity_kw=1.99,
        downward_capacity_kw=2.01,
        upward_energy_kwh=0.49,
        downward_energy_kwh=0.51,
        participant_count=14,
        confidence=0.99,
        consequence_type="DEFER",
        generated_at=START,
    )
    public = policy.sanitize(raw, START)
    assert public.upward_capacity_kw <= raw.upward_capacity_kw
    assert public.downward_capacity_kw <= raw.downward_capacity_kw
    assert public.upward_energy_kwh <= raw.upward_energy_kwh
    assert public.downward_energy_kwh <= raw.downward_energy_kwh
    assert public.participant_count == 10


def test_auxiliary_exact_values_do_not_recover_the_last_raw_contribution() -> None:
    policy = PrivacyPublicationPolicy()
    with client(policy) as api:
        response = query(
            api,
            "identity-one",
            START,
            START + timedelta(minutes=15),
        )
    public_total = response.json()["data"][0]["upward_capacity_kw"]
    eleven_known_resources = 11 * 1.19
    inferred_last = public_total - eleven_known_resources
    assert inferred_last != 1.19
    assert inferred_last <= 1.19 < inferred_last + policy.power_resolution_kw


def test_policy_rejects_windows_larger_than_the_public_catalog() -> None:
    policy = PrivacyPublicationPolicy(max_window_hours=24)
    try:
        policy.validate_window(START, START + timedelta(hours=25))
    except PrivacyQueryRejected as error:
        assert error.code == "window_too_large"
    else:
        raise AssertionError("oversized privacy window was accepted")
