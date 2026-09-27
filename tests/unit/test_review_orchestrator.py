"""Coordinator contracts with synthetic execution, no model/provider authority claims."""

from datetime import timedelta

import pytest
from test_review_fixtures import (
    at,
    begin,
    closure,
    finding,
    receipt,
    repair_report,
    report,
    session,
)

from nokinc_factory.application.review import ReviewOrchestrator
from nokinc_factory.domain.review import Reservation
from nokinc_factory.domain.review_base import renewed
from nokinc_factory.ports.review_execution import ExecutionResult, ExecutorUnavailable


class LocalStore:
    """Test double ONLY. Production must use atomic authenticated persistence."""

    def __init__(self, initial):
        self.current = initial
        self.writes = []
        self.compete = False
        self.fail_completion = False

    def load_current(self, scope):
        return self.current

    def compare_and_reserve(self, *, scope, expected_digest, updated, parent_budget_id):
        if self.fail_completion and updated.state.pending is None:
            raise ConnectionError("commit outcome unknown")
        if self.compete or self.current.content_digest != expected_digest:
            return False
        self.current = updated
        self.writes.append(updated)
        return True


class SyntheticExecutor:
    def __init__(self, store, *, defect=False, advisory=False):
        self.store, self.defect, self.advisory = store, defect, advisory
        self.calls = []
        self.outage = False
        self.invalid = False
        self.observed = store.current.artifact

    def execute(self, request):
        pending = self.store.current
        assert pending.state.pending == request.invocation, "execute requires committed reservation"
        self.calls.append(request.invocation.kind)
        if self.outage:
            raise ExecutorUnavailable("synthetic outage")
        if self.invalid:
            return ExecutionResult(receipt=receipt(pending), output="not JSON")
        if request.invocation.kind == "REPAIR":
            output = repair_report(pending)
            self.observed = output.artifact
        elif len(self.calls) == 1 and (self.defect or self.advisory):
            output = report(pending, findings=(finding(pending, advisory=self.advisory),),
                            fail=self.defect,
                            verdict="CHANGES_REQUIRED" if self.defect else "ACCEPT")
        else:
            output = report(pending, rechecks=closure(pending))
        return ExecutionResult(receipt=receipt(pending), output=output)


class Clock:
    def __init__(self):
        self.value = at(0)

    def __call__(self):
        self.value += timedelta(seconds=1)
        return self.value


def orchestrator(*, defect=False, advisory=False):
    store = LocalStore(session())
    executor = SyntheticExecutor(store, defect=defect, advisory=advisory)
    counter = iter(range(100))
    runner = ReviewOrchestrator(
        store=store, executor=executor, scope=store.current.seed.scope, parent_budget_id="parent",
        reservation=Reservation(tokens=100, cost_microusd=100, seconds=10), clock=Clock(),
        invocation_id=lambda: f"orchestrated-{next(counter)}",
        observe=lambda scope, artifact_key: executor.observed,
        authorize=lambda invocation: None,
    )
    return runner, store, executor


def test_two_clean_reviews_finish_without_gratuitous_repair() -> None:
    runner, store, executor = orchestrator()
    result = runner.run()
    assert result.decision.eligible
    assert executor.calls == ["REVIEW", "REVIEW"]
    assert store.current.budget.invocations == 2
    assert len(store.writes) == 4


def test_doer_repairs_then_independent_reviewer_closes_exact_artifact() -> None:
    runner, store, executor = orchestrator(defect=True)
    initial_digest = store.current.artifact.content_digest
    result = runner.run()
    assert result.decision.eligible
    assert executor.calls == ["REVIEW", "REPAIR", "REVIEW"]
    assert store.current.artifact.content_digest != initial_digest
    assert store.current.open_findings == ()


def test_optional_advisory_does_not_force_a_repair() -> None:
    runner, store, executor = orchestrator(advisory=True)
    assert runner.run().decision.eligible
    assert executor.calls == ["REVIEW", "REVIEW"]


def test_persisted_pending_invocation_is_not_executed_again_after_restart() -> None:
    runner, store, executor = orchestrator()
    store.current = begin(store.current, 1)
    result = runner.run()
    assert result.status == "AWAITING_SETTLEMENT"
    assert executor.calls == []
    assert not result.decision.eligible


def test_losing_reservation_cas_never_dispatches() -> None:
    runner, store, executor = orchestrator()
    store.compete = True
    result = runner.run()
    assert result.status == "CONTENDED"
    assert executor.calls == []


def test_outage_charges_reservation_and_stops_at_repeated_failure_limit() -> None:
    runner, store, executor = orchestrator()
    executor.outage = True
    result = runner.run()
    assert not result.decision.eligible
    assert executor.calls == ["REVIEW", "REVIEW"]
    assert store.current.budget.tokens_spent == 200
    assert len(store.current.valid_completed_reports) == 0


def test_malformed_output_consumes_attempts_but_never_passes() -> None:
    runner, store, executor = orchestrator()
    executor.invalid = True
    result = runner.run()
    assert not result.decision.eligible
    assert len(executor.calls) == 2
    assert len(store.current.valid_completed_reports) == 0


def test_unknown_completion_commit_outcome_never_reexecutes_provider() -> None:
    runner, store, executor = orchestrator()
    store.fail_completion = True
    with pytest.raises(ConnectionError, match="unknown"):
        runner.run()
    assert executor.calls == ["REVIEW"]
    store.fail_completion = False
    assert runner.run().status == "AWAITING_SETTLEMENT"
    assert executor.calls == ["REVIEW"]


def test_already_eligible_session_does_not_call_models_again() -> None:
    runner, store, executor = orchestrator()
    assert runner.run().decision.eligible
    assert runner.run().decision.eligible
    assert executor.calls == ["REVIEW", "REVIEW"]


def test_live_artifact_drift_blocks_before_any_dispatch() -> None:
    runner, store, executor = orchestrator()
    executor.observed = renewed(executor.observed, artifact_key="different-artifact")
    assert runner.run().decision.status == "BLOCKED"
    assert executor.calls == []


def test_cooperative_batch_pause_preserves_review_count_for_resume() -> None:
    runner, store, executor = orchestrator()
    first = runner.run(max_steps=1)
    assert first.status == "PAUSED"
    assert not first.decision.eligible
    assert len(store.current.valid_completed_reports) == 1
    assert runner.run().decision.eligible
    assert executor.calls == ["REVIEW", "REVIEW"]


@pytest.mark.parametrize("steps", [0, -1, True, 65])
def test_invalid_batch_bounds_cannot_start_execution(steps) -> None:
    runner, store, executor = orchestrator()
    with pytest.raises(ValueError):
        runner.run(max_steps=steps)
    assert executor.calls == []