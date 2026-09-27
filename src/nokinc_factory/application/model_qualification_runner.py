"""Bounded, redacted live-model qualification orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from time import monotonic

from nokinc_factory.adapters.model_providers import ModelProviderError
from nokinc_factory.adapters.model_qualification import ModelUnavailable
from nokinc_factory.ports.model import ModelPort, ModelRequest, ModelStatus


class QualificationBudgetExceeded(RuntimeError):
    """A qualification call would exceed its declared hard budget."""


class QualificationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED_BUDGET = "BLOCKED_BUDGET"


@dataclass
class QualificationBudget:
    max_calls: int
    max_estimated_cost_usd: Decimal
    estimated_cost_per_call_usd: Decimal
    calls_used: int = 0
    estimated_cost_usd: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.max_calls <= 0:
            raise ValueError("qualification max_calls must be positive")
        if self.max_estimated_cost_usd <= 0:
            raise ValueError("qualification max_estimated_cost_usd must be positive")
        if self.estimated_cost_per_call_usd <= 0:
            raise ValueError("qualification estimated_cost_per_call_usd must be positive")

    def reserve(self) -> None:
        next_cost = self.estimated_cost_usd + self.estimated_cost_per_call_usd
        if self.calls_used >= self.max_calls or next_cost > self.max_estimated_cost_usd:
            raise QualificationBudgetExceeded("qualification budget exhausted")
        self.calls_used += 1
        self.estimated_cost_usd = next_cost


@dataclass(frozen=True)
class QualificationCase:
    provider: str
    model: str
    family: str
    port: ModelPort


@dataclass(frozen=True)
class QualificationEvidence:
    provider: str
    model: str
    family: str
    status: QualificationStatus
    error_code: str | None = None
    output_digest: str = ""
    output: str = ""
    provider_execution_id: str | None = None
    latency_ms: int = 0


@dataclass(frozen=True)
class QualificationReport:
    evidence: tuple[QualificationEvidence, ...]
    budget: QualificationBudget

    @property
    def passed(self) -> bool:
        return bool(self.evidence) and all(
            item.status is QualificationStatus.PASSED for item in self.evidence
        )

    def as_dict(self) -> dict[str, object]:
        """Return durable evidence without model output or provider errors."""
        return {
            "passed": self.passed,
            "budget": {
                "max_calls": self.budget.max_calls,
                "max_estimated_cost_usd": str(self.budget.max_estimated_cost_usd),
                "estimated_cost_per_call_usd": str(self.budget.estimated_cost_per_call_usd),
                "calls_used": self.budget.calls_used,
                "estimated_cost_usd": str(self.budget.estimated_cost_usd),
            },
            "evidence": [
                {
                    "provider": item.provider,
                    "model": item.model,
                    "family": item.family,
                    "status": item.status.value,
                    "error_code": item.error_code,
                    "output_digest": item.output_digest,
                    "provider_execution_id": item.provider_execution_id,
                    "latency_ms": item.latency_ms,
                }
                for item in self.evidence
            ],
        }


class QualificationRunner:
    def __init__(self, budget: QualificationBudget) -> None:
        self._budget = budget

    def run(
        self,
        cases: tuple[QualificationCase, ...],
        request: ModelRequest,
    ) -> QualificationReport:
        evidence: list[QualificationEvidence] = []
        for case in cases:
            try:
                self._budget.reserve()
            except QualificationBudgetExceeded:
                evidence.append(QualificationEvidence(
                    provider=case.provider,
                    model=case.model,
                    family=case.family,
                    status=QualificationStatus.BLOCKED_BUDGET,
                    error_code="QUALIFICATION_BUDGET_EXHAUSTED",
                ))
                continue

            started = monotonic()
            try:
                response = case.port.complete(request)
            except ModelUnavailable:
                evidence.append(self._failure(case, "MODEL_UNAVAILABLE", started))
                continue
            except ModelProviderError:
                evidence.append(self._failure(case, "PROVIDER_ERROR", started))
                continue
            except Exception:
                evidence.append(self._failure(case, "UNEXPECTED_PROVIDER_ERROR", started))
                continue

            latency_ms = max(0, int((monotonic() - started) * 1000))
            if response.status is not ModelStatus.COMPLETED:
                evidence.append(self._failure(case, "INVALID_RESPONSE_STATUS", started))
                continue
            if response.model != case.model:
                evidence.append(self._failure(case, "MODEL_ID_MISMATCH", started))
                continue
            if response.family != case.family:
                evidence.append(self._failure(case, "MODEL_FAMILY_MISMATCH", started))
                continue
            if not response.output.strip():
                evidence.append(self._failure(case, "EMPTY_OUTPUT", started))
                continue

            evidence.append(QualificationEvidence(
                provider=case.provider,
                model=case.model,
                family=case.family,
                status=QualificationStatus.PASSED,
                output_digest="sha256:" + sha256(response.output.encode("utf-8")).hexdigest(),
                provider_execution_id=response.provider_execution_id,
                latency_ms=latency_ms,
            ))
        return QualificationReport(evidence=tuple(evidence), budget=self._budget)

    @staticmethod
    def _failure(
        case: QualificationCase,
        error_code: str,
        started: float,
    ) -> QualificationEvidence:
        return QualificationEvidence(
            provider=case.provider,
            model=case.model,
            family=case.family,
            status=QualificationStatus.FAILED,
            error_code=error_code,
            latency_ms=max(0, int((monotonic() - started) * 1000)),
        )


__all__ = [
    "QualificationBudget",
    "QualificationBudgetExceeded",
    "QualificationCase",
    "QualificationEvidence",
    "QualificationReport",
    "QualificationRunner",
    "QualificationStatus",
]