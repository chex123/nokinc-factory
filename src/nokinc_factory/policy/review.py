"""Isolated deterministic ARP-1: start -> reserve -> complete -> evaluate.

Call ``start_session`` with trusted contract/model/lineage pins and injected time.
Persist ``reserve_invocation`` before a read-only review or original-doer repair;
``complete_invocation`` consumes that attempt even for malformed/missing output.
``evaluate`` replays the actual local ledger and binds the latest final artifact.
It returns stage eligibility only, never an approval or an executable action.
Receipt, schema/hash and all supplied artifact/evidence bindings must pass before
any report stop is trusted. Bound stops survive incomplete coverage or invalid
closure without completion credit, closure effects or advancing existing finding
occurrences. Completion failures still consume their invocation and spend limits.
Resolved findings reopen only for a changed artifact or genuinely new evidence
digests against the accumulated bound case history, not evidence deletion/order.

TRUSTED ORCHESTRATOR BOUNDARY: authenticate tenant and actor, authorize immutable
pins/risk/budgets, establish exact provider executions and noncached receipts,
normalize failure keys, independently retrieve context/evidence and verify bytes.
Use a durable CAS store with atomic parent reservations/global replay protection;
load its current state (not a caller-selected snapshot). Local hashes cannot
detect a consistently forged history or rollback to a formerly valid snapshot.
Recheck freshness, revocation, target drift and required humans before effects.
This module performs none of those external actions and provides no agent loop.
"""

from datetime import datetime
from typing import Literal

from nokinc_factory.domain.review import (
    ArtifactRef,
    ModelIdentity,
    QualityContract,
    Reservation,
    ReviewPolicy,
    ReviewScope,
)
from nokinc_factory.domain.review_base import utc
from nokinc_factory.domain.review_report import RepairReport, ReviewReport
from nokinc_factory.domain.review_session import (
    Completion,
    ExecutionStop,
    Invocation,
    InvocationReceipt,
    ReviewAttempt,
    ReviewDecision,
    ReviewReason,
    ReviewSeed,
    ReviewSession,
)
from nokinc_factory.policy.review_reducer import complete, initial_state, reserve
from nokinc_factory.policy.review_rules import eligibility_reasons, needs_repair, within_window


def validate_session(session: ReviewSession) -> ReviewSession:
    """Never consume nonvalidating model_copy state or caller-fabricated counters."""
    session = ReviewSession.model_validate(session)
    expected = initial_state(session.seed)
    for attempt in session.attempts:
        expected = reserve(session.seed, expected, attempt.invocation)
        if attempt.completion is not None:
            expected = complete(session.seed, expected, attempt.completion)
    if expected != session.state:
        raise ValueError("Review state disagrees with the actual attempt ledger")
    return session


def start_session(*, scope: ReviewScope, contract: QualityContract, policy: ReviewPolicy,
                  original_doer: ModelIdentity, reviewers: tuple[ModelIdentity, ...],
                  artifact: ArtifactRef, now: datetime) -> ReviewSession:
    """Start one local unit; only the external store can prevent parent/run resets."""
    seed = ReviewSeed(scope=scope, contract=contract, policy=policy, original_doer=original_doer,
                      reviewers=reviewers, initial_artifact=artifact, started_at=utc(now))
    return ReviewSession(seed=seed, attempts=(), state=initial_state(seed))


def reserve_invocation(session: ReviewSession, *, invocation_id: str,
                       kind: Literal["REVIEW", "REPAIR"], actor: ModelIdentity,
                       reservation: Reservation, now: datetime) -> ReviewSession:
    """Reserve locally before a call. Denials perform no execution and spend nothing.

    Only one pending local attempt is supported. After a terminal/capped review,
    further repairs are denied; any out-of-band final edit remains ineligible.
    """
    session = validate_session(session)
    invocation = Invocation(invocation_id=invocation_id, kind=kind, actor=actor,
                            binding=session.artifact.binding(), reservation=reservation,
                            started_at=utc(now))
    state = reserve(session.seed, session.state, invocation)
    return ReviewSession(seed=session.seed, state=state,
                         attempts=(*session.attempts, ReviewAttempt(invocation=invocation)))


def complete_invocation(session: ReviewSession, *, invocation_id: str,
                        receipt: InvocationReceipt | None,
                        output: ReviewReport | RepairReport | str | None,
                        now: datetime, failure_reason: ExecutionStop | None = None,
                        ) -> ReviewSession:
    """Record delivery once; malformed text consumes capacity but never a pass.

    For an outage use output=None/receipt=None: the full reservation is charged.
    Invalid receipt objects raise and leave the persisted reservation held; a
    trusted runtime must settle that unavailable invocation, not start it again.
    """
    session = validate_session(session)
    now = utc(now)
    output_json = (output.model_dump_json()
                   if isinstance(output, (ReviewReport, RepairReport)) else output)
    receipt = InvocationReceipt.model_validate(receipt) if receipt is not None else None
    previous = next((a for a in session.attempts
                     if a.invocation.invocation_id == invocation_id), None)
    if previous is None:
        raise ValueError("Completion has no reserved invocation")
    if previous.completion is not None:
        if (previous.completion.receipt == receipt
            and previous.completion.output_json == output_json
            and previous.completion.failure_reason == failure_reason):
            return session
        raise ValueError("Conflicting duplicate review delivery")
    completion = Completion(invocation_id=invocation_id, receipt=receipt,
                            output_json=output_json, finished_at=now, failure_reason=failure_reason)
    state = complete(session.seed, session.state, completion)
    attempt = ReviewAttempt(invocation=previous.invocation, completion=completion)
    return ReviewSession(seed=session.seed, state=state, attempts=(*session.attempts[:-1], attempt))


def evaluate(session: ReviewSession, *, current_artifact: ArtifactRef,
             now: datetime) -> ReviewDecision:
    """Eligibility binds replayed state AND the broker's latest observed artifact.

    Invalid structural/hash state raises; ordinary incomplete, stale or blocked
    evidence returns an ineligible decision. No human or machine gate is waived.
    """
    session = validate_session(session)
    current_artifact = ArtifactRef.model_validate(current_artifact)
    now = utc(now)
    reasons = list(eligibility_reasons(session.seed, session.state))
    if current_artifact != session.artifact:
        reasons.append(ReviewReason.ARTIFACT_DRIFT)
    if not within_window(session.seed, session.state, now):
        reasons.append(ReviewReason.TIME_WINDOW)
    status: Literal["ELIGIBLE", "NEEDS_REVIEW", "NEEDS_REPAIR", "BLOCKED", "ESCALATE"]
    if not reasons:
        status = "ELIGIBLE"
    elif any(reason in reasons for reason in (ReviewReason.CRITICAL, ReviewReason.CONTRACT_GAP,
                                             ReviewReason.REVIEW_ESCALATED,
                                             ReviewReason.REPEATED_FAILURE)):
        status = "ESCALATE"
    elif session.terminal_reasons or any(reason in reasons for reason in (
        ReviewReason.ARTIFACT_DRIFT, ReviewReason.TIME_WINDOW,
    )):
        status = "BLOCKED"
    elif needs_repair(session.state) and session.budget.repairs < (
        session.seed.policy.max_implementation_repairs
    ):
        status = "NEEDS_REPAIR"
    else:
        status = "NEEDS_REVIEW"
    return ReviewDecision(status=status, reasons=tuple(dict.fromkeys(reasons)),
                          session_digest=session.content_digest,
                          artifact_digest=session.artifact.content_digest,
                          completed_reviews=len(session.valid_completed_reports))