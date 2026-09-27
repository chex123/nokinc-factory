"""Deterministic red/green baseline and candidate rules (spec Part 8).

No command execution, clocks, retries or model judgment. A decision evaluates
supplied evidence only; authenticated runner provenance and frozen authoring
authority must be checked by the pipeline before any consequential operation.
"""

from pydantic import TypeAdapter

from nokinc_factory.domain.gate_evidence import (
    BaselineContract,
    GitSHA,
    SuiteRun,
    TestEvidenceDecision,
)


def _identity(contract: BaselineContract, run: SuiteRun, source: str, suite: str) -> list[str]:
    checks = (
        (run.work_item_id == contract.work_item_id, "WORK_ITEM_MISMATCH"),
        (run.source_sha == source, "SOURCE_MISMATCH"),
        (run.suite_digest == suite, "SUITE_MISMATCH"),
        (run.runner_digest == contract.runner_digest, "RUNNER_MISMATCH"),
        (run.environment_digest == contract.environment_digest, "ENVIRONMENT_MISMATCH"),
        (run.completed, "INCOMPLETE_EXECUTION"),
        (run.collection_errors == 0, "COLLECTION_ERRORS"),
    )
    return [reason for valid, reason in checks if not valid]


def _outcomes(run: SuiteRun, expected: tuple[str, ...], red: tuple[str, ...] = ()) -> list[str]:
    reasons: list[str] = []
    actual = {item.case_id: item.outcome for item in run.cases}
    if actual.keys() != set(expected) or not expected:
        reasons.append("INVENTORY_MISMATCH")
    failures = set(red)
    if run.exit_code != int(bool(failures)):
        reasons.append("UNEXPECTED_PROCESS_EXIT")
    for case_id in expected:
        required = "ASSERTION_FAILURE" if case_id in failures else "PASS"
        if actual.get(case_id) != required:
            reasons.append("UNEXPECTED_TEST_OUTCOME")
    return reasons


def _decision(contract: BaselineContract, reports: tuple[SuiteRun, ...], reasons: list[str]
              ) -> TestEvidenceDecision:
    return TestEvidenceDecision(
        status="FAIL" if reasons else "PASS", contract_digest=contract.content_digest,
        evidence_digests=tuple(run.content_digest for run in reports),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def verify_baseline(contract: BaselineContract, before: SuiteRun | None,
                    frozen_on_baseline: SuiteRun | None) -> TestEvidenceDecision:
    """Only declared new assertions may fail, after the old baseline proves green."""
    contract = BaselineContract.model_validate(contract)
    if contract.story_kind == "INFRASTRUCTURE":
        return TestEvidenceDecision(
            status="NOT_APPLICABLE", contract_digest=contract.content_digest,
            evidence_digests=(), reasons=("INFRASTRUCTURE_ONLY",),
        )
    if before is None or frozen_on_baseline is None:
        return TestEvidenceDecision(status="NOT_AVAILABLE", contract_digest=contract.content_digest,
                                    evidence_digests=(), reasons=("MISSING_BASELINE_EXECUTION",))
    before = SuiteRun.model_validate(before)
    frozen = SuiteRun.model_validate(frozen_on_baseline)
    reasons = _identity(contract, before, contract.baseline_sha, contract.baseline_suite_digest)
    reasons.extend(_identity(contract, frozen, contract.baseline_sha, contract.frozen_suite_digest))
    if before.run_id == frozen.run_id:
        reasons.append("REPLAYED_RUN")
    reasons.extend(_outcomes(before, contract.baseline_cases))
    reasons.extend(_outcomes(frozen, contract.frozen_cases, contract.expected_red_cases))
    return _decision(contract, (before, frozen), reasons)


def verify_candidate(contract: BaselineContract, candidate_sha: str,
                     candidate: SuiteRun | None) -> TestEvidenceDecision:
    """Green implementation uses the identical frozen test contract and exact candidate."""
    contract = BaselineContract.model_validate(contract)
    candidate_sha = TypeAdapter(GitSHA).validate_python(candidate_sha)
    if candidate is None:
        return TestEvidenceDecision(status="NOT_AVAILABLE", contract_digest=contract.content_digest,
                                    evidence_digests=(), reasons=("MISSING_CANDIDATE_EXECUTION",))
    candidate = SuiteRun.model_validate(candidate)
    reasons = _identity(contract, candidate, candidate_sha, contract.frozen_suite_digest)
    reasons.extend(_outcomes(candidate, contract.frozen_cases))
    return _decision(contract, (candidate,), reasons)