"""Second TDD slice: original-doer ordering, lineage, repair caps and closure recurrence."""

import pytest
from test_review_fixtures import (
    at,
    begin,
    changed_artifact,
    closure,
    digest,
    finding,
    finish,
    model,
    policy,
    receipt,
    repair_report,
    report,
    session,
)

from nokinc_factory.domain.review import ArtifactRef, ModelIdentity
from nokinc_factory.domain.review_base import renewed
from nokinc_factory.domain.review_session import ReviewSession
from nokinc_factory.policy.review import evaluate, start_session
from nokinc_factory.ports.review import ReviewSessionStore


def test_reconciled_report_requires_closure_review_before_another_doer_repair() -> None:
    first = begin(session(), 1)
    reviewed = finish(first, report(first, findings=(finding(first),)))
    pending = begin(reviewed, 2, repair=True)
    repaired = finish(pending, repair_report(pending))
    with pytest.raises(ValueError):
        begin(repaired, 3, repair=True)
    decision = evaluate(repaired, current_artifact=repaired.artifact, now=at(8))
    assert decision.status == "NEEDS_REVIEW"


def test_predeclared_reviewer_change_preserves_actual_open_failure_count() -> None:
    s = session()
    alternate = model("other-reviewer", reviewer=True, family="c")
    s = start_session(scope=s.seed.scope, contract=s.seed.contract, policy=s.seed.policy,
                      original_doer=s.seed.original_doer,
                      reviewers=(*s.seed.reviewers, alternate), artifact=s.artifact, now=at(0))
    first = begin(s, 1)
    reviewed = finish(first, report(first, findings=(finding(first),)))
    restored = ReviewSession.model_validate_json(reviewed.model_dump_json())
    second = begin(restored, 2, actor=alternate)
    # Even changing the model AND cause key cannot silently erase the real old blocker.
    stopped = finish(second, report(second, findings=(finding(second, cause="wording-churn"),)))
    assert "REPEATED_FAILURE" in stopped.terminal_reasons
    assert stopped.budget.invocations == 2
    assert len(stopped.open_findings) == 2


def test_independence_covers_every_contributing_producer_not_just_original_doer() -> None:
    s = session(risk="T2")
    artifact = renewed(s.artifact, producers=(*s.artifact.producers, model("upstream", family="b")))
    with pytest.raises(ValueError):
        start_session(scope=s.seed.scope, contract=s.seed.contract, policy=s.seed.policy,
                      original_doer=s.seed.original_doer, reviewers=s.seed.reviewers,
                      artifact=artifact, now=at(0))


def test_tenant_can_require_family_independence_below_t2_without_model_guessing() -> None:
    s = session(rules=policy(require_different_family=True))
    with pytest.raises(ValueError):
        start_session(scope=s.seed.scope, contract=s.seed.contract, policy=s.seed.policy,
                      original_doer=s.seed.original_doer,
                      reviewers=(model("separate-same-family", reviewer=True),),
                      artifact=s.artifact, now=at(0))


def test_third_implementation_repair_does_not_grant_a_fourth_or_reset_on_new_findings() -> None:
    s = session(rules=policy(min_review_passes=5, max_review_passes=8, max_invocations=20))
    first = begin(s, 1)
    s = finish(first, report(first, findings=(finding(first, cause="defect-0"),)))
    index = 2
    for number in range(1, 4):
        pending = begin(s, index, repair=True)
        candidate = changed_artifact(pending, f"v{number+1}")
        s = finish(pending, repair_report(pending, artifact=candidate))
        index += 1
        pending = begin(s, index)
        s = finish(pending, report(pending, rechecks=closure(pending),
                                    findings=(finding(pending, cause=f"defect-{number}"),)))
        index += 1
    assert len(s.valid_completed_reports) == 4
    assert s.budget.repairs == 3
    assert s.budget.invocations == 7
    assert "REPAIR_LIMIT" in s.terminal_reasons
    with pytest.raises(ValueError):
        begin(s, index, repair=True)


@pytest.mark.parametrize("case", ["scope", "contract", "context", "dropped-producer"])
def test_repair_cannot_change_pinned_scope_contract_context_or_drop_provenance(case: str) -> None:
    first = begin(session(), 1)
    reviewed = finish(first, report(first, findings=(finding(first),)))
    pending = begin(reviewed, 2, repair=True)
    artifact = changed_artifact(pending)
    if case == "scope":
        artifact = renewed(artifact, scope=renewed(artifact.scope, session_id="new-unit"))
    elif case == "contract":
        artifact = renewed(artifact, contract_digest=digest("new-contract"))
    elif case == "context":
        artifact = renewed(artifact, context_digest=digest("new-context"))
    else:
        artifact = renewed(artifact, producers=(model("replacement"),))
    done = finish(pending, repair_report(pending, artifact=artifact))
    assert done.artifact == reviewed.artifact
    assert not done.resolutions


def test_actual_receipt_model_must_match_the_pinned_report_reviewer() -> None:
    pending = begin(session(), 1)
    proof = renewed(receipt(pending), actual_model=model("imposter", reviewer=True, family="c"))
    done = finish(pending, report(pending), proof=proof)
    assert "RECEIPT_MISMATCH" in done.terminal_reasons
    assert not done.valid_completed_reports
    assert done.budget.tokens_spent == 100


@pytest.mark.parametrize("verdict,reason", [("ESCALATE", "REVIEW_ESCALATED"),
                                         ("CONTEXT_GAP", "CONTEXT_GAP")])
def test_structured_stop_verdicts_take_precedence_over_clean_evidence(
    verdict: str, reason: str,
) -> None:
    pending = begin(session(), 1)
    done = finish(pending, report(pending, verdict=verdict))
    assert reason in done.terminal_reasons
    assert not evaluate(done, current_artifact=done.artifact, now=at(5)).eligible


def test_mandatory_unknown_evidence_gap_stops_rather_than_inventing_a_defect() -> None:
    pending = begin(session(), 1)
    output = report(pending)
    unknown = renewed(output.criteria[0], status="UNKNOWN", evidence=())
    done = finish(pending, renewed(output, criteria=(unknown, output.criteria[1])))
    assert "CONTEXT_GAP" in done.terminal_reasons
    assert not done.open_findings


def test_advisory_failed_criterion_is_complete_but_not_a_mandatory_exit() -> None:
    s = session()
    for index in (1, 2):
        pending = begin(s, index)
        output = report(pending)
        advisory = renewed(output.criteria[1], status="FAIL", evidence=())
        s = finish(pending, renewed(output, criteria=(output.criteria[0], advisory)))
    assert evaluate(s, current_artifact=s.artifact, now=at(8)).eligible


def test_mandatory_low_finding_cannot_be_laundered_as_advisory() -> None:
    pending = begin(session(), 1)
    disguised = renewed(finding(pending), kind="ADVISORY")
    done = finish(pending, report(pending, findings=(disguised,)))
    assert done.open_findings[0].blocking


def test_store_extension_is_a_protocol_not_a_fake_atomic_memory_store() -> None:
    assert callable(ReviewSessionStore.load_current)
    assert callable(ReviewSessionStore.compare_and_reserve)
    with pytest.raises(TypeError):
        ReviewSessionStore()  # type: ignore[misc]


def test_new_evidence_can_reopen_resolved_finding_without_counting_a_resolved_occurrence() -> None:
    first = begin(session(rules=policy(min_review_passes=3, max_review_passes=4)), 1)
    reviewed = finish(first, report(first, findings=(finding(first),)))
    repairing = begin(reviewed, 2, repair=True)
    repaired = finish(repairing, repair_report(repairing))
    second = begin(repaired, 3)
    closed = finish(second, report(second, rechecks=closure(second)))
    assert not closed.open_findings
    third = begin(closed, 4)
    item = finding(third)
    item = renewed(item, evidence=(renewed(item.evidence[0], digest=digest("new-failure")),))
    reopened = finish(third, report(third, findings=(item,)))
    assert reopened.open_findings[0].occurrences == 1
    assert "REPEATED_FAILURE" not in reopened.terminal_reasons
    decision = evaluate(reopened, current_artifact=reopened.artifact, now=at(14))
    assert decision.status == "NEEDS_REPAIR"


def test_empty_identity_claims_do_not_become_qualified_defaults() -> None:
    with pytest.raises(ValueError):
        ModelIdentity.model_validate({"provider": "synthetic"})
    with pytest.raises(ValueError):
        ArtifactRef.model_validate({"payload_digest": digest("bytes")})


def test_old_resolution_cannot_close_a_reopened_finding_with_new_evidence() -> None:
    first = begin(session(rules=policy(min_review_passes=3, max_review_passes=4)), 1)
    reviewed = finish(first, report(first, findings=(finding(first),)))
    repairing = begin(reviewed, 2, repair=True)
    repaired = finish(repairing, repair_report(repairing))
    second = begin(repaired, 3)
    closed = finish(second, report(second, rechecks=closure(second)))
    third = begin(closed, 4)
    item = finding(third)
    item = renewed(item, evidence=(renewed(item.evidence[0], digest=digest("new-failure")),))
    reopened = finish(third, report(third, findings=(item,)))
    fourth = begin(reopened, 5)
    stale_closure = finish(fourth, report(fourth, rechecks=closure(fourth)))
    assert stale_closure.open_findings
    assert not evaluate(stale_closure, current_artifact=stale_closure.artifact, now=at(17)).eligible