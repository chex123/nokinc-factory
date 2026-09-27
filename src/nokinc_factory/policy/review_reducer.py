"""Pure local attempt reducers; no calls, clocks, storage, prompts or agent loop.

Reserve before execution. Invalid or unavailable completions cannot undo the
attempt; missing/untrustworthy receipts charge at least the full reservation.
Overruns are recorded, not clipped. The caller must persist before side effects.
"""

from pydantic import ValidationError

from nokinc_factory.domain.review_base import content_digest, renewed
from nokinc_factory.domain.review_report import RepairReport, ReviewReport
from nokinc_factory.domain.review_session import (
    BudgetState,
    Completion,
    FailureCount,
    Invocation,
    ReviewReason,
    ReviewSeed,
    ReviewState,
)
from nokinc_factory.policy.review_findings import reconcile_findings, reconcile_repair, retain_stops
from nokinc_factory.policy.review_rules import (
    ReviewViolation,
    eligibility_reasons,
    independent,
    needs_repair,
    require,
    validate_report,
    validate_report_binding,
    validate_seed,
    within_window,
)


def initial_state(seed: ReviewSeed) -> ReviewState:
    validate_seed(seed)
    return ReviewState(artifact=seed.initial_artifact, last_at=seed.started_at,
                       budget=BudgetState())


def reserve(seed: ReviewSeed, state: ReviewState, invocation: Invocation) -> ReviewState:
    require(not state.terminal_reasons and bool(eligibility_reasons(seed, state)),
            ReviewReason.REVIEW_LIMIT)
    require(state.pending is None, ReviewReason.PENDING)
    require(invocation.invocation_id not in state.invocation_ids, ReviewReason.REPLAY)
    require(invocation.binding == state.artifact.binding(), ReviewReason.BINDING_MISMATCH)
    require(within_window(seed, state, invocation.started_at), ReviewReason.TIME_WINDOW)
    limits, budget, held = seed.policy, state.budget, invocation.reservation
    elapsed = (invocation.started_at - seed.started_at).total_seconds()
    require(held.seconds <= limits.max_elapsed_seconds - elapsed, ReviewReason.TIME_WINDOW)
    require(budget.invocations < limits.max_invocations, ReviewReason.INVOCATION_LIMIT)
    require(budget.tokens_spent + held.tokens <= limits.max_tokens, ReviewReason.TOKEN_LIMIT)
    require(budget.cost_microusd_spent + held.cost_microusd <= limits.max_cost_microusd,
            ReviewReason.COST_LIMIT)
    repair = invocation.kind == "REPAIR"
    if repair:
        require(invocation.actor == seed.original_doer, ReviewReason.IDENTITY_UNAVAILABLE)
        require(needs_repair(state), ReviewReason.INVALID_REPAIR)
        require(budget.repairs < limits.max_implementation_repairs, ReviewReason.REPAIR_LIMIT)
    else:
        require(len(state.valid_completed_reports) < limits.max_review_passes,
                ReviewReason.REVIEW_LIMIT)
        require(invocation.actor in seed.reviewers, ReviewReason.IDENTITY_UNAVAILABLE)
        independent(seed, state.artifact, invocation.actor)
    budget = renewed(budget, invocations=budget.invocations + 1,
                     repairs=budget.repairs + int(repair), tokens_reserved=held.tokens,
                     cost_microusd_reserved=held.cost_microusd)
    return renewed(state, budget=budget, pending=invocation, last_at=invocation.started_at,
                   invocation_ids=(*state.invocation_ids, invocation.invocation_id))


def _account(state: ReviewState, invocation: Invocation, completion: Completion
             ) -> tuple[ReviewState, ReviewReason | None]:
    proof, held = completion.receipt, invocation.reservation
    execution_key = (content_digest((proof.actual_model.provider, proof.execution_id))
                     if proof is not None else None)
    reason = None
    if proof is None:
        reason = ReviewReason.RECEIPT_UNAVAILABLE
    elif (proof.invocation_id != invocation.invocation_id or proof.binding != invocation.binding
          or proof.actual_model != invocation.actor):
        reason = ReviewReason.RECEIPT_MISMATCH
    elif proof.cached or execution_key in state.execution_ids:
        reason = ReviewReason.REPLAY
    tokens = proof.usage.tokens if proof is not None else held.tokens
    cost = proof.usage.cost_microusd if proof is not None else held.cost_microusd
    if reason is not None:
        tokens, cost = max(tokens, held.tokens), max(cost, held.cost_microusd)
    if tokens > held.tokens or cost > held.cost_microusd:
        reason = ReviewReason.BUDGET_OVERRUN
    budget = renewed(state.budget, tokens_spent=state.budget.tokens_spent + tokens,
                     cost_microusd_spent=state.budget.cost_microusd_spent + cost,
                     tokens_reserved=0, cost_microusd_reserved=0)
    execution_ids = state.execution_ids
    if execution_key is not None and execution_key not in execution_ids:
        execution_ids = (*execution_ids, execution_key)
    return renewed(state, budget=budget, pending=None, execution_ids=execution_ids,
                   last_at=max(state.last_at, completion.finished_at)), reason


def _failure(seed: ReviewSeed, state: ReviewState, reason: ReviewReason) -> ReviewState:
    counts = {item.reason: item.occurrences for item in state.failures}
    counts[reason] = counts.get(reason, 0) + 1
    terminal = list(state.terminal_reasons)
    if reason in {ReviewReason.TIME_WINDOW, ReviewReason.RECEIPT_MISMATCH, ReviewReason.REPLAY,
                  ReviewReason.BUDGET_OVERRUN, ReviewReason.NO_PROGRESS, ReviewReason.CONTRACT_GAP,
                  ReviewReason.ARTIFACT_DRIFT, ReviewReason.AUTHORIZATION_REVOKED,
                  ReviewReason.EXECUTION_INVALID, ReviewReason.OUTPUT_LIMIT}:
        terminal.append(reason)
    if counts[reason] >= seed.policy.same_unresolved_failure_limit:
        terminal.append(ReviewReason.REPEATED_FAILURE)
    return renewed(state, failures=tuple(FailureCount(reason=key, occurrences=counts[key])
                                        for key in sorted(counts)),
                   terminal_reasons=tuple(dict.fromkeys(terminal)))


def _capacity(seed: ReviewSeed, state: ReviewState) -> ReviewState:
    if not eligibility_reasons(seed, state):
        return state
    reasons = list(state.terminal_reasons)
    for exhausted, reason in (
        (needs_repair(state) and state.budget.repairs >= seed.policy.max_implementation_repairs,
         ReviewReason.REPAIR_LIMIT),
        (len(state.valid_completed_reports) >= seed.policy.max_review_passes,
         ReviewReason.REVIEW_LIMIT),
        (state.budget.invocations >= seed.policy.max_invocations, ReviewReason.INVOCATION_LIMIT),
        (state.budget.tokens_spent >= seed.policy.max_tokens, ReviewReason.TOKEN_LIMIT),
        (state.budget.cost_microusd_spent >= seed.policy.max_cost_microusd,
         ReviewReason.COST_LIMIT),
    ):
        if exhausted:
            reasons.append(reason)
    return renewed(state, terminal_reasons=tuple(dict.fromkeys(reasons)))


def complete(seed: ReviewSeed, state: ReviewState, completion: Completion) -> ReviewState:
    invocation = state.pending
    require(invocation is not None, ReviewReason.PENDING)
    assert invocation is not None
    require(completion.invocation_id == invocation.invocation_id, ReviewReason.BINDING_MISMATCH)
    accounted, reason = _account(state, invocation, completion)
    elapsed = (completion.finished_at - invocation.started_at).total_seconds()
    if (not within_window(seed, state, completion.finished_at)
            or not 0 <= elapsed < invocation.reservation.seconds):
        reason = ReviewReason.TIME_WINDOW
    if completion.failure_reason is not None:
        # Normalize the runtime stop only after retaining actual receipt usage.
        # It can remove eligibility, never grant it or manufacture a model review.
        return _capacity(seed, _failure(seed, accounted, ReviewReason(completion.failure_reason)))
    if reason is not None:
        return _capacity(seed, _failure(seed, accounted, reason))
    rejected = accounted
    try:
        if completion.output_json is None:
            raise ReviewViolation(ReviewReason.MALFORMED_REPORT)
        if invocation.kind == "REVIEW":
            report = ReviewReport.model_validate_json(completion.output_json)
            validate_report_binding(seed, accounted, invocation, report)
            rejected = retain_stops(seed, accounted, report)
            validate_report(seed, accounted, report)
            findings, terminal = reconcile_findings(seed, accounted, report)
            updated = renewed(accounted, findings=findings, terminal_reasons=terminal, failures=(),
                              valid_completed_reports=(*accounted.valid_completed_reports, report))
        else:
            repair = RepairReport.model_validate_json(completion.output_json)
            resolutions = reconcile_repair(seed, accounted, invocation, repair)
            updated = renewed(accounted, artifact=repair.artifact, failures=(),
                              resolutions=(*accounted.resolutions, *resolutions))
    except ReviewViolation as exc:
        updated = _failure(seed, rejected, exc.reason)
    except (ValidationError, ValueError):
        updated = _failure(seed, rejected, ReviewReason.MALFORMED_REPORT)
    return _capacity(seed, updated)