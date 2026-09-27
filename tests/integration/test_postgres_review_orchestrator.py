"""Actual PostgreSQL plus synthetic executors: workflow plumbing, not live AI qualification."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nokinc_factory.adapters.postgres_review_store import PostgresReviewSessionStore
from nokinc_factory.application.review import ReviewOrchestrator
from nokinc_factory.domain.review import Reservation
from nokinc_factory.domain.review_base import renewed
from nokinc_factory.ports.review_execution import ExecutionResult
from tests.integration.test_postgres_review_store import database, setup_unit
from tests.unit.test_review_fixtures import closure, finding, receipt, repair_report, report

__all__ = ["database"]


class SyntheticNativeExecutor:
    def __init__(self, store, initial, *, defect=False, invalid=False):
        self.store, self.observed = store, initial.artifact
        self.defect, self.invalid = defect, invalid
        self.calls = []
        self.crash = False

    def execute(self, request):
        current = self.store.load_current(request.invocation.binding.scope)
        assert current is not None
        assert current.state.pending == request.invocation
        self.calls.append(request.invocation.invocation_id)
        if self.crash:
            raise ConnectionError("synthetic interrupted provider response")
        if self.invalid:
            return ExecutionResult(receipt(current), "\ud800")
        if request.invocation.kind == "REPAIR":
            output = repair_report(current)
            self.observed = output.artifact
        elif self.defect and len(self.calls) == 1:
            output = report(current, findings=(finding(current),),
                            fail=True, verdict="CHANGES_REQUIRED")
        else:
            output = report(current, rechecks=closure(current))
        return ExecutionResult(receipt(current), output)


def runtime(store, initial, parent, executor, *, authorize=lambda invocation: None):
    return ReviewOrchestrator(
        store=store, executor=executor, scope=initial.seed.scope, parent_budget_id=parent.budget_id,
        reservation=Reservation(tokens=100, cost_microusd=100, seconds=10),
        clock=lambda: datetime.now(UTC), invocation_id=lambda: uuid4().hex,
        observe=lambda scope, key: executor.observed, authorize=authorize,
    )


@pytest.mark.parametrize("defect", [False, True])
def test_actual_store_runs_two_reviews_and_original_doer_repair(database, defect) -> None:
    store, initial, parent, _ = setup_unit(database)
    executor = SyntheticNativeExecutor(store, initial, defect=defect)
    result = runtime(store, initial, parent, executor).run()
    final = store.load_current(initial.seed.scope)
    assert result.decision.eligible
    assert final is not None and len(final.valid_completed_reports) == 2
    assert [attempt.invocation.kind for attempt in final.attempts] == (
        ["REVIEW", "REPAIR", "REVIEW"] if defect else ["REVIEW", "REVIEW"]
    )
    assert len(store.history(initial.seed.scope)) == (7 if defect else 5)
    assert store.usage(parent.budget_id).tokens_spent == (30 if defect else 20)
    assert store.usage(parent.budget_id).tokens_reserved == 0


def test_pause_then_new_worker_resumes_without_resetting_budgets(database) -> None:
    store, initial, parent, _ = setup_unit(database)
    executor = SyntheticNativeExecutor(store, initial)
    first = runtime(store, initial, parent, executor).run(max_steps=1)
    assert first.status == "PAUSED" and first.decision.completed_reviews == 1
    restarted = PostgresReviewSessionStore(database[1], grant=store.grant)
    result = runtime(restarted, initial, parent, executor).run()
    assert result.decision.eligible
    assert len(executor.calls) == 2
    assert restarted.usage(parent.budget_id).invocations == 2


def test_interrupted_provider_cannot_be_resubmitted_after_worker_restart(database) -> None:
    store, initial, parent, _ = setup_unit(database)
    executor = SyntheticNativeExecutor(store, initial)
    executor.crash = True
    with pytest.raises(ConnectionError, match="interrupted"):
        runtime(store, initial, parent, executor).run()
    executor.crash = False
    restarted = PostgresReviewSessionStore(database[1], grant=store.grant)
    result = runtime(restarted, initial, parent, executor).run()
    assert result.status == "AWAITING_SETTLEMENT"
    assert not result.decision.eligible
    assert len(executor.calls) == 1
    assert restarted.usage(parent.budget_id).tokens_reserved == 100


def test_bad_execution_output_becomes_durable_terminal_stop(database) -> None:
    store, initial, parent, _ = setup_unit(database)
    executor = SyntheticNativeExecutor(store, initial, invalid=True)
    result = runtime(store, initial, parent, executor).run()
    assert not result.decision.eligible
    assert "EXECUTION_INVALID" in result.decision.reasons
    assert len(executor.calls) == 1
    assert store.usage(parent.budget_id).tokens_spent == 10
    assert store.usage(parent.budget_id).tokens_reserved == 0
    assert runtime(store, initial, parent, executor).run().status == "FINISHED"
    assert len(executor.calls) == 1


def test_revocation_after_commit_is_settled_without_dispatch(database) -> None:
    store, initial, parent, _ = setup_unit(database)
    executor = SyntheticNativeExecutor(store, initial)
    calls = 0

    def authorize(invocation):
        nonlocal calls
        calls += 1
        if calls == 2:
            executor.observed = renewed(executor.observed, artifact_key="revoked-artifact")
            raise PermissionError("synthetic authority revoked")

    result = runtime(store, initial, parent, executor, authorize=authorize).run()
    assert not result.decision.eligible
    assert "AUTHORIZATION_REVOKED" in result.decision.reasons
    assert executor.calls == []
    assert store.usage(parent.budget_id).tokens_reserved == 0
    assert len(store.history(initial.seed.scope)) == 3