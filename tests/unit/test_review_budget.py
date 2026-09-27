"""Reservations precede calls; incomplete delivery and time never manufacture a pass."""

import json
from datetime import datetime

import pytest
from test_review_fixtures import (
    at,
    begin,
    clean,
    digest,
    finish,
    policy,
    receipt,
    report,
    session,
)

from nokinc_factory.domain.review import Reservation
from nokinc_factory.domain.review_session import InvocationReceipt
from nokinc_factory.policy.review import complete_invocation, evaluate


@pytest.mark.parametrize("output", [None, "", "not JSON", "{}", "{\"verdict\":\"ACCEPT\"}"])
def test_absent_or_malformed_reviews_consume_attempts_and_never_count(output: str | None) -> None:
    pending = begin(session(rules=policy(max_invocations=1)), 1)
    assert pending.budget.invocations == 1
    assert pending.budget.tokens_reserved == 100
    done = finish(pending, output)
    assert done.budget.tokens_spent == 10
    assert done.budget.tokens_reserved == 0
    assert done.valid_completed_reports == ()
    assert "INVOCATION_LIMIT" in done.terminal_reasons
    with pytest.raises(ValueError):
        begin(done, 2)


def test_unavailable_receipt_charges_the_full_reservation() -> None:
    pending = begin(session(), 1)
    done = complete_invocation(pending, invocation_id="call-1", receipt=None,
                               output=report(pending), now=at(4))
    assert done.budget.tokens_spent == 100
    assert done.budget.cost_microusd_spent == 100
    assert done.valid_completed_reports == ()


def test_duplicate_delivery_is_idempotent_not_a_pass_or_failure_occurrence() -> None:
    pending = begin(session(), 1)
    output, proof = report(pending), receipt(pending)
    once = finish(pending, output, proof=proof)
    again = complete_invocation(once, invocation_id="call-1", receipt=proof,
                                output=output, now=at(5))
    assert again == once
    assert len(again.valid_completed_reports) == 1
    with pytest.raises(ValueError):
        begin(again, 1, seconds=5)
    with pytest.raises(ValueError):
        complete_invocation(again, invocation_id="call-1", receipt=proof,
                            output="different delivery", now=at(5))


@pytest.mark.parametrize("cached,reused_execution", [(True, False), (False, True)])
def test_cached_or_reused_execution_receipt_cannot_increment_passes(
    cached: bool, reused_execution: bool,
) -> None:
    once = clean(session(), 1)
    pending = begin(once, 2)
    proof = receipt(pending, cached=cached,
                    execution_id="exec-call-1" if reused_execution else "exec-call-2")
    stopped = finish(pending, report(pending), proof=proof)
    assert len(stopped.valid_completed_reports) == 1
    assert stopped.budget.invocations == 2
    assert not evaluate(stopped, current_artifact=stopped.artifact, now=at(8)).eligible


def test_replayed_report_with_a_fresh_receipt_is_not_a_new_inspection() -> None:
    first = begin(session(), 1)
    old = report(first)
    once = finish(first, old)
    pending = begin(once, 2)
    stopped = finish(pending, old)
    assert len(stopped.valid_completed_reports) == 1
    assert stopped.budget.invocations == 2


@pytest.mark.parametrize("tokens,cost", [(101, 10), (10, 101), (1001, 1001)])
def test_actual_usage_over_reservation_is_charged_and_blocks_even_a_clean_report(
    tokens: int, cost: int,
) -> None:
    pending = begin(clean(session(), 1), 2)
    stopped = finish(pending, report(pending), proof=receipt(pending, tokens=tokens, cost=cost))
    assert stopped.budget.tokens_spent >= tokens
    assert stopped.budget.cost_microusd_spent >= cost
    assert "BUDGET_OVERRUN" in stopped.terminal_reasons
    assert not evaluate(stopped, current_artifact=stopped.artifact, now=at(8)).eligible


@pytest.mark.parametrize("reservation", [
    Reservation(tokens=1001, cost_microusd=1, seconds=10),
    Reservation(tokens=1, cost_microusd=1001, seconds=10),
    Reservation(tokens=1, cost_microusd=1, seconds=121),
])
def test_reservations_cannot_exceed_any_remaining_cap(reservation: Reservation) -> None:
    with pytest.raises(ValueError):
        begin(session(), 1, reservation=reservation)


@pytest.mark.parametrize("seconds", [2, 13, 120, 121])
def test_completion_cannot_be_accepted_before_start_or_at_or_after_deadlines(seconds: int) -> None:
    pending = begin(session(), 1)
    stopped = finish(pending, report(pending), seconds=seconds)
    assert stopped.budget.invocations == 1
    assert stopped.valid_completed_reports == ()
    assert "TIME_WINDOW" in stopped.terminal_reasons


@pytest.mark.parametrize("seconds", [-1, 6, 120, 121])
def test_evaluation_revalidates_clock_window_even_after_two_successes(seconds: int) -> None:
    done = clean(clean(session(), 1), 2)
    decision = evaluate(done, current_artifact=done.artifact, now=at(seconds))
    assert not decision.eligible
    assert "TIME_WINDOW" in decision.reasons


def test_pending_reservation_disallows_concurrent_local_invocation_and_eligibility() -> None:
    pending = begin(session(), 1)
    with pytest.raises(ValueError):
        begin(pending, 2)
    assert not evaluate(pending, current_artifact=pending.artifact, now=at(4)).eligible


def test_naive_clock_cannot_be_assumed_to_be_utc() -> None:
    s = session()
    with pytest.raises(ValueError):
        evaluate(s, current_artifact=s.artifact, now=datetime(2026, 9, 13))


@pytest.mark.parametrize("field,value", [
    ("tenant_id", "foreign"), ("work_item_id", "foreign"), ("session_id", "foreign"),
])
def test_report_and_receipt_cannot_cross_tenant_work_item_or_session(
    field: str, value: str,
) -> None:
    pending = begin(session(), 1)
    payload = report(pending).model_dump(mode="json")
    payload.pop("content_digest")
    payload["binding"].pop("content_digest")
    payload["binding"]["scope"].pop("content_digest")
    payload["binding"]["scope"][field] = value
    done = finish(pending, json.dumps(payload))
    assert not done.valid_completed_reports
    proof_values = receipt(pending).model_dump(exclude={"content_digest"})
    proof_values["binding"] = payload["binding"]
    foreign_proof = InvocationReceipt.model_validate(proof_values)
    done = finish(pending, report(pending), proof=foreign_proof)
    assert not done.valid_completed_reports
    assert done.budget.invocations == 1


def test_stale_report_hash_cannot_be_laundered_through_a_model_copy() -> None:
    pending = begin(session(), 1)
    corrupt = report(pending).model_copy(update={"content_digest": digest("forged")})
    done = finish(pending, corrupt)
    assert not done.valid_completed_reports
    assert done.budget.invocations == 1