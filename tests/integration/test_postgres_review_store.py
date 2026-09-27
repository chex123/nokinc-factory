"""Real PostgreSQL storage contracts; never substitute SQLite for RLS/locking.

Supply FACTORY_TEST_DATABASE_URL (a disposable database owner) and
FACTORY_TEST_WORKER_URL (a separate nonowner role) securely in the environment.
Absent infrastructure is an explicit skip in the general suite, not a release
qualification pass. This suite does not call a model or production provider.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError

from nokinc_factory.adapters.postgres_review_schema import (
    budgets,
    create_schema,
    events,
    grant_worker,
    registrations,
    sessions,
)
from nokinc_factory.adapters.postgres_review_store import (
    PostgresReviewAdmin,
    PostgresReviewSessionStore,
    ReviewStoreDenied,
)
from nokinc_factory.domain.review_base import renewed
from nokinc_factory.domain.review_budget import ParentBudget, ReviewStoreGrant

# This suite reuses pure synthetic model facts, not authenticated provider claims.
from tests.unit.test_review_fixtures import finish, report, session


def at(seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


def begin(initial, index):
    from nokinc_factory.domain.review import Reservation
    from nokinc_factory.policy.review import reserve_invocation
    return reserve_invocation(
        initial,
        invocation_id=f"{initial.seed.scope.tenant_id}:{initial.seed.scope.session_id}:{index}",
        kind="REVIEW", actor=initial.seed.reviewers[0],
        reservation=Reservation(tokens=100, cost_microusd=100, seconds=10), now=at(0),
    )


@pytest.fixture
def database():
    owner_url = os.environ.get("FACTORY_TEST_DATABASE_URL")
    worker_url = os.environ.get("FACTORY_TEST_WORKER_URL")
    if not owner_url or not worker_url:
        pytest.skip("Disposable PostgreSQL owner/worker URLs are required")
    owner, worker = create_engine(owner_url), create_engine(worker_url)
    create_schema(owner)
    with worker.connect() as connection:
        role = connection.scalar(text("SELECT current_user"))
    grant_worker(owner, str(role))
    yield owner, worker
    worker.dispose()
    owner.dispose()


def setup_unit(database, *, caps=None):
    owner, worker = database
    tenant = "tenant-" + uuid4().hex
    original = session()
    scope = renewed(original.seed.scope, tenant_id=tenant)
    contract = renewed(original.seed.contract, tenant_id=tenant)
    artifact = renewed(original.artifact, scope=scope, contract_digest=contract.content_digest)
    from nokinc_factory.policy.review import start_session
    initial = start_session(
        scope=scope, contract=contract, policy=original.seed.policy,
        original_doer=original.seed.original_doer, reviewers=original.seed.reviewers,
        artifact=artifact, now=at(0),
    )
    parent = ParentBudget.model_validate(dict(
        tenant_id=tenant, work_item_id=scope.work_item_id, budget_id="story-budget",
        max_invocations=8, max_repairs=3, max_tokens=500, max_cost_microusd=500,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    ) | (caps or {}))
    admin = PostgresReviewAdmin(owner)
    admin.provision(parent)
    admin.register(initial.seed, parent.budget_id)
    store = PostgresReviewSessionStore(worker, grant=ReviewStoreGrant(
        tenant_id=tenant, subject_id="synthetic-worker"))
    assert store.compare_and_reserve(scope=scope, expected_digest=None,
                                     updated=initial, parent_budget_id=parent.budget_id)
    return store, initial, parent, admin


def persist(store, before, after, parent):
    return store.compare_and_reserve(scope=before.seed.scope,
                                    expected_digest=before.content_digest,
                                    updated=after, parent_budget_id=parent.budget_id)


def test_create_reserve_complete_and_restart_are_durable(database) -> None:
    store, initial, parent, _ = setup_unit(database)
    pending = begin(initial, 1)
    assert persist(store, initial, pending, parent)
    finished = finish(pending, report(pending))
    assert persist(store, pending, finished, parent)
    restarted = PostgresReviewSessionStore(database[1], grant=store.grant)
    assert restarted.load_current(initial.seed.scope) == finished
    usage = restarted.usage(parent.budget_id)
    assert (usage.invocations, usage.tokens_spent, usage.tokens_reserved) == (1, 10, 0)
    assert len(restarted.history(initial.seed.scope)) == 3


def test_atomic_cas_competing_reservations_execute_only_once(database) -> None:
    store, initial, parent, _ = setup_unit(database)
    one, two = begin(initial, 1), begin(initial, 2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda candidate: persist(store, initial, candidate, parent),
                               (one, two)))
    assert sorted(results) == [False, True]
    assert store.usage(parent.budget_id).invocations == 1


def test_identical_retry_does_not_charge_twice_or_append_event(database) -> None:
    store, initial, parent, _ = setup_unit(database)
    pending = begin(initial, 1)
    assert persist(store, initial, pending, parent)
    assert not persist(store, initial, pending, parent), "replay cannot acquire dispatch rights"
    assert store.usage(parent.budget_id).tokens_reserved == 100
    assert len(store.history(initial.seed.scope)) == 2


def test_parent_budget_cannot_reset_with_new_unit_or_reparent(database) -> None:
    store, initial, parent, admin = setup_unit(database, caps={"max_invocations": 1})
    pending = begin(initial, 1)
    assert persist(store, initial, pending, parent)
    other_scope = renewed(initial.seed.scope, session_id="unit-2")
    other_artifact = renewed(initial.artifact, scope=other_scope)
    from nokinc_factory.policy.review import start_session
    other = start_session(
        scope=other_scope, contract=initial.seed.contract, policy=initial.seed.policy,
        original_doer=initial.seed.original_doer, reviewers=initial.seed.reviewers,
        artifact=other_artifact, now=at(0),
    )
    admin.register(other.seed, parent.budget_id)
    assert store.compare_and_reserve(scope=other_scope, expected_digest=None,
                                     updated=other, parent_budget_id=parent.budget_id)
    with pytest.raises(ReviewStoreDenied, match="budget"):
        persist(store, other, begin(other, 2), parent)
    assert store.load_current(other_scope) == other
    assert store.usage(parent.budget_id).invocations == 1
    with pytest.raises(ReviewStoreDenied):
        admin.provision(renewed(parent, budget_id="reset"))


def test_missing_registration_and_foreign_tenant_are_denied(database) -> None:
    store, initial, parent, _ = setup_unit(database)
    with pytest.raises(ReviewStoreDenied):
        store.load_current(renewed(initial.seed.scope, tenant_id="somebody-else"))
    with pytest.raises(ReviewStoreDenied):
        store.compare_and_reserve(scope=initial.seed.scope, expected_digest=initial.content_digest,
                                  updated=begin(initial, 1), parent_budget_id="foreign-budget")
    with pytest.raises(ReviewStoreDenied):
        store.load_current(renewed(initial.seed.scope, session_id="not-registered"))


def test_worker_cannot_read_other_tenants_or_rewrite_authority(database) -> None:
    store, initial, parent, _ = setup_unit(database)
    with database[1].begin() as connection:
        # A pooled connection with no transaction-local tenant must see nothing.
        assert connection.execute(select(sessions)).all() == []
        connection.execute(text("SELECT set_config('factory.tenant_id', :tenant, true)"),
                           {"tenant": store.grant.tenant_id})
        assert len(connection.execute(select(sessions)).all()) == 1
    with database[1].begin() as connection:
        assert connection.execute(select(sessions)).all() == []
    for statement in (
        budgets.update().values(limits_json="{}"),
        registrations.delete(), events.delete(), text("TRUNCATE factory_review_event"),
    ):
        with pytest.raises(DBAPIError), database[1].begin() as connection:
            connection.execute(statement)
    assert store.load_current(initial.seed.scope) == initial


def test_completion_after_exhaustion_records_actual_overrun(database) -> None:
    store, initial, parent, _ = setup_unit(database, caps={"max_tokens": 100})
    pending = begin(initial, 1)
    assert persist(store, initial, pending, parent)
    from tests.unit.test_review_fixtures import receipt
    completed = finish(pending, report(pending), proof=receipt(pending, tokens=150))
    assert persist(store, pending, completed, parent)
    assert store.usage(parent.budget_id).tokens_spent == 150
    assert store.usage(parent.budget_id).tokens_reserved == 0
    assert store.load_current(initial.seed.scope) == completed


def test_failure_after_writes_rolls_back_counters_session_and_event(database, monkeypatch) -> None:
    store, initial, parent, _ = setup_unit(database)

    def fail(*args, **kwargs):
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(store, "_append_event", fail)
    with pytest.raises(RuntimeError, match="injected"):
        persist(store, initial, begin(initial, 1), parent)
    assert store.load_current(initial.seed.scope) == initial
    assert store.usage(parent.budget_id).invocations == 0
    assert len(store.history(initial.seed.scope)) == 1