"""ARP-1 completed inspections, factual reconciliation and anti-nitpick contracts."""

import pytest
from test_review_fixtures import (
    at,
    begin,
    changed_artifact,
    clean,
    closure,
    finding,
    finish,
    policy,
    repair_report,
    report,
    session,
)

from nokinc_factory.policy.review import evaluate


def test_two_clean_independent_invocations_need_no_gratuitous_edit() -> None:
    original = session()
    once = clean(original, 1)
    assert not evaluate(once, current_artifact=once.artifact, now=at(5)).eligible
    twice = clean(once, 2)
    decision = evaluate(twice, current_artifact=twice.artifact, now=at(8))
    assert decision.eligible
    assert decision.status == "ELIGIBLE"
    assert decision.session_digest == twice.content_digest
    assert decision.artifact_digest == original.artifact.content_digest
    assert len(twice.valid_completed_reports) == 2
    assert twice.budget.invocations == 2
    assert twice.budget.repairs == 0
    assert original.attempts == ()
    with pytest.raises(ValueError):
        begin(twice, 3)


def test_original_doer_repair_and_independent_second_review_close_changed_artifact() -> None:
    first = begin(session(), 1)
    reviewed = finish(first, report(first, findings=(finding(first),), fail=True,
                                    verdict="CHANGES_REQUIRED"))
    repairing = begin(reviewed, 2, repair=True)
    repaired = finish(repairing, repair_report(repairing))
    assert len(repaired.open_findings) == 1  # Producer statements cannot self-close.
    assert repaired.artifact != reviewed.artifact
    assert repaired.resolutions[0].producer == repaired.seed.original_doer
    assert not evaluate(repaired, current_artifact=repaired.artifact, now=at(8)).eligible
    second = begin(repaired, 3)
    done = finish(second, report(second, rechecks=closure(second)))
    assert evaluate(done, current_artifact=done.artifact, now=at(11)).eligible
    assert len(done.valid_completed_reports) == 2
    assert done.open_findings == ()
    assert done.budget.invocations == 3
    assert done.budget.repairs == 1


def test_optional_nitpick_repeated_twice_neither_blocks_nor_demands_a_repair() -> None:
    first = begin(session(), 1)
    first_done = finish(first, report(first, findings=(finding(first, advisory=True),)))
    with pytest.raises(ValueError):
        begin(first_done, 2, repair=True)
    second = begin(first_done, 2)
    done = finish(second, report(second, findings=(finding(second, advisory=True),)))
    assert evaluate(done, current_artifact=done.artifact, now=at(8)).eligible
    assert len(done.open_findings) == 1
    assert not done.open_findings[0].blocking
    assert done.terminal_reasons == ()


def test_mandatory_low_finding_cannot_be_waived_by_accept_or_passing_dispositions() -> None:
    first = begin(session(), 1)
    done = finish(first, report(first, findings=(finding(first),)))
    assert len(done.valid_completed_reports) == 1
    assert done.open_findings[0].blocking
    assert not evaluate(done, current_artifact=done.artifact, now=at(5)).eligible


@pytest.mark.parametrize("omit", [False, True])
def test_actual_open_blocker_repeats_even_if_paraphrased_or_omitted(omit: bool) -> None:
    first = begin(session(), 1)
    one = finish(first, report(first, findings=(finding(first),), fail=True,
                              verdict="CHANGES_REQUIRED"))
    second = begin(one, 2)
    findings = () if omit else (finding(second, consequence="Paraphrased wrong result"),)
    stopped = finish(second, report(second, findings=findings))
    assert "REPEATED_FAILURE" in stopped.terminal_reasons
    assert stopped.open_findings[0].occurrences == 2
    assert not evaluate(stopped, current_artifact=stopped.artifact, now=at(8)).eligible
    with pytest.raises(ValueError):
        begin(stopped, 3)


def test_critical_issue_stops_before_minimum_review_count() -> None:
    pending = begin(session(), 1)
    stopped = finish(pending, report(pending, findings=(finding(pending, severity="CRITICAL"),)))
    assert "CRITICAL" in stopped.terminal_reasons
    assert len(stopped.valid_completed_reports) == 1
    with pytest.raises(ValueError):
        begin(stopped, 2, repair=True)


def test_no_material_progress_is_a_stop_not_another_repair_loop() -> None:
    first = begin(session(), 1)
    reviewed = finish(first, report(first, findings=(finding(first),)))
    pending = begin(reviewed, 2, repair=True)
    stopped = finish(pending, repair_report(pending, artifact=pending.artifact))
    assert "NO_PROGRESS" in stopped.terminal_reasons
    assert stopped.artifact == reviewed.artifact
    assert stopped.budget.repairs == 1


def test_rebuttal_needs_evidence_and_independent_closure_not_an_edit() -> None:
    first = begin(session(), 1)
    reviewed = finish(first, report(first, findings=(finding(first),)))
    pending = begin(reviewed, 2, repair=True)
    rebutted = finish(pending, repair_report(
        pending, artifact=pending.artifact, disposition="REJECTED_WITH_EVIDENCE"))
    assert rebutted.artifact == reviewed.artifact
    assert rebutted.open_findings
    second = begin(rebutted, 3)
    done = finish(second, report(second, rechecks=closure(second)))
    assert evaluate(done, current_artifact=done.artifact, now=at(11)).eligible


def test_review_cap_does_not_approve_a_final_unreviewed_repair() -> None:
    once = clean(session(rules=policy(max_review_passes=2)), 1)
    second = begin(once, 2)
    stopped = finish(second, report(second, findings=(finding(second),)))
    assert "REVIEW_LIMIT" in stopped.terminal_reasons
    final = changed_artifact(stopped)
    assert not evaluate(stopped, current_artifact=final, now=at(8)).eligible
    with pytest.raises(ValueError):
        begin(stopped, 3, repair=True)


def test_configured_minimum_can_increase_but_not_reset_on_artifact_lineage() -> None:
    twice = clean(clean(session(rules=policy(min_review_passes=3)), 1), 2)
    assert not evaluate(twice, current_artifact=twice.artifact, now=at(8)).eligible
    done = clean(twice, 3)
    assert evaluate(done, current_artifact=done.artifact, now=at(11)).eligible