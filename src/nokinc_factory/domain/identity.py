"""Build and release identity.

Two rules that are easy to get wrong and expensive to fix:

  1. CodeModelSnapshot pins the FULL analysis toolchain, not just wrapper versions.
     multilspy delegates to real language-server binaries. Two snapshots produced
     with different language-server versions have different analysis semantics and
     ARE NOT COMPARABLE. This is exactly analogous to pinning the compiler.

  2. ReleaseBundle is immutable and promoted unchanged. Per-environment
     configuration lives in a separately signed DeploymentBinding. Putting config
     inside the bundle makes "promote the same artifact" a lie.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LanguageServerPin(BaseModel):
    version: str
    artifact_digest: str = Field(description="No unpinned download during a production build.")


class CodeModelToolchain(BaseModel):
    repository_intelligence_version: str
    tree_sitter_version: str
    grammar_digests: dict[str, str] = Field(default_factory=dict)
    language_servers: dict[str, LanguageServerPin] = Field(default_factory=dict)
    multilspy_version: str | None = None

    def comparable_to(self, other: CodeModelToolchain) -> bool:
        """Snapshots from different toolchains cannot be diffed for intent drift.

        A False here must invalidate cached impact analysis and force
        approved-intent re-baselining.
        """
        return self.model_dump() == other.model_dump()


class CodeModelSnapshot(BaseModel):
    """Immutable, content-addressed, produced BY THE BUILD PIPELINE.

    A separate indexing job that happens to run at build time is a race
    condition, not a binding. Runtime configuration is deliberately absent --
    that is deployment identity.
    """

    snapshot_id: str
    git_sha: str
    build_id: str
    image_digest: str
    build_config_digest: str | None = Field(
        default=None, description="Build-time defaults ONLY. Never runtime config."
    )
    toolchain: CodeModelToolchain
    generated_at: datetime
    generated_by: str = "build-pipeline"


class ReleaseBundle(BaseModel):
    """Immutable. Signed. Promoted unchanged through every environment."""

    release_id: str
    changeset_id: str
    changeset_version: int
    work_item_id: str

    artifacts: dict[str, str] = Field(description="logical name -> digest")
    infrastructure: dict[str, str] = Field(default_factory=dict)
    migration_bundle: str | None = None

    code_model_snapshot: str
    approved_intent_digest: str
    containment_contract_digest: str

    sbom_digest: str
    provenance_digest: str
    signature: str


class DeploymentBinding(BaseModel):
    """Per-environment, separately signed, separately approved.

    Without its own approval, changing production config becomes the way to
    bypass the entire gate model.
    """

    release_id: str
    environment: str
    config_digest: str
    secret_version_refs: list[str] = Field(
        default_factory=list, description="References only. Never values."
    )
    infrastructure_parameters_digest: str | None = None
    feature_flag_state_digest: str | None = None
    deployment_policy_version: str
    approved_by: str
    signature: str
