"""Build and promotion checks for signed release/deployment evidence."""

from __future__ import annotations

from typing import Annotated

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import Field

from nokinc_factory.domain.identity import DeploymentBinding, ReleaseBundle
from nokinc_factory.domain.review_base import Digest, Identifier, ReviewModel
from nokinc_factory.policy.release_signing import (
    ReleaseVerificationError,
    sign_deployment_binding,
    sign_release_bundle,
    verify_deployment_binding,
    verify_release_bundle,
)

SecretReference = Annotated[str, Field(pattern=r"^secret://[^\s]+$")]


class ReleaseBundleInput(ReviewModel):
    release_id: Identifier
    changeset_id: Identifier
    changeset_version: int = Field(strict=True, ge=1)
    work_item_id: Identifier
    artifacts: dict[str, Digest] = Field(min_length=1)
    infrastructure: dict[str, Digest] = Field(default_factory=dict)
    code_model_snapshot: Digest
    approved_intent_digest: Digest
    containment_contract_digest: Digest
    sbom_digest: Digest
    provenance_digest: Digest


class ReleasePromotionError(ValueError):
    """The exact signed release cannot be promoted with the supplied binding."""


def build_release_bundle(
    values: ReleaseBundleInput, key: Ed25519PrivateKey,
) -> ReleaseBundle:
    source = ReleaseBundleInput.model_validate(values)
    bundle = ReleaseBundle(
        release_id=source.release_id, changeset_id=source.changeset_id,
        changeset_version=source.changeset_version, work_item_id=source.work_item_id,
        artifacts=dict(source.artifacts), infrastructure=dict(source.infrastructure),
        code_model_snapshot=source.code_model_snapshot,
        approved_intent_digest=source.approved_intent_digest,
        containment_contract_digest=source.containment_contract_digest,
        sbom_digest=source.sbom_digest, provenance_digest=source.provenance_digest,
        signature="",
    )
    return sign_release_bundle(bundle, key)


def build_deployment_binding(
    *, release_id: str, environment: str, config_digest: str,
    secret_version_refs: tuple[str, ...], deployment_policy_version: str,
    approved_by: str, key: Ed25519PrivateKey,
    infrastructure_parameters_digest: str | None = None,
    feature_flag_state_digest: str | None = None,
) -> DeploymentBinding:
    if any(not reference.startswith("secret://") for reference in secret_version_refs):
        raise ValueError("deployment bindings accept secret references, not secret values")
    binding = DeploymentBinding(
        release_id=release_id, environment=environment, config_digest=config_digest,
        secret_version_refs=list(secret_version_refs),
        infrastructure_parameters_digest=infrastructure_parameters_digest,
        feature_flag_state_digest=feature_flag_state_digest,
        deployment_policy_version=deployment_policy_version,
        approved_by=approved_by, signature="",
    )
    return sign_deployment_binding(binding, key)


def verify_promotion(
    bundle: ReleaseBundle, binding: DeploymentBinding, key: Ed25519PublicKey,
    *, expected_environment: str,
) -> None:
    try:
        verify_release_bundle(bundle, key)
        verify_deployment_binding(binding, key)
    except ReleaseVerificationError as exc:
        raise ReleasePromotionError("release or deployment signature is invalid") from exc
    if bundle.release_id != binding.release_id:
        raise ReleasePromotionError("deployment binding targets a different release")
    if binding.environment != expected_environment:
        raise ReleasePromotionError("deployment binding environment mismatch")
