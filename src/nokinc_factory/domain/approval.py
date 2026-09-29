"""Content-bound approval facts; evidence alone never authorizes execution.

Spec Part 1 treats provider position and execution authority as separate. This
module verifies that a provider run matches a canonical issue/work-item/digest
and that its protected Environment review records match the configured people.
Policy must still decide whether the verified evidence permits a transition.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, StrictInt, model_validator

from nokinc_factory.domain.review_base import Digest, Identifier, ReviewModel, distinct

ApprovalGate = Literal["gate-1", "gate-2", "gate-3", "gate-4"]
_RUN_NAME_PREFIX = "factory-approval"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    INVALID = "INVALID"


class ExpectedReviewer(ReviewModel):
    """Canonical provider identity required for one Environment."""

    login: Identifier
    user_id: StrictInt = Field(gt=0)


class ApprovalConfig(ReviewModel):
    """Pinned provider location, workflow, ref, and two expected identities."""

    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    workflow_path: str = Field(pattern=r"^\.github/workflows/[A-Za-z0-9_.-]+\.yml$")
    default_branch: Identifier
    reviewers: tuple[ExpectedReviewer, ExpectedReviewer]

    @model_validator(mode="after")
    def _two_distinct_reviewers(self) -> Self:
        distinct(reviewer.login.casefold() for reviewer in self.reviewers)
        distinct(str(reviewer.user_id) for reviewer in self.reviewers)
        return self


class ApprovalIntent(ReviewModel):
    """Exact gate decision that a canonical ALM issue is being asked to review."""

    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    work_item_id: Identifier = Field(max_length=80)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    issue_number: StrictInt = Field(gt=0)
    gate: ApprovalGate
    decision_digest: Digest
    requester_login: Identifier
    ref: Identifier

    @property
    def display_title(self) -> str:
        return "/".join((
            _RUN_NAME_PREFIX,
            self.request_id,
            self.work_item_id,
            str(self.issue_number),
            self.gate,
            self.decision_digest,
        ))


class ApprovalRun(ReviewModel):
    """Provider-returned workflow facts used for binding, never trusted by labels."""

    run_id: StrictInt = Field(gt=0)
    run_attempt: StrictInt = Field(ge=1)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    workflow_path: str = Field(pattern=r"^\.github/workflows/[A-Za-z0-9_.-]+\.yml$")
    head_branch: Identifier
    head_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    event: str = Field(min_length=1)
    display_title: str = Field(min_length=1)
    status: Literal[
        "queued", "in_progress", "waiting", "requested", "pending",
        "action_required", "completed",
    ]
    conclusion: str | None = None


class EnvironmentReview(ReviewModel):
    """One provider-authenticated Environment approval record."""

    state: Literal["approved", "rejected", "pending"]
    reviewer_login: Identifier
    reviewer_id: StrictInt = Field(gt=0)
    environments: tuple[Identifier, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def _unique_environments(self) -> Self:
        distinct(self.environments)
        return self


class ApprovalEvidence(ReviewModel):
    """Verified provider observation; it is explicitly not a policy authorization."""

    request_id: Identifier
    work_item_id: Identifier
    repository: str
    issue_number: StrictInt = Field(gt=0)
    gate: ApprovalGate
    decision_digest: Digest
    run_id: StrictInt = Field(gt=0)
    run_attempt: StrictInt = Field(ge=1)
    status: ApprovalStatus
    approver_logins: tuple[Identifier, ...] = ()
    reason_codes: tuple[Identifier, ...] = ()
    authorizes_execution: Literal[False] = False


class ApprovalDispatch(ReviewModel):
    """Provider dispatch receipt bound to the exact previously validated intent."""

    request_id: Identifier
    intent_digest: Digest
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    run_id: StrictInt = Field(gt=0)
    run_attempt: StrictInt = Field(ge=1)
    run_url: str = Field(min_length=1)


def verify_github_approval(
    *,
    intent: ApprovalIntent,
    config: ApprovalConfig,
    run: ApprovalRun,
    reviews: tuple[EnvironmentReview, ...],
    expected_run_id: int,
    expected_run_attempt: int,
) -> ApprovalEvidence:
    """Verify run identity and two actual reviewer records, without granting authority."""
    reasons: list[str] = []
    if intent.repository != config.repository:
        reasons.append("REQUEST_REPOSITORY_MISMATCH")
    if intent.ref != config.default_branch:
        reasons.append("REQUEST_REF_NOT_PROTECTED_DEFAULT")
    if run.run_id != expected_run_id or run.run_attempt != expected_run_attempt:
        reasons.append("RUN_IDENTITY_MISMATCH")
    if (
        run.repository != config.repository
        or run.workflow_path != config.workflow_path
        or run.head_branch != config.default_branch
        or run.event != "workflow_dispatch"
        or run.display_title != intent.display_title
    ):
        reasons.append("RUN_BINDING_MISMATCH")
    if reasons:
        return _evidence(intent, run, ApprovalStatus.INVALID, reasons=tuple(reasons))

    if run.status != "completed":
        return _evidence(intent, run, ApprovalStatus.PENDING)
    if run.conclusion != "success":
        return _evidence(intent, run, ApprovalStatus.FAILED, reasons=("RUN_NOT_SUCCESSFUL",))

    if len(reviews) != len(config.reviewers):
        return _evidence(intent, run, ApprovalStatus.INVALID, reasons=("REVIEW_SET_INCOMPLETE",))

    expected_by_login = {reviewer.login.casefold(): reviewer for reviewer in config.reviewers}
    seen_logins: set[str] = set()
    seen_ids: set[int] = set()
    for review in reviews:
        login = review.reviewer_login.casefold()
        expected = expected_by_login.get(login)
        if expected is None or review.reviewer_id != expected.user_id:
            reasons.append("REVIEWER_IDENTITY_MISMATCH")
            continue
        if login in seen_logins or review.reviewer_id in seen_ids:
            reasons.append("REVIEWER_NOT_DISTINCT")
        seen_logins.add(login)
        seen_ids.add(review.reviewer_id)
        if login == intent.requester_login.casefold():
            reasons.append("REVIEWER_IS_REQUESTER")
        expected_environment = f"{intent.gate}-{expected.login}"
        if review.environments != (expected_environment,):
            reasons.append("REVIEW_ENVIRONMENT_MISMATCH")
        if review.state != "approved":
            reasons.append("REVIEW_NOT_APPROVED")

    expected_logins = set(expected_by_login)
    if seen_logins != expected_logins:
        reasons.append("REVIEW_SET_INCOMPLETE")
    if reasons:
        return _evidence(intent, run, ApprovalStatus.INVALID, reasons=tuple(sorted(set(reasons))))

    approvers = tuple(sorted(seen_logins))
    return _evidence(intent, run, ApprovalStatus.APPROVED, approvers=approvers)


def _evidence(
    intent: ApprovalIntent,
    run: ApprovalRun,
    status: ApprovalStatus,
    *,
    approvers: tuple[str, ...] = (),
    reasons: tuple[str, ...] = (),
) -> ApprovalEvidence:
    return ApprovalEvidence(
        request_id=intent.request_id,
        work_item_id=intent.work_item_id,
        repository=intent.repository,
        issue_number=intent.issue_number,
        gate=intent.gate,
        decision_digest=intent.decision_digest,
        run_id=run.run_id,
        run_attempt=run.run_attempt,
        status=status,
        approver_logins=approvers,
        reason_codes=reasons,
    )
