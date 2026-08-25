"""Work item lifecycle. The ALM owns this state; the factory observes and reacts.

Transitions are compare-and-swap: `expected_current` makes duplicate webhooks
idempotent. See Spec Part 1.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class WorkItemState(StrEnum):
    NEW = "NEW"
    REFINING = "REFINING"
    BUSINESS_READY = "BUSINESS_READY"  # Gate 1 pending
    DESIGNING = "DESIGNING"
    SOLUTION_READY = "SOLUTION_READY"  # Gate 2 pending
    IMPLEMENTING = "IMPLEMENTING"
    DEV_VERIFYING = "DEV_VERIFYING"
    DEV_VERIFIED = "DEV_VERIFIED"  # Gate 3 pending
    PREPROD_VERIFYING = "PREPROD_VERIFYING"
    PREPROD_VERIFIED = "PREPROD_VERIFIED"  # Gate 4 pending
    RELEASING = "RELEASING"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


#: States whose exit requires a human approval carrying verified identity.
HUMAN_GATES: frozenset[WorkItemState] = frozenset(
    {
        WorkItemState.BUSINESS_READY,
        WorkItemState.SOLUTION_READY,
        WorkItemState.DEV_VERIFIED,  # configurable; see gate_3 policy
        WorkItemState.PREPROD_VERIFIED,
    }
)

_ALLOWED: dict[WorkItemState, frozenset[WorkItemState]] = {
    WorkItemState.NEW: frozenset({WorkItemState.REFINING, WorkItemState.REJECTED}),
    WorkItemState.REFINING: frozenset({WorkItemState.BUSINESS_READY, WorkItemState.REJECTED}),
    WorkItemState.BUSINESS_READY: frozenset(
        {WorkItemState.DESIGNING, WorkItemState.REFINING, WorkItemState.REJECTED}
    ),
    WorkItemState.DESIGNING: frozenset({WorkItemState.SOLUTION_READY, WorkItemState.BLOCKED}),
    WorkItemState.SOLUTION_READY: frozenset(
        {WorkItemState.IMPLEMENTING, WorkItemState.DESIGNING, WorkItemState.REJECTED}
    ),
    WorkItemState.IMPLEMENTING: frozenset(
        {WorkItemState.DEV_VERIFYING, WorkItemState.BLOCKED, WorkItemState.SOLUTION_READY}
    ),
    WorkItemState.DEV_VERIFYING: frozenset(
        {WorkItemState.DEV_VERIFIED, WorkItemState.IMPLEMENTING}
    ),
    WorkItemState.DEV_VERIFIED: frozenset(
        {WorkItemState.PREPROD_VERIFYING, WorkItemState.IMPLEMENTING}
    ),
    WorkItemState.PREPROD_VERIFYING: frozenset(
        {WorkItemState.PREPROD_VERIFIED, WorkItemState.IMPLEMENTING}
    ),
    WorkItemState.PREPROD_VERIFIED: frozenset(
        {WorkItemState.RELEASING, WorkItemState.IMPLEMENTING}
    ),
    WorkItemState.RELEASING: frozenset({WorkItemState.DONE, WorkItemState.ROLLED_BACK}),
    WorkItemState.BLOCKED: frozenset(
        {
            WorkItemState.REFINING,
            WorkItemState.DESIGNING,
            WorkItemState.IMPLEMENTING,
            WorkItemState.REJECTED,
        }
    ),
    WorkItemState.DONE: frozenset(),
    WorkItemState.REJECTED: frozenset(),
    WorkItemState.ROLLED_BACK: frozenset({WorkItemState.IMPLEMENTING}),
}


class IllegalTransition(Exception):
    """The transition is not permitted by the lifecycle."""


class StaleTransition(Exception):
    """CAS failure: the work item was not in the expected state.

    Normal and expected -- SaaS webhooks are duplicated and retried. Drop, do not replay.
    """


class Transition(BaseModel):
    """One lifecycle transition. Persist in the inbox before processing."""

    work_item_id: str
    workflow_run_id: str
    transition_id: str
    expected_current: WorkItemState
    target: WorkItemState
    event_id: str = Field(description="Provider event id; the inbox dedupe key.")
    actor: str
    approval_id: str | None = Field(
        default=None, description="Required when leaving a state in HUMAN_GATES."
    )
    occurred_at: datetime

    def validate_against(self, actual: WorkItemState) -> None:
        """Raise unless this transition may be applied to `actual`.

        Order matters: CAS first (cheap, and the common duplicate-webhook case),
        then legality, then approval presence.
        """
        if actual is not self.expected_current:
            raise StaleTransition(
                f"{self.work_item_id}: expected {self.expected_current}, found {actual}"
            )
        if self.target not in _ALLOWED[actual]:
            raise IllegalTransition(f"{actual} -> {self.target} is not permitted")
        if actual in HUMAN_GATES and self.approval_id is None:
            raise IllegalTransition(f"leaving {actual} requires a bound human approval")


def allowed_from(state: WorkItemState) -> frozenset[WorkItemState]:
    return _ALLOWED[state]
