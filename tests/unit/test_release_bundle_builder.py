import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nokinc_factory.domain.identity import DeploymentBinding
from nokinc_factory.policy.release_bundle import (
    ReleaseBundleInput,
    ReleasePromotionError,
    build_deployment_binding,
    build_release_bundle,
    verify_promotion,
)

DIGESTS = {
    "artifacts": {"payments": "sha256:" + "1" * 64},
    "infrastructure": {"terraform": "sha256:" + "2" * 64},
    "code_model_snapshot": "sha256:" + "3" * 64,
    "approved_intent_digest": "sha256:" + "4" * 64,
    "containment_contract_digest": "sha256:" + "5" * 64,
    "sbom_digest": "sha256:" + "6" * 64,
    "provenance_digest": "sha256:" + "7" * 64,
}


def release_input() -> ReleaseBundleInput:
    return ReleaseBundleInput(
        release_id="release-1", changeset_id="changeset-1", changeset_version=1,
        work_item_id="work-1", **DIGESTS,
    )


def test_builder_creates_signed_bundle_and_separate_binding() -> None:
    key = Ed25519PrivateKey.generate()
    bundle = build_release_bundle(release_input(), key)
    binding = build_deployment_binding(
        release_id=bundle.release_id, environment="staging",
        config_digest="sha256:" + "8" * 64,
        secret_version_refs=("secret://payments/v1",),
        deployment_policy_version="policy-1", approved_by="human-1", key=key,
    )

    verify_promotion(bundle, binding, key.public_key(), expected_environment="staging")
    assert bundle.signature
    assert binding.signature
    assert bundle.release_id == binding.release_id


def test_promotion_rejects_release_or_environment_substitution() -> None:
    key = Ed25519PrivateKey.generate()
    bundle = build_release_bundle(release_input(), key)
    binding = build_deployment_binding(
        release_id=bundle.release_id, environment="staging",
        config_digest="sha256:" + "8" * 64,
        secret_version_refs=("secret://payments/v1",),
        deployment_policy_version="policy-1", approved_by="human-1", key=key,
    )

    with pytest.raises(ReleasePromotionError):
        verify_promotion(bundle, binding.model_copy(update={"environment": "production"}),
                         key.public_key(), expected_environment="staging")
    with pytest.raises(ReleasePromotionError):
        verify_promotion(bundle.model_copy(update={"release_id": "release-2"}), binding,
                         key.public_key(), expected_environment="staging")


def test_binding_rejects_secret_values_and_unsigned_inputs() -> None:
    key = Ed25519PrivateKey.generate()
    bundle = build_release_bundle(release_input(), key)
    with pytest.raises(ValueError, match="secret"):
        build_deployment_binding(
            release_id=bundle.release_id, environment="staging",
            config_digest="sha256:" + "8" * 64,
            secret_version_refs=("super-secret-value",),
            deployment_policy_version="policy-1", approved_by="human-1", key=key,
        )
    with pytest.raises(ReleasePromotionError):
        verify_promotion(bundle.model_copy(update={"signature": ""}),
                         DeploymentBinding(
                             release_id=bundle.release_id, environment="staging",
                             config_digest="sha256:" + "8" * 64,
                             deployment_policy_version="policy-1", approved_by="human-1",
                             signature="",
                         ), key.public_key(), expected_environment="staging")
