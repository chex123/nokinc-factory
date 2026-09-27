"""ARP-1 structured, immutable reports: raw prose is untrusted data, not instructions.

Fingerprints derive from stable criterion/behavior/cause keys, not severity,
wording, location, model or artifact version. The orchestrator must bind these
keys to its normalized failure registry; deterministic code cannot discover
semantic equivalence between arbitrary natural-language claims.
"""

from typing import Literal, Self

from pydantic import Field, StrictBool, ValidationInfo, field_validator, model_validator

from nokinc_factory.domain.review import ArtifactRef, ModelIdentity, ReviewBinding
from nokinc_factory.domain.review_base import (
    Digest,
    Identifier,
    ReviewModel,
    Text,
    content_digest,
    distinct,
)

Disposition = Literal["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]
Verdict = Literal["ACCEPT", "CHANGES_REQUIRED", "ESCALATE", "CONTEXT_GAP", "CONTRACT_GAP"]


class Evidence(ReviewModel):
    """Pinned observation, externally verified against the exact artifact manifest."""

    evidence_id: Identifier
    digest: Digest
    artifact_digest: Digest
    observation: Text


class CriterionDisposition(ReviewModel):
    criterion_id: Identifier
    status: Disposition
    evidence: tuple[Evidence, ...]

    @model_validator(mode="after")
    def _evidence_ids(self) -> Self:
        distinct(item.evidence_id for item in self.evidence)
        return self


class Finding(ReviewModel):
    criterion_id: Identifier
    behavior_id: Identifier
    cause_id: Identifier
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    kind: Literal["DEFECT", "EVIDENCE_GAP", "ADVISORY", "CONTRACT_GAP"]
    artifact_digest: Digest
    location: Text
    scenario: Text
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    consequence: Text
    correction: Text
    required_recheck: Text
    fingerprint: Digest = ""

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object, info: ValidationInfo) -> str:
        """Serialize the derived ID, but never trust a caller-provided replacement."""
        keys = tuple(info.data.get(name) for name in ("criterion_id", "behavior_id", "cause_id"))
        if not isinstance(value, str) or any(not isinstance(key, str) for key in keys):
            raise ValueError("A fingerprint needs validated criterion/behavior/cause keys")
        expected = content_digest(keys)
        if value and value != expected:
            raise ValueError("Finding fingerprint mismatch")
        return expected


class FindingResolution(ReviewModel):
    fingerprint: Digest
    report_digest: Digest
    disposition: Literal["FIXED", "REJECTED_WITH_EVIDENCE", "DEFERRED_ADVISORY"]
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    explanation: Text


class FindingRecheck(ReviewModel):
    fingerprint: Digest
    resolution_digest: Digest
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    check: Text


class ReviewReport(ReviewModel):
    invocation_id: Identifier
    binding: ReviewBinding
    reviewer: ModelIdentity
    complete: StrictBool
    criteria: tuple[CriterionDisposition, ...]
    findings: tuple[Finding, ...] = ()
    rechecks: tuple[FindingRecheck, ...] = ()
    verdict: Verdict
    raw_text: str = Field(default="", strict=True)


class RepairReport(ReviewModel):
    """Original producer's factual proposals, not authority to close a finding."""

    invocation_id: Identifier
    binding: ReviewBinding
    producer: ModelIdentity
    artifact: ArtifactRef
    resolutions: tuple[FindingResolution, ...] = Field(min_length=1)