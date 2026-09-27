"""Review 1 regressions: evidence subtraction is not novelty after independent closure."""

import pytest
from test_review_fixtures import (
    at,
    begin,
    closure,
    digest,
    evidence,
    finding,
    finish,
    policy,
    repair_report,
    report,
    session,
)

from nokinc_factory.domain.review_base import renewed
from nokinc_factory.domain.review_session import ReviewSession
from nokinc_factory.policy.review import evaluate


def _closed_rebuttal(*, risk: str = "T1", max_reviews: int = 3,
                     min_reviews: int = 3) -> ReviewSession:
    first = begin(session(risk=risk, rules=policy(min_review_passes=min_reviews,
                                                max_review_passes=max_reviews)), 1)
    item = renewed(finding(first), evidence=(evidence(first.artifact, "A", "observation A"),
                                            evidence(first.artifact, "B", "observation B")))
    reviewed = finish(first, report(first, findings=(item,), fail=True,
                                    verdict="CHANGES_REQUIRED"))
    pending = begin(reviewed, 2, repair=True)
    rebutted = finish(pending, repair_report(
        pending, artifact=pending.artifact, disposition="REJECTED_WITH_EVIDENCE"))
    assert rebutted.artifact == first.artifact
    assert rebutted.open_findings  # A producer rebuttal is not its own closure.
    second = begin(rebutted, 3)
    closed = finish(second, report(second, rechecks=closure(second)))
    assert closed.artifact == first.artifact
    assert not closed.open_findings
    assert len(closed.valid_completed_reports) == 2
    assert not evaluate(closed, current_artifact=closed.artifact, now=at(11)).eligible
    return ReviewSession.model_validate_json(closed.model_dump_json())


@pytest.mark.parametrize("risk", ["T1", "T2"])
@pytest.mark.parametrize("indices", [(0,), (1,), (1, 0), (0, 0), (1, 0, 1), (0, 1)],
                         ids=["subset-A", "subset-B", "reorder", "duplicate-subset",
                              "duplicates", "same-set"])
def test_closed_rejected_finding_without_new_evidence_stays_closed_at_third_review(
    risk: str, indices: tuple[int, ...],
) -> None:
    closed = _closed_rebuttal(risk=risk)
    previous = closed.state.findings[0]
    third = begin(closed, 4)
    # Different evidence IDs/prose or repeated digests are not new observations.
    repeated = renewed(previous.finding, location="reworded location", evidence=tuple(
        renewed(previous.finding.evidence[index], evidence_id=f"repeat-{position}")
        for position, index in enumerate(indices)
    ))
    done = finish(third, report(third, findings=(repeated,)))

    assert done.open_findings == ()
    assert done.state.findings == closed.state.findings
    assert done.state.findings[0].closed_by == previous.closed_by
    assert done.state.findings[0].report_digest == previous.report_digest
    assert len(done.valid_completed_reports) == 3
    assert done.budget.invocations == 4
    assert done.budget.repairs == 1
    assert done.artifact == closed.artifact
    assert done.terminal_reasons == ()
    assert done.state.failures == ()
    decision = evaluate(done, current_artifact=done.artifact, now=at(14))
    assert decision.eligible and decision.completed_reviews == 3
    assert decision.reasons == ()
    with pytest.raises(ValueError, match="REVIEW_LIMIT"):
        begin(done, 5, repair=True)


@pytest.mark.parametrize("max_reviews", [3, 4])
def test_genuinely_new_evidence_still_reopens_once_and_obeys_review_capacity(
    max_reviews: int,
) -> None:
    closed = _closed_rebuttal(max_reviews=max_reviews)
    previous = closed.state.findings[0]
    third = begin(closed, 4)
    changed = renewed(previous.finding, evidence=(previous.finding.evidence[0],
                      evidence(third.artifact, "C", "new observation C")))
    output = report(third, findings=(changed,))
    reopened = finish(third, output)
    assert len(reopened.open_findings) == 1
    current = reopened.open_findings[0]
    assert current.finding == changed and current.blocking
    assert current.report_digest == output.content_digest
    assert current.closed_by is None and current.occurrences == 1
    assert "REPEATED_FAILURE" not in reopened.terminal_reasons
    assert len(reopened.valid_completed_reports) == 3
    assert reopened.budget.repairs == 1
    decision = evaluate(reopened, current_artifact=reopened.artifact, now=at(14))
    assert not decision.eligible and "OPEN_BLOCKERS" in decision.reasons
    if max_reviews == 3:
        assert decision.status == "BLOCKED" and "REVIEW_LIMIT" in decision.reasons
        with pytest.raises(ValueError, match="REVIEW_LIMIT"):
            begin(reopened, 5, repair=True)
    else:
        assert decision.status == "NEEDS_REPAIR"
        assert reopened.terminal_reasons == ()


@pytest.mark.parametrize("risk", ["T1", "T2"])
def test_subset_of_an_unresolved_mandatory_low_still_repeats_and_stops(risk: str) -> None:
    first = begin(session(risk=risk), 1)
    item = finding(first)
    item = renewed(item, evidence=(*item.evidence, evidence(first.artifact, "B", "observation B")))
    reviewed = finish(first, report(first, findings=(item,)))
    second = begin(reviewed, 2)
    repeated = renewed(item, evidence=item.evidence[:1])
    stopped = finish(second, report(second, findings=(repeated,)))
    assert stopped.open_findings[0].blocking
    assert stopped.open_findings[0].finding.severity == "LOW"
    assert stopped.open_findings[0].occurrences == 2
    assert "REPEATED_FAILURE" in stopped.terminal_reasons
    assert not evaluate(stopped, current_artifact=stopped.artifact, now=at(8)).eligible


def test_ignored_resolved_finding_does_not_waive_a_failed_mandatory_disposition() -> None:
    closed = _closed_rebuttal(risk="T2")
    third = begin(closed, 4)
    item = closed.state.findings[0].finding
    repeated = renewed(item, evidence=item.evidence[:1])
    done = finish(third, report(third, findings=(repeated,), fail=True))
    assert done.open_findings == ()
    assert done.state.findings == closed.state.findings
    assert len(done.valid_completed_reports) == 3
    decision = evaluate(done, current_artifact=done.artifact, now=at(14))
    assert not decision.eligible
    assert "MANDATORY_CRITERIA" in decision.reasons
    assert "REVIEW_LIMIT" in decision.reasons


def test_changed_artifact_can_reopen_a_closed_finding_with_previously_seen_evidence() -> None:
    first = begin(session(rules=policy(min_review_passes=3, max_review_passes=4)), 1)
    original = finding(first)
    reviewed = finish(first, report(first, findings=(original,)))
    repairing = begin(reviewed, 2, repair=True)
    rebutted = finish(repairing, repair_report(
        repairing, artifact=repairing.artifact, disposition="REJECTED_WITH_EVIDENCE"))
    second = begin(rebutted, 3)
    closed = finish(second, report(second, rechecks=closure(second),
                                  findings=(finding(second, cause="another-defect"),)))
    repairing = begin(closed, 4, repair=True)
    repaired = finish(repairing, repair_report(repairing))
    third = begin(repaired, 5)
    rebound = renewed(original, artifact_digest=third.artifact.content_digest, evidence=tuple(
        renewed(item, artifact_digest=third.artifact.content_digest) for item in original.evidence
    ))
    done = finish(third, report(third, findings=(rebound,), rechecks=closure(third)))
    assert done.artifact != first.artifact
    assert len(done.open_findings) == 1
    assert done.open_findings[0].finding == rebound
    assert done.open_findings[0].occurrences == 1
    assert done.terminal_reasons == ()
    assert evaluate(done, current_artifact=done.artifact, now=at(17)).status == "NEEDS_REPAIR"


def test_accumulated_evidence_survives_multiple_reopen_close_cycles_and_serialization() -> None:
    closed = _closed_rebuttal(min_reviews=5, max_reviews=5)
    original = closed.state.findings[0].finding
    third = begin(closed, 4)
    changed = renewed(original, evidence=(evidence(third.artifact, "C", "new observation C"),))
    reopened = finish(third, report(third, findings=(changed,)))
    assert reopened.open_findings[0].occurrences == 1
    repairing = begin(reopened, 5, repair=True)
    output = repair_report(repairing, artifact=repairing.artifact,
                           disposition="REJECTED_WITH_EVIDENCE")
    resolution = renewed(output.resolutions[0], evidence=(evidence(
        repairing.artifact, "second-rebuttal", "new rebuttal of observation C"),))
    rebutted = finish(repairing, renewed(output, resolutions=(resolution,)))
    fourth = begin(rebutted, 6)
    # Only the resolution of this exact occurrence may authorize its recheck.
    recheck = renewed(closure(fourth)[-1], evidence=(evidence(
        fourth.artifact, "second-recheck", "independent recheck of observation C"),))
    closed_again = finish(fourth, report(fourth, rechecks=(recheck,)))
    assert not closed_again.open_findings
    assert closed_again.state.findings[0].finding == changed
    restored = ReviewSession.model_validate_json(closed_again.model_dump_json())
    fifth = begin(restored, 7)
    # A and B vanished from the latest finding, not from the adjudicated history.
    done = finish(fifth, report(fifth, findings=(original,)))
    assert done.state.findings == restored.state.findings
    assert done.open_findings == ()
    assert done.terminal_reasons == ()
    assert done.state.failures == ()
    assert len(done.valid_completed_reports) == 5
    assert done.budget.repairs == 2
    assert evaluate(done, current_artifact=done.artifact, now=at(23)).eligible


@pytest.mark.parametrize("source", ["resolution", "recheck"])
def test_adjudicated_resolution_and_recheck_observations_are_already_known(source: str) -> None:
    closed = _closed_rebuttal()
    known = (closed.resolutions[0].resolution.evidence if source == "resolution"
             else closed.valid_completed_reports[-1].rechecks[0].evidence)
    third = begin(closed, 4)
    repeated = renewed(closed.state.findings[0].finding, evidence=known)
    done = finish(third, report(third, findings=(repeated,)))
    assert done.state.findings == closed.state.findings
    assert not done.open_findings
    assert evaluate(done, current_artifact=done.artifact, now=at(14)).eligible


def test_rebinding_old_evidence_to_the_already_closed_artifact_is_not_novel() -> None:
    first = begin(session(rules=policy(min_review_passes=3)), 1)
    original = finding(first)
    reviewed = finish(first, report(first, findings=(original,)))
    repairing = begin(reviewed, 2, repair=True)
    repaired = finish(repairing, repair_report(repairing))
    second = begin(repaired, 3)
    closed = finish(second, report(second, rechecks=closure(second)))
    assert closed.artifact != first.artifact
    assert not closed.open_findings
    third = begin(ReviewSession.model_validate_json(closed.model_dump_json()), 4)
    repeated = renewed(original, artifact_digest=third.artifact.content_digest, evidence=tuple(
        renewed(item, artifact_digest=third.artifact.content_digest) for item in original.evidence
    ))
    done = finish(third, report(third, findings=(repeated,)))
    assert done.artifact == closed.artifact  # No artifact change since independent closure.
    assert done.state.findings == closed.state.findings
    assert not done.open_findings
    assert done.terminal_reasons == ()
    assert evaluate(done, current_artifact=done.artifact, now=at(14)).eligible


@pytest.mark.parametrize("invalid", ["incomplete", "foreign"])
def test_uncredited_reports_cannot_poison_the_history_to_suppress_new_evidence(
    invalid: str,
) -> None:
    closed = _closed_rebuttal(max_reviews=4)
    third = begin(closed, 4)
    changed = renewed(closed.state.findings[0].finding,
                      evidence=(evidence(third.artifact, "C", "new observation C"),))
    output = report(third, findings=(changed,))
    if invalid == "incomplete":
        output = renewed(output, complete=False)
    else:
        output = renewed(output, binding=renewed(output.binding, artifact_digest=digest("foreign")))
    rejected = finish(third, output)
    assert rejected.state.findings == closed.state.findings
    assert rejected.valid_completed_reports == closed.valid_completed_reports
    retry = begin(ReviewSession.model_validate_json(rejected.model_dump_json()), 5)
    reopened = finish(retry, report(retry, findings=(changed,)))
    assert len(reopened.open_findings) == 1
    assert reopened.open_findings[0].finding == changed
    assert reopened.open_findings[0].occurrences == 1
    assert reopened.terminal_reasons == ()
    assert reopened.state.failures == ()
    decision = evaluate(reopened, current_artifact=reopened.artifact, now=at(17))
    assert decision.status == "NEEDS_REPAIR"