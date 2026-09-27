"""Durable doer/reviewer coordination, not approval, provider qualification or deployment.

Each call is reserved and committed before dispatch. Only the CAS winner may
execute; a persisted pending invocation is never resubmitted after a crash.
Provider-specific reconciliation settles those cases separately. Unknown commit
errors propagate without executing again. Shared caps and replay claims belong
to the durable store, not a locally resettable loop counter.

The trusted composition root injects a live artifact observer and an execution
authorizer. Neither may rely on model declarations. The executor must enforce
hard time/resource limits; this synchronous coordinator cannot interrupt an
arbitrary unsafe Python adapter and does not pretend to be its sandbox.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

from nokinc_factory.application.review_result import normalize_result
from nokinc_factory.domain.review import ArtifactRef, Reservation, ReviewScope
from nokinc_factory.domain.review_base import ReviewModel, utc
from nokinc_factory.domain.review_session import (
    ExecutionStop,
    Invocation,
    InvocationReceipt,
    ReviewDecision,
    ReviewSession,
)
from nokinc_factory.policy.review import (
    complete_invocation,
    evaluate,
    reserve_invocation,
    validate_session,
)
from nokinc_factory.ports.review import ReviewSessionStore
from nokinc_factory.ports.review_execution import (
    ExecutionRequest,
    ExecutionResult,
    ExecutorUnavailable,
    ReviewExecutor,
)

RunStatus = Literal["FINISHED", "ADVANCED", "AWAITING_SETTLEMENT", "CONTENDED", "PAUSED"]


class OrchestrationResult(ReviewModel):
    status: RunStatus
    decision: ReviewDecision


class ReviewOrchestrator:
    def __init__(
        self, *, store: ReviewSessionStore, executor: ReviewExecutor, scope: ReviewScope,
        parent_budget_id: str, reservation: Reservation, clock: Callable[[], datetime],
        observe: Callable[[ReviewScope, str], ArtifactRef],
        authorize: Callable[[Invocation], None],
        invocation_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._store, self._executor = store, executor
        self._scope = ReviewScope.model_validate(scope)
        if not parent_budget_id.strip():
            raise ValueError("Approved parent budget is required")
        self._parent = parent_budget_id
        self._reservation = Reservation.model_validate(reservation)
        self._clock, self._observe, self._authorize = clock, observe, authorize
        self._invocation_id = invocation_id

    def _current(self) -> ReviewSession:
        current = self._store.load_current(self._scope)
        if current is None:
            raise ValueError("Review unit must be registered and created before execution")
        current = validate_session(current)
        if current.seed.scope != self._scope:
            raise ValueError("Store returned a foreign review unit")
        return current

    def _decision(self, current: ReviewSession) -> ReviewDecision:
        observed = self._observe(self._scope, current.artifact.artifact_key)
        return evaluate(current, current_artifact=observed, now=utc(self._clock()))

    def _dispatch_stop(self, pending: ReviewSession) -> ExecutionStop | None:
        invocation = pending.state.pending
        assert invocation is not None
        try:
            self._authorize(invocation)
        except PermissionError:
            return "AUTHORIZATION_REVOKED"
        observed = self._observe(self._scope, pending.artifact.artifact_key)
        now = utc(self._clock())
        if observed != pending.artifact:
            return "ARTIFACT_DRIFT"
        if not invocation.started_at <= now < (
            invocation.started_at + timedelta(seconds=invocation.reservation.seconds)
        ) or now >= pending.seed.started_at + timedelta(
            seconds=pending.seed.policy.max_elapsed_seconds
        ):
            return "TIME_WINDOW"
        return None

    def step(self) -> OrchestrationResult:
        current = self._current()
        decision = self._decision(current)
        if current.state.pending is not None:
            return OrchestrationResult(status="AWAITING_SETTLEMENT", decision=decision)
        if decision.status in ("ELIGIBLE", "BLOCKED", "ESCALATE"):
            return OrchestrationResult(status="FINISHED", decision=decision)
        repair = decision.status == "NEEDS_REPAIR"
        actor = current.seed.original_doer if repair else current.seed.reviewers[0]
        pending = reserve_invocation(
            current, invocation_id=self._invocation_id(), kind="REPAIR" if repair else "REVIEW",
            actor=actor, reservation=self._reservation, now=utc(self._clock()),
        )
        invocation = pending.state.pending
        assert invocation is not None
        self._authorize(invocation)
        if not self._store.compare_and_reserve(
            scope=self._scope, expected_digest=current.content_digest, updated=pending,
            parent_budget_id=self._parent,
        ):
            return OrchestrationResult(status="CONTENDED", decision=self._decision(self._current()))
        stop = self._dispatch_stop(pending)
        if stop is not None:
            return self._settle(pending, None, None, stop)
        request = ExecutionRequest(
            invocation=invocation, contract=pending.seed.contract, artifact=pending.artifact,
            findings=pending.open_findings, resolutions=pending.resolutions,
        )
        try:
            result = self._executor.execute(request)
        except ExecutorUnavailable:
            result = ExecutionResult(receipt=None, output=None)
        receipt, output, stop = normalize_result(result)
        return self._settle(pending, receipt, output, stop)

    def _settle(self, pending: ReviewSession, receipt: InvocationReceipt | None,
                output: str | None, stop: ExecutionStop | None) -> OrchestrationResult:
        invocation = pending.state.pending
        assert invocation is not None
        finished = complete_invocation(
            pending, invocation_id=invocation.invocation_id, receipt=receipt,
            output=output, now=utc(self._clock()), failure_reason=stop,
        )
        accepted = self._store.compare_and_reserve(
            scope=self._scope, expected_digest=pending.content_digest, updated=finished,
            parent_budget_id=self._parent,
        )
        actual = self._current()
        if not accepted and actual != finished:
            return OrchestrationResult(status="CONTENDED", decision=self._decision(actual))
        decision = self._decision(actual)
        status: RunStatus = (
            "FINISHED" if decision.status in ("ELIGIBLE", "BLOCKED", "ESCALATE") else "ADVANCED"
        )
        return OrchestrationResult(status=status, decision=decision)

    def run(self, *, max_steps: int = 8) -> OrchestrationResult:
        """Bounded cooperative batch; later calls resume stored counters, not reset them."""
        if type(max_steps) is not int or not 1 <= max_steps <= 64:
            raise ValueError("max_steps must be an integer from 1 to 64")
        for _ in range(max_steps):
            result = self.step()
            if result.status != "ADVANCED":
                return result
        return OrchestrationResult(status="PAUSED", decision=result.decision)