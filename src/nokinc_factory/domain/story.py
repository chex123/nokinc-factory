"""Business Ready (Gate 1) and Solution Ready (Gate 2).

Gate 1 answers "do we understand what needs to be done?" -- no architecture.
Gate 2 answers "do we approve this solution?" -- architecture required.

Requiring architecture at Gate 1 is circular: the Architect runs after it.
Spec Part 5: open questions and blank declared facts are not ready. Optional
collections may remain empty; validation never invents facts or normalizes text.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


def _nonblank(value: str) -> str:
    """Reject missing content without changing the bytes of an established fact."""
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class RiskTier(StrEnum):
    T0 = "T0"  # matches a declared constrained change class
    T1 = "T1"  # everything not T0 or T2
    T2 = "T2"  # touches the auto-promotion list, or Architect designates


class DataClassification(StrEnum):
    NONE = "NONE"
    PII = "PII"
    PAYMENT = "PAYMENT"
    HEALTH = "HEALTH"
    CREDENTIAL = "CREDENTIAL"


class Scenario(BaseModel):
    """A Gherkin scenario. Executable acceptance criteria, not prose."""

    name: str
    gherkin: str
    is_failure_case: bool = False

    @field_validator("name", "gherkin")
    @classmethod
    def _content_required(cls, value: str) -> str:
        return _nonblank(value)


class TestDataNeed(BaseModel):
    description: str
    source: str = Field(description="synthetic | masked | fixture | generator name")
    volume: str
    sensitivity: DataClassification = DataClassification.NONE

    @field_validator("description", "source", "volume")
    @classmethod
    def _content_required(cls, value: str) -> str:
        return _nonblank(value)


class NonFunctionalTarget(BaseModel):
    """A number, or an explicit no-change. 'Fast' is not a target."""

    metric: str
    target: str
    unchanged: bool = False

    @field_validator("metric", "target")
    @classmethod
    def _content_required(cls, value: str) -> str:
        return _nonblank(value)


class BusinessReady(BaseModel):
    """Gate 1 payload. No architecture fields -- deliberately."""

    work_item_id: str
    problem_and_value: str
    scope_in: list[str]
    scope_out: list[str] = Field(
        description="Explicit exclusions. The strongest control on agent scope creep."
    )
    scenarios: list[Scenario]
    business_rules: list[str]
    test_data_needs: list[TestDataNeed]
    nfr_impact: list[NonFunctionalTarget]
    data_classification: DataClassification
    known_constraints: list[str] = Field(default_factory=list)
    preliminary_tier: RiskTier
    rough_size: str = Field(description="T-shirt size from Repository Intelligence.")
    size_confidence: str
    open_questions: list[str] = Field(
        default_factory=list,
        description="Unknowns the Domain Expert marked rather than assumed away.",
    )

    @field_validator("work_item_id", "problem_and_value", "rough_size", "size_confidence")
    @classmethod
    def _content_required(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("scope_in", "scope_out", "business_rules", "known_constraints")
    @classmethod
    def _entries_required(cls, values: list[str]) -> list[str]:
        for value in values:
            _nonblank(value)
        return values

    @field_validator("open_questions")
    @classmethod
    def _no_unresolved_questions(cls, values: list[str]) -> list[str]:
        if values:
            raise ValueError("open_questions must be resolved before Business Ready")
        return values

    @field_validator("scenarios")
    @classmethod
    def _needs_a_failure_case(cls, v: list[Scenario]) -> list[Scenario]:
        if not any(s.is_failure_case for s in v):
            raise ValueError(
                "at least one failure or edge scenario is mandatory; "
                "happy-path-only criteria are not Business Ready"
            )
        return v

    @field_validator("scope_in", "scope_out")
    @classmethod
    def _scope_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError(
                "scope_in and scope_out must be explicit; an empty list is not a scope"
            )
        return v


class ObservabilitySpec(BaseModel):
    """Becomes the declared span topology in the assurance SDK.

    This is one of the two fields that make every factory output
    assurance-designed by construction.
    """

    spans: list[str]
    metrics: list[str]


class ApprovedIntentDelta(BaseModel):
    """Empty-because-checked differs from empty-because-forgotten."""

    new_outbound_destinations: list[str] = Field(default_factory=list)
    new_data_stores: list[str] = Field(default_factory=list)
    new_capabilities: list[str] = Field(default_factory=list)
    checked: bool = Field(description="Set true to assert the delta was reviewed, not skipped.")


class ContainmentDelta(BaseModel):
    """Service-specific safety invariants. Evaluated by policy, never by an LLM."""

    min_healthy_instances: int | None = None
    isolate_replica: bool = True
    isolate_leader: bool = False
    singleton: bool = False
    stateful: bool = False
    active_lease_blocks_isolation: bool = True


class RollbackStrategy(StrEnum):
    FIX_FORWARD = "fix_forward"
    FEATURE_FLAG = "feature_flag"
    REVERT = "revert"
    BACKWARD_MIGRATION = "backward_migration"


class SolutionReady(BaseModel):
    """Gate 2 payload. Produced by the Architect using Repository Intelligence."""

    work_item_id: str
    affected_repositories: list[str]
    service_boundary_decision: str
    api_contract_delta: str | None = None
    database_changes: str | None = None
    iac_changes: str | None = None
    migration_strategy: str | None = None
    security_design: str

    observability: ObservabilitySpec
    approved_intent_delta: ApprovedIntentDelta
    containment_delta: ContainmentDelta

    rollback_strategy: RollbackStrategy
    rollback_justification: str
    feature_flag: str | None = None
    data_written_during_rollout: str | None = None

    deployment_impact: str
    final_tier: RiskTier
    changeset_id: str
    adr_ref: str | None = None
