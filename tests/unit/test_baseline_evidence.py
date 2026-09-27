"""Spec Part 8: expected red behavior is not a broken test harness or vacuous pass."""

import pytest
from pydantic import ValidationError

from nokinc_factory.domain.gate_evidence import BaselineContract, CaseResult, SuiteRun
from nokinc_factory.domain.review_base import renewed
from nokinc_factory.policy.baseline import verify_baseline, verify_candidate

BASE = "a" * 40
CANDIDATE = "b" * 40
OLD_SUITE = "sha256:" + "1" * 64
FROZEN_SUITE = "sha256:" + "2" * 64
RUNNER = "sha256:" + "3" * 64
ENVIRONMENT = "sha256:" + "4" * 64


def contract(**changes):
    return BaselineContract.model_validate({
        "work_item_id": "story-1", "story_kind": "NEW_BEHAVIOR", "baseline_sha": BASE,
        "baseline_suite_digest": OLD_SUITE, "frozen_suite_digest": FROZEN_SUITE,
        "runner_digest": RUNNER, "environment_digest": ENVIRONMENT,
        "baseline_cases": ("tests/acceptance/test_old.py::test_old",),
        "frozen_cases": ("tests/acceptance/test_old.py::test_old",
                         "tests/acceptance/test_new.py::test_new"),
        "expected_red_cases": ("tests/acceptance/test_new.py::test_new",),
    } | changes)


def suite(*, red=False, old=False, source=BASE, **changes):
    cases = [CaseResult(case_id="tests/acceptance/test_old.py::test_old", outcome="PASS")]
    if not old:
        cases.append(CaseResult(case_id="tests/acceptance/test_new.py::test_new",
                                outcome="ASSERTION_FAILURE" if red else "PASS"))
    return SuiteRun.model_validate({
        "work_item_id": "story-1", "run_id": "old" if old else "frozen",
        "source_sha": source, "suite_digest": OLD_SUITE if old else FROZEN_SUITE,
        "runner_digest": RUNNER, "environment_digest": ENVIRONMENT,
        "completed": True, "exit_code": 1 if red else 0,
        "collection_errors": 0, "cases": tuple(cases),
    } | changes)


@pytest.mark.parametrize("kind", ["NEW_BEHAVIOR", "BUG_FIX"])
def test_correct_expected_failures_prove_behavior_on_clean_baseline(kind) -> None:
    result = verify_baseline(contract(story_kind=kind), suite(old=True), suite(red=True))
    assert result.status == "PASS"
    assert not result.authorizes_merge
    assert result.contract_digest == contract(story_kind=kind).content_digest


def test_new_behavior_passing_on_baseline_is_not_proof() -> None:
    assert verify_baseline(contract(), suite(old=True), suite()).status == "FAIL"


@pytest.mark.parametrize("field,value", [
    ("source_sha", CANDIDATE), ("suite_digest", OLD_SUITE),
    ("runner_digest", OLD_SUITE), ("environment_digest", OLD_SUITE),
    ("work_item_id", "other-story"), ("run_id", "old"),
    ("exit_code", 0), ("exit_code", 2), ("exit_code", 5),
    ("collection_errors", 1), ("completed", False),
])
def test_stale_incomplete_or_invalid_red_evidence_fails_closed(field, value) -> None:
    bad = renewed(suite(red=True), **{field: value})
    assert verify_baseline(contract(), suite(old=True), bad).status == "FAIL"


@pytest.mark.parametrize("outcome", ["ERROR", "SKIPPED"])
def test_wrong_failure_kind_cannot_satisfy_red_requirement(outcome) -> None:
    old, new = suite(red=True).cases
    run = renewed(suite(red=True), cases=(old, renewed(new, outcome=outcome)))
    assert verify_baseline(contract(), suite(old=True), run).status == "FAIL"


def test_regressing_existing_behavior_is_not_an_expected_red_case() -> None:
    old, new = suite(red=True).cases
    bad = renewed(suite(red=True), cases=(renewed(old, outcome="ASSERTION_FAILURE"), new))
    assert verify_baseline(contract(), suite(old=True), bad).status == "FAIL"


def test_original_baseline_must_already_be_green() -> None:
    old = suite(old=True)
    bad = renewed(old, exit_code=1,
                  cases=(renewed(old.cases[0], outcome="ASSERTION_FAILURE"),))
    assert verify_baseline(contract(), bad, suite(red=True)).status == "FAIL"


def test_missing_or_extra_case_is_not_complete_evidence() -> None:
    red = suite(red=True)
    for cases in (red.cases[:1], (*red.cases, CaseResult(case_id="extra", outcome="PASS"))):
        result = verify_baseline(contract(), suite(old=True), renewed(red, cases=cases))
        assert result.status == "FAIL"


def test_refactor_requires_unchanged_suite_green_before_and_after() -> None:
    refactor = contract(story_kind="REFACTOR", frozen_cases=contract().baseline_cases,
                        frozen_suite_digest=OLD_SUITE, expected_red_cases=())
    before = suite(old=True)
    baseline = renewed(before, run_id="refactor-baseline")
    assert verify_baseline(refactor, before, baseline).status == "PASS"
    candidate = renewed(baseline, run_id="candidate", source_sha=CANDIDATE)
    assert verify_candidate(refactor, CANDIDATE, candidate).status == "PASS"


def test_infrastructure_baseline_is_explicitly_not_applicable_not_pass() -> None:
    infra = contract(story_kind="INFRASTRUCTURE", expected_red_cases=())
    assert verify_baseline(infra, None, None).status == "NOT_APPLICABLE"


def test_missing_required_runner_is_never_a_pass() -> None:
    assert verify_baseline(contract(), None, None).status == "NOT_AVAILABLE"
    assert verify_candidate(contract(), CANDIDATE, None).status == "NOT_AVAILABLE"


def test_candidate_requires_exact_sha_frozen_suite_and_no_skipped_tests() -> None:
    green = suite(source=CANDIDATE)
    assert verify_candidate(contract(), CANDIDATE, green).status == "PASS"
    for bad in (suite(red=True, source=CANDIDATE), suite(),
                renewed(green, suite_digest=OLD_SUITE),
                renewed(green, exit_code=1), renewed(green, completed=False)):
        assert verify_candidate(contract(), CANDIDATE, bad).status == "FAIL"


@pytest.mark.parametrize("changes", [
    {"expected_red_cases": ()},
    {"expected_red_cases": ("missing",)},
    {"expected_red_cases": ("tests/acceptance/test_old.py::test_old",)},
    {"baseline_cases": ()},
    {"story_kind": "REFACTOR", "expected_red_cases": ()},
    {"frozen_cases": ("duplicate", "duplicate")},
])
def test_inconsistent_declared_contract_is_rejected(changes) -> None:
    with pytest.raises(ValidationError):
        contract(**changes)


def test_duplicate_case_ids_and_mutated_counters_cannot_be_smuggled() -> None:
    old = suite(old=True)
    with pytest.raises(ValidationError):
        renewed(old, cases=(*old.cases, *old.cases))
    forged = old.model_copy(update={"exit_code": 0, "completed": "yes"})
    with pytest.raises(ValidationError):
        verify_baseline(contract(), forged, suite(red=True))