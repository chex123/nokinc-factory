"""Immutable local ARP-1 ledger and its replay-checked materialized state.

No in-memory object is durable or atomic. A trusted store must load the actual
latest session, prevent rollback/forking and reserve parent budgets before any
provider call. Public policy operations replay attempts rather than trusting
serialized counters, terminal flags or a supplied collection of clean reports.
"""

from enum import StrEnum
from typing import Literal

from pydantic import StrictBool

from nokinc_factory.domain.review import (
    ArtifactRef,
    ModelIdentity,
    QualityContract,
    Reservation,
    ReviewBinding,
    ReviewPolicy,
    ReviewScope,
    Usage,
)
from nokinc_factory.domain.review_base import Count, Digest, Identifier, Moment, ReviewModel
from nokinc_factory.domain.review_report import Finding, FindingResolution, ReviewReport


class ReviewReason(StrEnum):
    TIME_WINDOW = "TIME_WINDOW"
    ARTIFACT_DRIFT = "ARTIFACT_DRIFT"
    IDENTITY_UNAVAILABLE = "IDENTITY_UNAVAILABLE"
    BINDING_MISMATCH = "BINDING_MISMATCH"
    REPLAY = "REPLAY"
    RECEIPT_UNAVAILABLE = "RECEIPT_UNAVAILABLE"
    RECEIPT_MISMATCH = "RECEIPT_MISMATCH"
    AUTHORIZATION_REVOKED = "AUTHORIZATION_REVOKED"
    EXECUTION_INVALID = "EXECUTION_INVALID"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    BUDGET_OVERRUN = "BUDGET_OVERRUN"
    INVOCATION_LIMIT = "INVOCATION_LIMIT"
    TOKEN_LIMIT = "TOKEN_LIMIT"
    COST_LIMIT = "COST_LIMIT"
    REPAIR_LIMIT = "REPAIR_LIMIT"
    REVIEW_LIMIT = "REVIEW_LIMIT"
    REPEATED_FAILURE = "REPEATED_FAILURE"
    NO_PROGRESS = "NO_PROGRESS"
    CRITICAL = "CRITICAL"
    CONTRACT_GAP = "CONTRACT_GAP"
    CONTEXT_GAP = "CONTEXT_GAP"
    REVIEW_ESCALATED = "REVIEW_ESCALATED"
    MALFORMED_REPORT = "MALFORMED_REPORT"
    INCOMPLETE_CRITERIA = "INCOMPLETE_CRITERIA"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    INVALID_CLOSURE = "INVALID_CLOSURE"
    INVALID_REPAIR = "INVALID_REPAIR"
    UNSUPPORTED_VERDICT = "UNSUPPORTED_VERDICT"
    MANDATORY_CRITERIA = "MANDATORY_CRITERIA"
    MIN_REVIEWS = "MIN_REVIEWS"
    UNREVIEWED_ARTIFACT = "UNREVIEWED_ARTIFACT"
    OPEN_BLOCKERS = "OPEN_BLOCKERS"
    PENDING = "PENDING"


class ReviewSeed(ReviewModel):
    scope: ReviewScope
    contract: QualityContract
    policy: ReviewPolicy
    original_doer: ModelIdentity
    reviewers: tuple[ModelIdentity, ...]
    initial_artifact: ArtifactRef
    started_at: Moment


class Invocation(ReviewModel):
    invocation_id: Identifier
    kind: Literal["REVIEW", "REPAIR"]
    binding: ReviewBinding
    actor: ModelIdentity
    reservation: Reservation
    started_at: Moment


class InvocationReceipt(ReviewModel):
    """Trusted adapter observation, NOT a signature or an authenticated assertion.

    The runtime must establish execution_id uniqueness and actual resolved model
    identity itself. ``cached=False`` supplied by model text proves nothing.
    """

    binding: ReviewBinding
    invocation_id: Identifier
    execution_id: Identifier
    actual_model: ModelIdentity
    usage: Usage
    cached: StrictBool


ExecutionStop = Literal[
    "ARTIFACT_DRIFT", "TIME_WINDOW", "AUTHORIZATION_REVOKED", "EXECUTION_INVALID", "OUTPUT_LIMIT"
]


class Completion(ReviewModel):
    """Trusted execution observation; failure_reason is never taken from model output."""

    invocation_id: Identifier
    finished_at: Moment
    receipt: InvocationReceipt | None
    output_json: str | None
    failure_reason: ExecutionStop | None = None


class ReviewAttempt(ReviewModel):
    invocation: Invocation
    completion: Completion | None = None


class FindingRecord(ReviewModel):
    finding: Finding
    report_digest: Digest
    blocking: StrictBool
    occurrences: Count = 0
    closed_by: Digest | None = None
    closed_artifact_digest: Digest | None = None


class ResolutionRecord(ReviewModel):
    resolution: FindingResolution
    repair_invocation_id: Identifier
    artifact_digest: Digest
    producer: ModelIdentity


class FailureCount(ReviewModel):
    reason: ReviewReason
    occurrences: Count


class BudgetState(ReviewModel):
    invocations: Count = 0
    repairs: Count = 0
    tokens_spent: Count = 0
    cost_microusd_spent: Count = 0
    tokens_reserved: Count = 0
    cost_microusd_reserved: Count = 0


class ReviewState(ReviewModel):
    artifact: ArtifactRef
    last_at: Moment
    budget: BudgetState
    pending: Invocation | None = None
    invocation_ids: tuple[Identifier, ...] = ()
    execution_ids: tuple[Identifier, ...] = ()
    valid_completed_reports: tuple[ReviewReport, ...] = ()
    findings: tuple[FindingRecord, ...] = ()
    resolutions: tuple[ResolutionRecord, ...] = ()
    failures: tuple[FailureCount, ...] = ()
    terminal_reasons: tuple[ReviewReason, ...] = ()

    @property
    def open_findings(self) -> tuple[FindingRecord, ...]:
        return tuple(item for item in self.findings if item.closed_by is None)


class ReviewSession(ReviewModel):
    seed: ReviewSeed
    attempts: tuple[ReviewAttempt, ...]
    state: ReviewState

    @property
    def artifact(self) -> ArtifactRef:
        return self.state.artifact

    @property
    def valid_completed_reports(self) -> tuple[ReviewReport, ...]:
        return self.state.valid_completed_reports

    @property
    def open_findings(self) -> tuple[FindingRecord, ...]:
        return self.state.open_findings

    @property
    def resolutions(self) -> tuple[ResolutionRecord, ...]:
        return self.state.resolutions

    @property
    def budget(self) -> BudgetState:
        return self.state.budget

    @property
    def terminal_reasons(self) -> tuple[ReviewReason, ...]:
        return self.state.terminal_reasons


class ReviewDecision(ReviewModel):
    """Eligibility for a governed stage, never merge/release/action authorization."""

    status: Literal["ELIGIBLE", "NEEDS_REVIEW", "NEEDS_REPAIR", "BLOCKED", "ESCALATE"]
    reasons: tuple[ReviewReason, ...]
    session_digest: Digest
    artifact_digest: Digest
    completed_reviews: Count

    @property
    def eligible(self) -> bool:
        return self.status == "ELIGIBLE"