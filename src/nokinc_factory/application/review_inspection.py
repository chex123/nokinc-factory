"""Read-only review evidence views; archived material cannot establish live authority.

Replay verifies internal consistency only. These views never verify an external
artifact, authenticate approvers or complete the release-to-runtime trace chain.
Do not include model report prose, prompts, configuration values or credentials.
"""

from datetime import datetime
from typing import Literal

from nokinc_factory.domain.review_base import Digest, Identifier, Moment, ReviewModel, utc
from nokinc_factory.domain.review_session import ReviewDecision, ReviewSession
from nokinc_factory.policy.review import evaluate, validate_session


class ReviewInspection(ReviewModel):
    evidence_scope: Literal["ARCHIVED_ADVISORY"] = "ARCHIVED_ADVISORY"
    live_artifact_verified: Literal[False] = False
    deployment_authorized: Literal[False] = False
    tenant_id: Identifier
    work_item_id: Identifier
    unit_id: Identifier
    session_digest: Digest
    completed_reviews: int
    pending_invocation: Identifier | None
    inspected_at: Moment
    snapshot_decision: ReviewDecision


class AttemptTrace(ReviewModel):
    invocation_id: Identifier
    kind: Literal["REVIEW", "REPAIR"]
    invocation_digest: Digest
    artifact_digest: Digest
    started_at: Moment
    finished_at: Moment | None
    completion_digest: Digest | None
    receipt_digest: Digest | None


class ReviewTrace(ReviewModel):
    evidence_scope: Literal["ARCHIVED_ADVISORY"] = "ARCHIVED_ADVISORY"
    deployment_authorized: Literal[False] = False
    release_chain_complete: Literal[False] = False
    work_item_id: Identifier
    session_digest: Digest
    contract_digest: Digest
    context_digest: Digest
    artifact_digest: Digest
    attempts: tuple[AttemptTrace, ...]
    missing_release_evidence: tuple[str, ...] = (
        "verified merge candidate", "signed release bundle",
        "deployment binding", "runtime receipt",
    )


def inspect_review(session: ReviewSession, *, now: datetime) -> ReviewInspection:
    session = validate_session(session)
    now = utc(now)
    return ReviewInspection(
        tenant_id=session.seed.scope.tenant_id, work_item_id=session.seed.scope.work_item_id,
        unit_id=session.seed.scope.session_id, session_digest=session.content_digest,
        completed_reviews=len(session.valid_completed_reports), inspected_at=now,
        pending_invocation=(session.state.pending.invocation_id if session.state.pending else None),
        snapshot_decision=evaluate(session, current_artifact=session.artifact, now=now),
    )


def trace_review(session: ReviewSession, *, work_item_id: str) -> ReviewTrace:
    session = validate_session(session)
    if session.seed.scope.work_item_id != work_item_id:
        raise ValueError("Work-item identity does not match review evidence")
    attempts = []
    for attempt in session.attempts:
        completion = attempt.completion
        attempts.append(AttemptTrace(
            invocation_id=attempt.invocation.invocation_id, kind=attempt.invocation.kind,
            invocation_digest=attempt.invocation.content_digest,
            artifact_digest=attempt.invocation.binding.artifact_digest,
            started_at=attempt.invocation.started_at,
            finished_at=completion.finished_at if completion else None,
            completion_digest=completion.content_digest if completion else None,
            receipt_digest=(completion.receipt.content_digest
                            if completion and completion.receipt else None),
        ))
    return ReviewTrace(
        work_item_id=work_item_id, session_digest=session.content_digest,
        contract_digest=session.seed.contract.content_digest,
        context_digest=session.artifact.context_digest,
        artifact_digest=session.artifact.content_digest,
        attempts=tuple(attempts),
    )