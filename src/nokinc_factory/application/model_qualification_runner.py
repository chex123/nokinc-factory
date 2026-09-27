"""Bounded, redacted live-model qualification orchestration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from time import monotonic

from nokinc_factory.adapters.model_providers import ModelProviderError
from nokinc_factory.adapters.model_qualification import ModelUnavailable
from nokinc_factory.application.model_pricing import ModelCostEstimate, estimate_model_cost
from nokinc_factory.ports.model import ModelPort, ModelRequest, ModelStatus, ModelUsage


class QualificationCallLimitReached(RuntimeError):
    """A qualification call would exceed its explicit count limit."""


class QualificationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED_CALL_LIMIT = "BLOCKED_CALL_LIMIT"


@dataclass
class QualificationCallLimit:
    max_calls: int
    calls_used: int = 0

    def __post_init__(self) -> None:
        if self.max_calls <= 0:
            raise ValueError("qualification call limit must be positive")

    def reserve(self) -> None:
        if self.calls_used >= self.max_calls:
            raise QualificationCallLimitReached("qualification call limit reached")
        self.calls_used += 1


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
    usage: ModelUsage | None = None
    list_price_cost: ModelCostEstimate | None = None


@dataclass(frozen=True)
class QualificationReport:
    evidence: tuple[QualificationEvidence, ...]
    call_limit: QualificationCallLimit

    @property
    def passed(self) -> bool:
        return bool(self.evidence) and all(
            item.status is QualificationStatus.PASSED for item in self.evidence
        )

    def as_dict(self) -> dict[str, object]:
        """Return durable evidence without model output or provider errors."""
        return {
            "passed": self.passed,
            "call_limit": {
                "max_calls": self.call_limit.max_calls,
                "calls_used": self.call_limit.calls_used,
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
                    "usage": (
                        item.usage.model_dump(mode="json") if item.usage is not None else None
                    ),
                    "list_price_cost": (
                        item.list_price_cost.model_dump(mode="json")
                        if item.list_price_cost is not None
                        else None
                    ),
                }
                for item in self.evidence
            ],
        }


class QualificationRunner:
    def __init__(
        self,
        call_limit: QualificationCallLimit,
        *,
        pricing_region: str = "us-east-1",
    ) -> None:
        self._call_limit = call_limit
        self._pricing_region = pricing_region

    def run(
        self,
        cases: tuple[QualificationCase, ...],
        request: ModelRequest,
    ) -> QualificationReport:
        evidence: list[QualificationEvidence] = []
        for case in cases:
            try:
                self._call_limit.reserve()
            except QualificationCallLimitReached:
                evidence.append(QualificationEvidence(
                    provider=case.provider,
                    model=case.model,
                    family=case.family,
                    status=QualificationStatus.BLOCKED_CALL_LIMIT,
                    error_code="QUALIFICATION_CALL_LIMIT_REACHED",
                ))
                continue

            started = monotonic()
            try:
                response = case.port.complete(request)
            except ModelUnavailable:
                evidence.append(self._failure(case, "MODEL_UNAVAILABLE", started))
                continue
            except ModelProviderError as error:
                diagnostic_code = error.diagnostic_code
                if re.fullmatch(r"[A-Z][A-Z0-9_]{0,47}", diagnostic_code) is None:
                    diagnostic_code = "PROVIDER_ERROR"
                evidence.append(self._failure(case, diagnostic_code, started))
                continue
            except Exception:
                evidence.append(self._failure(case, "UNEXPECTED_PROVIDER_ERROR", started))
                continue

            latency_ms = max(0, int((monotonic() - started) * 1000))
            cost = (
                estimate_model_cost(
                    provider=case.provider,
                    model=case.model,
                    usage=response.usage,
                    region=self._pricing_region,
                )
                if response.usage is not None and response.model == case.model
                else None
            )
            if response.status is not ModelStatus.COMPLETED:
                evidence.append(self._failure(
                    case, "INVALID_RESPONSE_STATUS", started,
                    usage=response.usage, list_price_cost=cost,
                ))
                continue
            if response.model != case.model:
                evidence.append(self._failure(
                    case, "MODEL_ID_MISMATCH", started, usage=response.usage,
                ))
                continue
            if response.family != case.family:
                evidence.append(self._failure(
                    case, "MODEL_FAMILY_MISMATCH", started,
                    usage=response.usage, list_price_cost=cost,
                ))
                continue
            if not response.output.strip():
                evidence.append(self._failure(
                    case, "EMPTY_OUTPUT", started,
                    usage=response.usage, list_price_cost=cost,
                ))
                continue

            evidence.append(QualificationEvidence(
                provider=case.provider,
                model=case.model,
                family=case.family,
                status=QualificationStatus.PASSED,
                output_digest="sha256:" + sha256(response.output.encode("utf-8")).hexdigest(),
                provider_execution_id=response.provider_execution_id,
                latency_ms=latency_ms,
                usage=response.usage,
                list_price_cost=cost,
            ))
        return QualificationReport(evidence=tuple(evidence), call_limit=self._call_limit)

    @staticmethod
    def _failure(
        case: QualificationCase,
        error_code: str,
        started: float,
        *,
        usage: ModelUsage | None = None,
        list_price_cost: ModelCostEstimate | None = None,
    ) -> QualificationEvidence:
        return QualificationEvidence(
            provider=case.provider,
            model=case.model,
            family=case.family,
            status=QualificationStatus.FAILED,
            error_code=error_code,
            latency_ms=max(0, int((monotonic() - started) * 1000)),
            usage=usage,
            list_price_cost=list_price_cost,
        )


__all__ = [
    "QualificationCallLimit",
    "QualificationCallLimitReached",
    "QualificationCase",
    "QualificationEvidence",
    "QualificationReport",
    "QualificationRunner",
    "QualificationStatus",
]