from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nokinc_factory.domain.identity import (
    DeploymentBinding,
    ReleaseBundle,
)
from nokinc_factory.policy.release_signing import (
    ReleaseVerificationError,
    sign_deployment_binding,
    sign_release_bundle,
    verify_deployment_binding,
    verify_release_bundle,
)

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


def bundle() -> ReleaseBundle:
    return ReleaseBundle(
        release_id="release-1", changeset_id="changeset-1", changeset_version=1,
        work_item_id="work-1", artifacts={"payments": "sha256:" + "1" * 64},
        infrastructure={"terraform": "sha256:" + "2" * 64},
        code_model_snapshot="sha256:" + "3" * 64,
        approved_intent_digest="sha256:" + "4" * 64,
        containment_contract_digest="sha256:" + "5" * 64,
        sbom_digest="sha256:" + "6" * 64,
        provenance_digest="sha256:" + "7" * 64,
        signature="",
    )


def binding() -> DeploymentBinding:
    return DeploymentBinding(
        release_id="release-1", environment="staging",
        config_digest="sha256:" + "8" * 64,
        secret_version_refs=["secret://payments/v1"],
        infrastructure_parameters_digest="sha256:" + "9" * 64,
        feature_flag_state_digest="sha256:" + "a" * 64,
        deployment_policy_version="deploy-policy-1", approved_by="human-1", signature="",
    )


def test_bundle_and_binding_have_independent_ed25519_signatures() -> None:
    key = Ed25519PrivateKey.generate()
    public = key.public_key()

    signed_bundle = sign_release_bundle(bundle(), key)
    signed_binding = sign_deployment_binding(binding(), key)

    verify_release_bundle(signed_bundle, public)
    verify_deployment_binding(signed_binding, public)
    assert signed_bundle.signature != signed_binding.signature


def test_tampering_with_artifact_or_deployment_config_is_rejected() -> None:
    key = Ed25519PrivateKey.generate()
    signed_bundle = sign_release_bundle(bundle(), key)
    signed_binding = sign_deployment_binding(binding(), key)

    with pytest.raises(ReleaseVerificationError):
        verify_release_bundle(
            signed_bundle.model_copy(update={"artifacts": {"payments": "sha256:" + "f" * 64}}),
            key.public_key(),
        )
    with pytest.raises(ReleaseVerificationError):
        verify_deployment_binding(
            signed_binding.model_copy(update={"config_digest": "sha256:" + "f" * 64}),
            key.public_key(),
        )


def test_unsigned_or_wrong_key_evidence_fails_closed() -> None:
    key = Ed25519PrivateKey.generate()
    signed = sign_release_bundle(bundle(), key)

    with pytest.raises(ReleaseVerificationError):
        verify_release_bundle(bundle(), key.public_key())
    with pytest.raises(ReleaseVerificationError):
        verify_release_bundle(signed, Ed25519PrivateKey.generate().public_key())


def test_unordered_artifacts_have_one_canonical_signed_payload() -> None:
    key = Ed25519PrivateKey.generate()
    first = sign_release_bundle(bundle(), key)
    second = sign_release_bundle(
        bundle().model_copy(update={"artifacts": {"payments": "sha256:" + "1" * 64}}), key,
    )

    assert first.signature == second.signature
