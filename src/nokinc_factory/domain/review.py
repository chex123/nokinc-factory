"""ARP-1 pinned contract, artifact and execution inputs (spec Parts 7 and 11).

The trusted orchestrator establishes scope, approved policy, actual byte digests,
model qualifications, canonical model aliases, capability pins and producer
lineage before starting. These declarations do not authenticate anybody or grant
tools. Contract/model/risk amendments are intentionally not local session edits.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from nokinc_factory.domain.review_base import (
    Count,
    Digest,
    Identifier,
    Limit,
    ReviewModel,
    Text,
    distinct,
)


class ReviewScope(ReviewModel):
    tenant_id: Identifier
    work_item_id: Identifier
    session_id: Identifier


class PinnedReference(ReviewModel):
    name: Identifier
    digest: Digest


class QualityCriterion(ReviewModel):
    criterion_id: Identifier
    predicate: Text
    mandatory: StrictBool
    required_evidence: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct_evidence(self) -> Self:
        distinct(self.required_evidence)
        return self


class QualityContract(ReviewModel):
    tenant_id: Identifier
    work_item_id: Identifier
    contract_version: Identifier
    criteria: tuple[QualityCriterion, ...] = Field(min_length=1)
    artifact_scope: tuple[Identifier, ...] = Field(min_length=1)
    scope_in: tuple[Text, ...] = Field(min_length=1)
    scope_out: tuple[Text, ...] = Field(min_length=1)
    design_refs: tuple[PinnedReference, ...]
    design_not_applicable_reason: Text | None
    risk_tier: Literal["T0", "T1", "T2"]

    @model_validator(mode="after")
    def _grounding(self) -> Self:
        distinct(item.criterion_id for item in self.criteria)
        distinct(self.artifact_scope)
        distinct(item.name for item in self.design_refs)
        if not any(item.mandatory for item in self.criteria):
            raise ValueError("A contract needs a mandatory observable predicate")
        if bool(self.design_refs) == (self.design_not_applicable_reason is not None):
            raise ValueError("Pin design references or explicitly justify non-applicability")
        return self


class ReviewPolicy(ReviewModel):
    """Finite local ceilings; a parent store must separately reserve aggregate usage.

    Integer micro-USD avoids float rounding, NaN and infinity. No execution cap
    has an unlimited default. Organization authority for these pins is external.
    """

    policy_version: Identifier
    min_review_passes: Annotated[int, Field(strict=True, ge=2)] = 2
    max_review_passes: Annotated[int, Field(strict=True, ge=2)] = 3
    max_implementation_repairs: Annotated[int, Field(strict=True, ge=0, le=3)] = 3
    same_unresolved_failure_limit: Annotated[int, Field(strict=True, ge=1, le=2)] = 2
    max_invocations: Limit
    max_tokens: Limit
    max_cost_microusd: Limit
    max_elapsed_seconds: Limit
    require_different_family: StrictBool = False

    @model_validator(mode="after")
    def _review_range(self) -> Self:
        if self.max_review_passes < self.min_review_passes:
            raise ValueError("max_review_passes must cover the minimum")
        return self


class ModelIdentity(ReviewModel):
    """Exact, externally qualified identity, not proof of caller authentication.

    canonical_model_id comes from qualification, never endpoint/provider-name
    inference. Null qualification/family and empty capabilities mean unavailable.
    Even two different declared strings cannot prove real model independence.
    """

    provider: Identifier
    model: Identifier
    canonical_model_id: Identifier
    family: Identifier | None
    qualification_ref: Digest | None
    capability_refs: tuple[Digest, ...]
    role: Literal["PRODUCER", "REVIEWER"]

    @model_validator(mode="after")
    def _capabilities(self) -> Self:
        distinct(self.capability_refs)
        return self


class ArtifactRef(ReviewModel):
    """Manifest digest binds payload, all producers, upstream inputs and build refs.

    payload_digest is a trusted broker's observation of bytes. The core cannot
    inspect bytes or authenticate a build; its content_digest hashes this entire
    manifest so stale evidence and context cannot follow a changed payload.
    """

    scope: ReviewScope
    artifact_key: Identifier
    payload_digest: Digest
    producers: tuple[ModelIdentity, ...] = Field(min_length=1)
    contract_digest: Digest
    context_digest: Digest
    inputs: tuple[PinnedReference, ...]
    build_refs: tuple[PinnedReference, ...]

    @model_validator(mode="after")
    def _lineage(self) -> Self:
        distinct(item.canonical_model_id for item in self.producers)
        distinct(item.name for item in self.inputs)
        distinct(item.name for item in self.build_refs)
        if any(item.role != "PRODUCER" for item in self.producers):
            raise ValueError("Artifact contributors must be recorded as producers")
        return self

    def binding(self) -> ReviewBinding:
        return ReviewBinding(scope=self.scope, artifact_digest=self.content_digest,
                             contract_digest=self.contract_digest,
                             context_digest=self.context_digest)


class ReviewBinding(ReviewModel):
    scope: ReviewScope
    artifact_digest: Digest
    contract_digest: Digest
    context_digest: Digest


class Usage(ReviewModel):
    tokens: Count
    cost_microusd: Count


class Reservation(Usage):
    """Reserve before executing; seconds bounds the invocation as well as the unit."""

    tokens: Limit
    seconds: Limit