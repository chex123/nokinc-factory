"""FROZEN CONTRACT. Spec Part 1 -- CAS transitions and gate approval."""

from datetime import UTC, datetime

import pytest

from nokinc_factory.domain.states import (
    IllegalTransition,
    StaleTransition,
    Transition,
    WorkItemState,
)


def _t(expected: WorkItemState, target: WorkItemState, approval: str | None = None) -> Transition:
    return Transition(
        work_item_id="4711",
        workflow_run_id="wf_1",
        transition_id="t_1",
        expected_current=expected,
        target=target,
        event_id="evt_1",
        actor="user:123",
        approval_id=approval,
        occurred_at=datetime.now(UTC),
    )


def test_duplicate_webhook_is_dropped_not_replayed() -> None:
    """SaaS webhooks are duplicated and retried. CAS makes that idempotent."""
    with pytest.raises(StaleTransition):
        _t(WorkItemState.REFINING, WorkItemState.BUSINESS_READY).validate_against(
            WorkItemState.BUSINESS_READY
        )


def test_leaving_a_human_gate_requires_bound_approval() -> None:
    """ALM position never confers execution authorization."""
    with pytest.raises(IllegalTransition, match="approval"):
        _t(WorkItemState.SOLUTION_READY, WorkItemState.IMPLEMENTING).validate_against(
            WorkItemState.SOLUTION_READY
        )


def test_lifecycle_jump_is_rejected() -> None:
    """Dragging a ticket to RELEASING must not release."""
    with pytest.raises(IllegalTransition):
        _t(WorkItemState.SOLUTION_READY, WorkItemState.RELEASING, "appr_1").validate_against(
            WorkItemState.SOLUTION_READY
        )
