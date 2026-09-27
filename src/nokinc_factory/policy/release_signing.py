"""Authenticated signatures for immutable release and deployment evidence."""

from __future__ import annotations

import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from nokinc_factory.domain.identity import DeploymentBinding, ReleaseBundle


class ReleaseVerificationError(ValueError):
    """Signed release evidence is missing, malformed, or tampered."""


def _payload(model: ReleaseBundle | DeploymentBinding) -> bytes:
    values = model.model_dump(mode="json", exclude={"signature"})
    return json.dumps(values, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _encode(signature: bytes) -> str:
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if not value or any(character not in alphabet for character in value):
        raise ReleaseVerificationError("signature encoding is invalid")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise ReleaseVerificationError("signature encoding is invalid") from exc


def _sign(model: ReleaseBundle | DeploymentBinding, key: Ed25519PrivateKey) -> str:
    return _encode(key.sign(_payload(model)))


def _verify(model: ReleaseBundle | DeploymentBinding, key: Ed25519PublicKey) -> None:
    if not model.signature:
        raise ReleaseVerificationError("signed evidence is required")
    try:
        key.verify(_decode(model.signature), _payload(model))
    except (InvalidSignature, ReleaseVerificationError) as exc:
        raise ReleaseVerificationError("signed evidence does not match its content") from exc


def sign_release_bundle(bundle: ReleaseBundle, key: Ed25519PrivateKey) -> ReleaseBundle:
    candidate = ReleaseBundle.model_validate(bundle)
    return candidate.model_copy(update={"signature": _sign(candidate, key)})


def verify_release_bundle(bundle: ReleaseBundle, key: Ed25519PublicKey) -> None:
    candidate = ReleaseBundle.model_validate(bundle)
    _verify(candidate, key)


def sign_deployment_binding(
    binding: DeploymentBinding, key: Ed25519PrivateKey,
) -> DeploymentBinding:
    candidate = DeploymentBinding.model_validate(binding)
    return candidate.model_copy(update={"signature": _sign(candidate, key)})


def verify_deployment_binding(binding: DeploymentBinding, key: Ed25519PublicKey) -> None:
    candidate = DeploymentBinding.model_validate(binding)
    _verify(candidate, key)


def canonical_signed_payload(model: ReleaseBundle | DeploymentBinding) -> str:
    """Return the content that must be signed, useful for audit evidence."""
    return _payload(model).decode("utf-8")
