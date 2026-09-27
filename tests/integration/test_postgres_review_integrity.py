"""Adversarial database regressions from independent store review."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

import nokinc_factory.adapters.postgres_review_store as store_module
from nokinc_factory.adapters.postgres_review_store import ReviewStoreDenied
from nokinc_factory.domain.review import Reservation
from nokinc_factory.policy.review import reserve_invocation
from tests.integration.test_postgres_review_store import (
    at,
    begin,
    database,
    finish,
    persist,
    report,
    sessions,
    setup_unit,
)
from tests.unit.test_review_fixtures import receipt

__all__ = ["database"]


def test_identical_concurrent_reservations_grant_one_dispatch_right(database) -> None:
    store, initial, parent, _ = setup_unit(database)
    pending = begin(initial, 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: persist(store, initial, pending, parent), range(2)))
    assert sorted(outcomes) == [False, True]


@pytest.mark.parametrize("claim", ["invocation", "execution"])
def test_global_claim_cannot_be_reused_across_registered_tenants(database, claim) -> None:
    one, first, parent1, _ = setup_unit(database)
    two, second, parent2, _ = setup_unit(database)
    common = "shared-" + first.seed.scope.tenant_id

    def pending(s):
        return reserve_invocation(
            s, invocation_id=common if claim == "invocation" else s.seed.scope.tenant_id,
            kind="REVIEW", actor=s.seed.reviewers[0],
            reservation=Reservation(tokens=100, cost_microusd=100, seconds=10), now=at(0),
        )

    a, b = pending(first), pending(second)
    assert persist(one, first, a, parent1)
    if claim == "invocation":
        with pytest.raises(ReviewStoreDenied):
            persist(two, second, b, parent2)
        assert two.usage(parent2.budget_id).invocations == 0
    else:
        assert persist(two, second, b, parent2)
        accepted = finish(a, report(a), proof=receipt(a, execution_id=common))
        replayed = finish(b, report(b), proof=receipt(b, execution_id=common))
        assert persist(one, a, accepted, parent1)
        with pytest.raises(ReviewStoreDenied):
            persist(two, b, replayed, parent2)
        assert two.load_current(second.seed.scope) == b
        assert two.usage(parent2.budget_id).tokens_reserved == 100


@pytest.mark.parametrize("operation", ["load", "cas"])
def test_restored_snapshot_cannot_override_append_only_event_tip(database, operation) -> None:
    store, initial, parent, _ = setup_unit(database)
    pending = begin(initial, 1)
    assert persist(store, initial, pending, parent)
    with database[0].begin() as connection:
        connection.execute(sessions.update().where(
            sessions.c.tenant_id == initial.seed.scope.tenant_id,
        ).values(document=initial.model_dump_json(), digest=initial.content_digest, sequence=1))
    with pytest.raises(ReviewStoreDenied, match="(?i)audit|tip|snapshot"):
        if operation == "load":
            store.load_current(initial.seed.scope)
        else:
            persist(store, initial, begin(initial, 2), parent)


@pytest.mark.parametrize("seconds", [-20, 20])
def test_reservation_time_is_checked_against_database_wall_clock(database, seconds) -> None:
    store, initial, parent, _ = setup_unit(database)
    # Reconstruct an approved time window containing the supplied time, while
    # the database remains the authority about whether dispatch is current.
    from nokinc_factory.domain.review_base import renewed
    from nokinc_factory.policy.review import start_session
    earlier_scope = renewed(initial.seed.scope, session_id="clock-unit")
    s = start_session(
        scope=earlier_scope, contract=initial.seed.contract, policy=initial.seed.policy,
        original_doer=initial.seed.original_doer, reviewers=initial.seed.reviewers,
        artifact=renewed(initial.artifact, scope=earlier_scope), now=at(0) - timedelta(seconds=30),
    )
    from nokinc_factory.adapters.postgres_review_admin import PostgresReviewAdmin
    PostgresReviewAdmin(database[0]).register(s.seed, parent.budget_id)
    assert store.compare_and_reserve(scope=earlier_scope, expected_digest=None,
                                     updated=s, parent_budget_id=parent.budget_id)
    pending = reserve_invocation(
        s, invocation_id=s.seed.scope.tenant_id + "clock", kind="REVIEW",
        actor=s.seed.reviewers[0],
        reservation=Reservation(tokens=100, cost_microusd=100, seconds=5),
        now=at(seconds),
    )
    with pytest.raises(ReviewStoreDenied, match="(?i)time|expired"):
        persist(store, s, pending, parent)


def test_reservation_admission_keeps_room_for_unavailable_settlement(database, monkeypatch) -> None:
    store, initial, parent, _ = setup_unit(database)
    pending = begin(initial, 1)
    # An admission envelope needs room for the receipt and accounting result.
    monkeypatch.setattr(store_module, "MAX_SESSION_BYTES", len(pending.model_dump_json()) + 1,
                        raising=False)
    with pytest.raises(ReviewStoreDenied, match="(?i)byte|headroom"):
        persist(store, initial, pending, parent)
    assert store.usage(parent.budget_id).tokens_reserved == 0


@pytest.mark.parametrize("columns", [None, "execution_id"])
def test_inherited_audit_update_privilege_is_rejected(database, columns) -> None:
    store, initial, _, _ = setup_unit(database)
    group = "audit_group_" + uuid4().hex
    with database[1].connect() as connection:
        worker_role = connection.scalar(text("SELECT current_user"))
    quote = database[0].dialect.identifier_preparer.quote
    group_sql, worker_sql = quote(group), quote(worker_role)
    update = "UPDATE" if columns is None else "UPDATE (execution_id)"
    try:
        with database[0].begin() as connection:
            connection.execute(text(f"CREATE ROLE {group_sql}"))
            connection.execute(text(f"GRANT {update} ON factory_review_event TO {group_sql}"))
            connection.execute(text(f"GRANT {group_sql} TO {worker_sql}"))
        with pytest.raises(ReviewStoreDenied, match="unsafe"):
            store.load_current(initial.seed.scope)
    finally:
        with database[0].begin() as connection:
            connection.execute(text(f"REVOKE {group_sql} FROM {worker_sql}"))
            connection.execute(text(f"REVOKE ALL ON factory_review_event FROM {group_sql}"))
            connection.execute(text(f"DROP ROLE {group_sql}"))
    assert store.load_current(initial.seed.scope) == initial