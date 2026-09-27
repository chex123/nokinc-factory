"""Trusted execution seam for the bounded review coordinator (ARP-1).

Adapters enforce invocation deadlines, tool/egress/data policy, isolated artifact
writes, independent retrieval and actual provider receipt identity/usage. They
must not expose store credentials or treat model-generated metadata as proof.
One execute call performs at most one provider invocation; hidden retries are
forbidden. Result receipt authority comes from the adapter, never the LLM body.
"""

from dataclasses import dataclass
from typing import Protocol

from nokinc_factory.domain.review import ArtifactRef, QualityContract
from nokinc_factory.domain.review_base import ReviewModel
from nokinc_factory.domain.review_report import RepairReport, ReviewReport
from nokinc_factory.domain.review_session import (
    FindingRecord,
    Invocation,
    InvocationReceipt,
    ResolutionRecord,
)


class ExecutionRequest(ReviewModel):
    """Only task facts and factual reconciliation, not private producer reasoning."""

    invocation: Invocation
    contract: QualityContract
    artifact: ArtifactRef
    findings: tuple[FindingRecord, ...]
    resolutions: tuple[ResolutionRecord, ...]


@dataclass(frozen=True)
class ExecutionResult:
    receipt: InvocationReceipt | None
    output: ReviewReport | RepairReport | str | None


class ExecutorUnavailable(RuntimeError):
    """Expected provider outage; no trusted receipt is available, charge the hold."""


class ReviewExecutor(Protocol):
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute one bounded invocation using independently established context."""
        ...