"""Review 1 regressions: trusted stops survive incomplete coverage, not bad bindings."""

import pytest
from test_review_fixtures import (
    at,
    begin,
    clean,
    closure,
    digest,
    finding,
    finish,
    model,
    receipt,
    repair_report,
    report,
    session,
)

from nokinc_factory.domain.review_base import renewed
from nokinc_factory.domain.review_report import ReviewReport
from nokinc_factory.domain.review_session import ReviewSession
from nokinc_factory.policy.review import complete_invocation, evaluate


def _stop_report(pending: ReviewSession, signal: str) -> ReviewReport:
    if signal == "CRITICAL":
        return report(pending, findings=(finding(pending, severity="CRITICAL"),))
    if signal == "CONTRACT_FINDING":
        gap = renewed(finding(pending), criterion_id="uncontracted-risk", fingerprint="",
                      kind="CONTRACT_GAP")
        return report(pending, findings=(gap,))
    return report(pending, verdict=signal)


def _incomplete(output: ReviewReport, coverage: str) -> ReviewReport:
    if coverage == "flag":
        return renewed(output, complete=False)
    if coverage == "missing-criterion":
        return renewed(output, criteria=output.criteria[1:])
    if coverage == "empty":
        return renewed(output, complete=False, criteria=())
    if coverage == "duplicate":
        return renewed(output, criteria=(*output.criteria, output.criteria[0]))
    if coverage == "mandatory-na":
        item = renewed(output.criteria[0], status="NOT_APPLICABLE")
    else:
        assert coverage == "missing-evidence"
        item = renewed(output.criteria[0], evidence=())
    return renewed(output, criteria=(item, output.criteria[1]))


@pytest.mark.parametrize("signal,reason,status", [
    ("CONTRACT_GAP", "CONTRACT_GAP", "ESCALATE"),
    ("CRITICAL", "CRITICAL", "ESCALATE"),
    ("CONTRACT_FINDING", "CONTRACT_GAP", "ESCALATE"),
    ("CONTEXT_GAP", "CONTEXT_GAP", "BLOCKED"),
    ("ESCALATE", "REVIEW_ESCALATED", "ESCALATE"),
])
@pytest.mark.parametrize("coverage", [
    "flag", "missing-criterion", "empty", "duplicate", "mandatory-na", "missing-evidence",
])
def test_bound_incomplete_stop_survives_without_credit_or_a_third_clean_review(
    signal: str, reason: str, status: str, coverage: str,
) -> None:
    once = clean(session(risk="T2"), 1)
    pending = begin(once, 2)
    output = _incomplete(_stop_report(pending, signal), coverage)
    stopped = finish(pending, output)

    assert reason in stopped.terminal_reasons
    assert stopped.artifact == once.artifact
    assert stopped.valid_completed_reports == once.valid_completed_reports
    failure = "MISSING_EVIDENCE" if coverage == "missing-evidence" else "INCOMPLETE_CRITERIA"
    assert {item.reason: item.occurrences for item in stopped.state.failures} == {failure: 1}
    assert stopped.budget.invocations == 2
    assert stopped.budget.repairs == 0
    assert stopped.budget.tokens_spent == stopped.budget.cost_microusd_spent == 20
    assert stopped.budget.tokens_reserved == stopped.budget.cost_microusd_reserved == 0
    assert tuple(item.finding for item in stopped.open_findings) == output.findings
    assert all(item.report_digest == output.content_digest for item in stopped.open_findings)

    restored = ReviewSession.model_validate_json(stopped.model_dump_json())
    decision = evaluate(restored, current_artifact=restored.artifact, now=at(8))
    assert not decision.eligible
    assert decision.status == status
    assert decision.completed_reviews == 1
    assert reason in decision.reasons
    for repair in (False, True):
        with pytest.raises(ValueError, match="REVIEW_LIMIT"):
            begin(restored, 3, repair=repair)


@pytest.mark.parametrize("part", [
    "tenant_id", "work_item_id", "session_id", "artifact_digest", "contract_digest",
    "context_digest", "invocation", "reviewer", "finding-artifact", "finding-evidence",
    "criterion-evidence", "unknown-stale-finding", "hash",
])
def test_untrusted_incomplete_stop_never_acquires_current_artifact_authority(part: str) -> None:
    once = clean(session(risk="T2"), 1)
    pending = begin(once, 2)
    output = renewed(report(pending, findings=(finding(pending, severity="CRITICAL"),),
                            verdict="CONTRACT_GAP"), complete=False)
    expected = "BINDING_MISMATCH"
    if part in {"tenant_id", "work_item_id", "session_id"}:
        scope = renewed(output.binding.scope, **{part: "foreign"})
        output = renewed(output, binding=renewed(output.binding, scope=scope))
    elif part in {"artifact_digest", "contract_digest", "context_digest"}:
        output = renewed(output, binding=renewed(output.binding, **{part: digest("foreign")}))
    elif part == "invocation":
        output = renewed(output, invocation_id="unreserved-call")
    elif part == "reviewer":
        output = renewed(output, reviewer=model("unplanned", reviewer=True, family="c"))
    elif part == "finding-artifact":
        output = renewed(output, findings=(renewed(output.findings[0],
                                                  artifact_digest=digest("foreign")),))
    elif part in {"finding-evidence", "unknown-stale-finding"}:
        item = output.findings[0]
        stale = renewed(item.evidence[0], artifact_digest=digest("foreign"))
        item = renewed(item, evidence=(stale,))
        if part == "unknown-stale-finding":
            item = renewed(item, criterion_id="uncontracted-risk", fingerprint="")
        output = renewed(output, findings=(item,))
        expected = "MISSING_EVIDENCE"
    elif part == "criterion-evidence":
        criterion = output.criteria[0]
        stale = renewed(criterion.evidence[0], artifact_digest=digest("foreign"))
        output = renewed(output, criteria=(renewed(criterion, evidence=(stale,)),
                                           output.criteria[1]))
        expected = "MISSING_EVIDENCE"
    else:
        assert part == "hash"
        output = output.model_copy(update={"content_digest": digest("forged")})
        expected = "MALFORMED_REPORT"

    rejected = finish(pending, output)
    assert rejected.terminal_reasons == ()
    assert rejected.open_findings == ()
    assert rejected.valid_completed_reports == once.valid_completed_reports
    assert {item.reason: item.occurrences for item in rejected.state.failures} == {expected: 1}
    assert not evaluate(rejected, current_artifact=rejected.artifact, now=at(8)).eligible
    # A foreign/untrusted stop is not a finding against this artifact. A real retry may pass.
    done = clean(rejected, 3)
    assert done.artifact == once.artifact
    assert evaluate(done, current_artifact=done.artifact, now=at(11)).eligible


@pytest.mark.parametrize("part,reason", [
    ("missing", "RECEIPT_UNAVAILABLE"), ("binding", "RECEIPT_MISMATCH"),
    ("model", "RECEIPT_MISMATCH"), ("cached", "REPLAY"),
])
def test_untrusted_receipt_does_not_promote_incomplete_stop_findings(
    part: str, reason: str,
) -> None:
    pending = begin(clean(session(), 1), 2)
    output = renewed(_stop_report(pending, "CRITICAL"), complete=False, verdict="CONTRACT_GAP")
    proof = receipt(pending)
    if part == "binding":
        proof = renewed(proof, binding=renewed(proof.binding, artifact_digest=digest("foreign")))
    elif part == "model":
        proof = renewed(proof, actual_model=model("unplanned", reviewer=True, family="c"))
    elif part == "cached":
        proof = renewed(proof, cached=True)
    done = complete_invocation(pending, invocation_id=output.invocation_id,
                               receipt=None if part == "missing" else proof,
                               output=output, now=at(7))
    assert not {"CRITICAL", "CONTRACT_GAP"}.intersection(done.terminal_reasons)
    assert not done.open_findings
    assert len(done.valid_completed_reports) == 1
    assert {item.reason: item.occurrences for item in done.state.failures} == {reason: 1}
    assert done.budget.tokens_spent == done.budget.cost_microusd_spent == 110


@pytest.mark.parametrize("incomplete", [True, False])
def test_stop_survives_uncredited_closure_without_closing_an_existing_mandatory_low(
    incomplete: bool,
) -> None:
    first = begin(session(), 1)
    reviewed = finish(first, report(first, findings=(finding(first),)))
    repairing = begin(reviewed, 2, repair=True)
    rebutted = finish(repairing, repair_report(
        repairing, artifact=repairing.artifact, disposition="REJECTED_WITH_EVIDENCE"))
    pending = begin(rebutted, 3)
    rechecks = closure(pending)
    if not incomplete:
        rechecks = (renewed(rechecks[0], resolution_digest=digest("unrecorded")),)
    critical = finding(pending, severity="CRITICAL", cause="independent-risk")
    output = renewed(report(pending, findings=(critical,), rechecks=rechecks),
                     complete=not incomplete)
    stopped = finish(pending, output)
    assert "CRITICAL" in stopped.terminal_reasons
    assert stopped.valid_completed_reports == reviewed.valid_completed_reports
    original = next(item for item in stopped.open_findings
                    if item.finding.fingerprint == reviewed.open_findings[0].finding.fingerprint)
    assert original == reviewed.open_findings[0]
    assert original.finding.severity == "LOW" and original.blocking
    assert any(item.finding == critical for item in stopped.open_findings)
    reason = "INCOMPLETE_CRITERIA" if incomplete else "INVALID_CLOSURE"
    assert {item.reason for item in stopped.state.failures} == {reason}
    assert not evaluate(stopped, current_artifact=stopped.artifact, now=at(11)).eligible


def test_retained_stop_cannot_be_erased_by_redelivery_replacement_or_rehashed_state() -> None:
    pending = begin(clean(session(), 1), 2)
    output = renewed(_stop_report(pending, "CRITICAL"), complete=False)
    proof = receipt(pending)
    stopped = finish(pending, output, proof=proof)
    assert "CRITICAL" in stopped.terminal_reasons
    assert complete_invocation(stopped, invocation_id=output.invocation_id, receipt=proof,
                               output=output, now=at(8)) == stopped
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        complete_invocation(stopped, invocation_id=output.invocation_id, receipt=proof,
                            output=report(pending), now=at(8))
    forged = renewed(stopped, state=renewed(stopped.state, terminal_reasons=(), findings=()))
    with pytest.raises(ValueError, match="actual attempt ledger"):
        evaluate(forged, current_artifact=forged.artifact, now=at(8))


def test_bound_incomplete_clean_report_is_retryable_but_never_a_completed_review() -> None:
    once = clean(session(), 1)
    pending = begin(once, 2)
    incomplete = finish(pending, renewed(report(pending), complete=False))
    assert incomplete.terminal_reasons == ()
    assert incomplete.valid_completed_reports == once.valid_completed_reports
    assert not evaluate(incomplete, current_artifact=incomplete.artifact, now=at(8)).eligible
    done = clean(incomplete, 3)
    assert evaluate(done, current_artifact=done.artifact, now=at(11)).eligible
    assert len(done.valid_completed_reports) == 2


def test_foreign_recheck_evidence_rejects_the_stop_before_attempting_any_closure() -> None:
    first = begin(session(), 1)
    reviewed = finish(first, report(first, findings=(finding(first),)))
    repairing = begin(reviewed, 2, repair=True)
    rebutted = finish(repairing, repair_report(
        repairing, artifact=repairing.artifact, disposition="REJECTED_WITH_EVIDENCE"))
    pending = begin(rebutted, 3)
    recheck = closure(pending)[0]
    recheck = renewed(recheck, evidence=(renewed(recheck.evidence[0],
                                               artifact_digest=digest("foreign")),))
    output = renewed(report(pending, rechecks=(recheck,), verdict="CONTRACT_GAP",
                            findings=(finding(pending, severity="CRITICAL", cause="new-risk"),)),
                     complete=False)
    rejected = finish(pending, output)
    assert rejected.state.findings == reviewed.state.findings
    assert rejected.valid_completed_reports == reviewed.valid_completed_reports
    assert rejected.terminal_reasons == ()
    assert {item.reason: item.occurrences for item in rejected.state.failures} == {
        "MISSING_EVIDENCE": 1,
    }
    assert not evaluate(rejected, current_artifact=rejected.artifact, now=at(11)).eligible