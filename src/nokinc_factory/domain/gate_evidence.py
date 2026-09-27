"""Content-bound test evidence, not approval or proof of an authenticated runner.

Spec Part 8 distinguishes an intended assertion failure from collection/runtime
errors. The trusted pipeline supplies these observations and separately proves
source, suite, runner and environment identity; agents cannot manufacture them.
"""

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, StrictBool, model_validator

from nokinc_factory.domain.review_base import Digest, Identifier, ReviewModel, distinct, nonblank

GitSHA = Annotated[str, Field(strict=True, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
CaseID = Annotated[str, Field(strict=True, min_length=1, max_length=1000), AfterValidator(nonblank)]
CaseOutcome = Literal["PASS", "ASSERTION_FAILURE", "ERROR", "SKIPPED"]
StoryKind = Literal["NEW_BEHAVIOR", "BUG_FIX", "REFACTOR", "INFRASTRUCTURE"]


class CaseResult(ReviewModel):
    case_id: CaseID
    outcome: CaseOutcome


class ProbeBinding(ReviewModel):
    """Runner-supplied identities; declaring them does not authenticate their provenance."""

    work_item_id: Identifier
    run_id: Identifier
    source_sha: GitSHA
    suite_digest: Digest
    runner_digest: Digest
    environment_digest: Digest


class SuiteRun(ProbeBinding):
    completed: StrictBool
    exit_code: Annotated[int, Field(strict=True, ge=0, le=255)]
    collection_errors: Annotated[int, Field(strict=True, ge=0, le=10_000)]
    cases: tuple[CaseResult, ...] = Field(max_length=10_000)

    @model_validator(mode="after")
    def _case_ids(self) -> Self:
        distinct(item.case_id for item in self.cases)
        return self


class BaselineContract(ReviewModel):
    work_item_id: Identifier
    story_kind: StoryKind
    baseline_sha: GitSHA
    baseline_suite_digest: Digest
    frozen_suite_digest: Digest
    runner_digest: Digest
    environment_digest: Digest
    baseline_cases: tuple[CaseID, ...] = Field(max_length=10_000)
    frozen_cases: tuple[CaseID, ...] = Field(max_length=10_000)
    expected_red_cases: tuple[CaseID, ...] = Field(max_length=10_000)

    @model_validator(mode="after")
    def _scoped_expectations(self) -> Self:
        for inventory in (self.baseline_cases, self.frozen_cases, self.expected_red_cases):
            distinct(inventory)
        before, frozen, red = (set(self.baseline_cases), set(self.frozen_cases),
                               set(self.expected_red_cases))
        if self.story_kind == "INFRASTRUCTURE":
            if red:
                raise ValueError("Infrastructure-only baseline cannot claim behavior failures")
        elif not before or not frozen or not before <= frozen:
            raise ValueError("Explicit old and frozen inventories must preserve existing cases")
        elif self.story_kind == "REFACTOR":
            if red or before != frozen or self.baseline_suite_digest != self.frozen_suite_digest:
                raise ValueError("Refactor characterization suite must be unchanged")
        elif not red or not red <= frozen - before:
            raise ValueError("Expected red cases must be explicit new behavior/regression cases")
        return self


class TestEvidenceDecision(ReviewModel):
    status: Literal["PASS", "FAIL", "NOT_AVAILABLE", "NOT_APPLICABLE"]
    evidence_scope: Literal["DECLARED_EVIDENCE"] = "DECLARED_EVIDENCE"
    authorizes_merge: Literal[False] = False
    contract_digest: Digest
    evidence_digests: tuple[Digest, ...]
    reasons: tuple[Identifier, ...]