"""Coordinator review regressions: no stale dispatch or discarded stop evidence."""

from datetime import timedelta

import pytest
from test_review_fixtures import finding, receipt, report
from test_review_orchestrator import orchestrator

from nokinc_factory.domain.review_base import renewed
from nokinc_factory.ports.review_execution import ExecutionResult


@pytest.mark.parametrize("drift", ["artifact", "clock"])
def test_drift_during_reservation_never_dispatches(drift, monkeypatch) -> None:
    runner, store, executor = orchestrator()
    original = store.compare_and_reserve

    def reserve_then_drift(**kwargs):
        result = original(**kwargs)
        if kwargs["updated"].state.pending is not None:
            if drift == "artifact":
                executor.observed = renewed(executor.observed, artifact_key="changed")
            else:
                runner._clock.value += timedelta(seconds=30)
        return result

    monkeypatch.setattr(store, "compare_and_reserve", reserve_then_drift)
    result = runner.run()
    assert executor.calls == []
    assert not result.decision.eligible
    assert store.current.state.pending is None, "known non-dispatch must be durably settled"
    assert store.current.terminal_reasons


def test_revocation_after_reservation_is_a_durable_stop(monkeypatch) -> None:
    runner, store, executor = orchestrator()
    checked = 0

    def authorize(invocation):
        nonlocal checked
        checked += 1
        if checked == 2:
            raise PermissionError("revoked")

    monkeypatch.setattr(runner, "_authorize", authorize)
    result = runner.run()
    assert not result.decision.eligible
    assert executor.calls == []
    assert store.current.state.pending is None
    assert store.current.terminal_reasons


def test_unencodable_output_retains_actual_receipt_and_overrun(monkeypatch) -> None:
    runner, store, executor = orchestrator()

    def execute(request):
        executor.calls.append(request.invocation.kind)
        return ExecutionResult(receipt=receipt(store.current, tokens=150), output="\ud800")

    monkeypatch.setattr(executor, "execute", execute)
    result = runner.run()
    assert not result.decision.eligible
    assert store.current.budget.tokens_spent == 150
    assert store.current.state.pending is None
    assert executor.calls == ["REVIEW"]


@pytest.mark.parametrize("invalid", ["result", "receipt"])
def test_invalid_execution_objects_settle_without_completion_credit(invalid, monkeypatch) -> None:
    runner, store, executor = orchestrator()

    def execute(request):
        executor.calls.append(request.invocation.kind)
        if invalid == "result":
            return {"success": True}
        return ExecutionResult(receipt={"cost": 0}, output=None)

    monkeypatch.setattr(executor, "execute", execute)
    result = runner.run()
    assert not result.decision.eligible
    assert not store.current.valid_completed_reports
    assert store.current.state.pending is None
    assert executor.calls == ["REVIEW"]


def test_oversized_critical_report_cannot_be_replaced_by_later_clean_reviews(monkeypatch) -> None:
    runner, store, executor = orchestrator()
    original = executor.execute

    def execute(request):
        if not executor.calls:
            executor.calls.append(request.invocation.kind)
            unsafe = report(store.current, findings=(finding(store.current, severity="CRITICAL"),))
            unsafe = renewed(unsafe, raw_text="padding" * 20_000)
            return ExecutionResult(receipt=receipt(store.current), output=unsafe)
        return original(request)

    monkeypatch.setattr(executor, "execute", execute)
    result = runner.run()
    assert not result.decision.eligible
    assert executor.calls == ["REVIEW"]
    assert store.current.state.pending is None
    assert store.current.terminal_reasons


def test_commit_then_lost_ack_resumes_without_repeating_that_invocation(monkeypatch) -> None:
    runner, store, executor = orchestrator()
    original = store.compare_and_reserve
    raised = False

    def commit_then_raise(**kwargs):
        nonlocal raised
        result = original(**kwargs)
        if kwargs["updated"].state.pending is None and not raised:
            raised = True
            raise ConnectionError("ack lost after commit")
        return result

    monkeypatch.setattr(store, "compare_and_reserve", commit_then_raise)
    with pytest.raises(ConnectionError):
        runner.run()
    assert len(store.current.valid_completed_reports) == 1
    assert runner.run().decision.eligible
    assert executor.calls == ["REVIEW", "REVIEW"]
    assert len(set(store.current.state.invocation_ids)) == 2