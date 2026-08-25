"""WorkItemPort. Spec Part 11.

The ALM owns the business lifecycle. This port is how the factory observes and
advances it -- never how it owns it.

Rule: nothing provider-specific crosses this boundary. Adapters map our story
schema onto ADO fields, Jira custom fields or GitHub labels. If a vendor's field
model leaks into the core, switching providers becomes a rewrite.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from nokinc_factory.domain.states import Transition, WorkItemState
from nokinc_factory.domain.story import BusinessReady, SolutionReady


class ApprovalCapabilities(BaseModel):
    """What an adapter can actually prove about an approval.

    A GitHub label applied by the PR author proves nothing about separation of
    duties. Policy must be able to refuse T2 work on such an adapter, which
    requires the adapter to declare its limits honestly.
    """

    verified_identity: bool
    separation_of_duties: bool
    immutable_audit: bool
    time_bound: bool = False


class WorkItemRef(BaseModel):
    id: str
    url: str
    state: WorkItemState


@runtime_checkable
class WorkItemPort(Protocol):
    """Provider-neutral work item operations."""

    def capabilities(self) -> ApprovalCapabilities:
        """Declared, not inferred. Missing capability is an explicit degraded mode."""
        ...

    def create_story(self, story: BusinessReady) -> WorkItemRef: ...

    def create_design(self, design: SolutionReady) -> WorkItemRef: ...

    def get_state(self, work_item_id: str) -> WorkItemState:
        """Current state as the ALM reports it -- the authoritative position."""
        ...

    def apply(self, transition: Transition) -> WorkItemRef:
        """Advance the work item.

        Implementations MUST call `transition.validate_against(current_state)`
        before mutating anything. A protected transition without bound approval
        evidence is an authorization failure, not a sync problem.
        """
        ...

    def comment(self, work_item_id: str, body: str) -> None: ...
