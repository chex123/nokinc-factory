"""Provider-neutral workflow approval port. See Spec Part 1 and Part 11."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from nokinc_factory.domain.approval import (
    ApprovalConfig,
    ApprovalDispatch,
    ApprovalEvidence,
    ApprovalIntent,
)


@runtime_checkable
class ApprovalPort(Protocol):
    """Dispatch a bound review and inspect provider-owned approval records.

    Dispatch is not approval. Consumers may authorize a lifecycle transition
    only after ``inspect`` returns verified evidence and policy accepts it.
    """

    def config(self) -> ApprovalConfig: ...

    def dispatch(self, intent: ApprovalIntent) -> ApprovalDispatch: ...

    def inspect(
        self,
        intent: ApprovalIntent,
        dispatch: ApprovalDispatch,
    ) -> ApprovalEvidence: ...
