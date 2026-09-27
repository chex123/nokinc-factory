"""Real-database administrative and unavailable-evidence edge cases."""

import pytest
from sqlalchemy import create_engine, text

from nokinc_factory.adapters.postgres_review_schema import grant_worker, require_postgres
from nokinc_factory.adapters.postgres_review_store import (
    PostgresReviewSessionStore,
    ReviewStoreDenied,
)
from nokinc_factory.domain.review_base import renewed
from tests.integration.test_postgres_review_store import database, setup_unit

__all__ = ["database"]


def test_existing_authority_is_idempotent_but_cannot_be_rewritten(database) -> None:
    store, initial, parent, admin = setup_unit(database)
    admin.provision(parent)
    admin.register(initial.seed, parent.budget_id)
    with pytest.raises(ReviewStoreDenied, match="cannot change"):
        admin.register(renewed(initial.seed, policy=renewed(initial.seed.policy,
            policy_version="changed")), parent.budget_id)
    with pytest.raises(ReviewStoreDenied, match="parent"):
        admin.register(initial.seed, "missing-parent")
    assert store.load_current(initial.seed.scope) == initial


def test_budget_identity_collision_is_denied_and_rolled_back(database) -> None:
    store, initial, parent, admin = setup_unit(database)
    with pytest.raises(ReviewStoreDenied, match="already provisioned"):
        admin.provision(renewed(parent, work_item_id="different-work-item"))
    assert store.usage(parent.budget_id).invocations == 0


def test_owner_connection_is_never_accepted_as_worker(database) -> None:
    store, initial, _, _ = setup_unit(database)
    privileged = PostgresReviewSessionStore(database[0], grant=store.grant)
    with pytest.raises(ReviewStoreDenied, match="bypass"):
        privileged.load_current(initial.seed.scope)
    with database[0].connect() as connection:
        owner = connection.scalar(text("SELECT current_user"))
    with pytest.raises(ValueError, match="bypass"):
        grant_worker(database[0], owner)


def test_worker_role_identifier_rejects_sql_fragments(database) -> None:
    with pytest.raises(ValueError, match="identifier"):
        grant_worker(database[0], "worker; SELECT 1")


def test_non_postgres_dialect_is_rejected_without_database_execution() -> None:
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(ValueError, match="PostgreSQL"):
            require_postgres(engine)
    finally:
        engine.dispose()


def test_approved_but_uncreated_unit_has_no_fabricated_session(database) -> None:
    store, initial, parent, admin = setup_unit(database)
    scope = renewed(initial.seed.scope, session_id="not-started")
    seed = renewed(initial.seed, scope=scope,
                   initial_artifact=renewed(initial.artifact, scope=scope))
    admin.register(seed, parent.budget_id)
    assert store.load_current(scope) is None
    with pytest.raises(ReviewStoreDenied, match="unavailable"):
        store.usage("absent-parent")


def test_foreign_proposal_is_rejected_before_tenant_database_access(database) -> None:
    store, initial, parent, _ = setup_unit(database)
    with pytest.raises(ReviewStoreDenied, match="scope mismatch"):
        store.compare_and_reserve(
            scope=renewed(initial.seed.scope, session_id="wrong"), expected_digest=None,
            updated=initial, parent_budget_id=parent.budget_id,
        )