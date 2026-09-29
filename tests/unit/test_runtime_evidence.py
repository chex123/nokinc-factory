from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nokinc_factory.adapters.subprocess_toolchain import SubprocessToolchain
from nokinc_factory.domain.review_base import content_digest
from nokinc_factory.domain.runtime_evidence import (
    RuntimeDecisionStatus,
    RuntimeReceipt,
    RuntimeRequest,
    RuntimeResult,
    canonical_runtime_receipt_payload,
    verify_runtime_receipt,
)
from nokinc_factory.ports.runtime_execution import RuntimeExecutionPort
from nokinc_factory.ports.toolchain import GateName, ToolchainSpec

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _request() -> RuntimeRequest:
    return RuntimeRequest(
        request_id="2" * 32,
        work_item_id="wi-0123456789abcdef0123456789abcdef",
        repository="NOK-Apps/flur-backend",
        source_sha="a" * 40,
        candidate_digest="sha256:" + "5" * 64,
        gate="unit",
        toolchain_digest="sha256:" + "1" * 64,
        suite_digest="sha256:" + "6" * 64,
        runner_digest="sha256:" + "2" * 64,
        environment_digest="sha256:" + "3" * 64,
        worker_id="runtime-worker-prod",
        worker_image_digest="sha256:" + "4" * 64,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        max_duration_seconds=120,
    )


def _receipt(
    request: RuntimeRequest,
    private_key: Ed25519PrivateKey,
    *,
    status: RuntimeResult = "PASS",
    exit_code: int | None = 0,
    overrides: dict[str, object] | None = None,
) -> RuntimeReceipt:
    values: dict[str, object] = {
        "request_id": request.request_id,
        "request_digest": request.content_digest,
        "work_item_id": request.work_item_id,
        "repository": request.repository,
        "source_sha": request.source_sha,
        "candidate_digest": request.candidate_digest,
        "gate": request.gate,
        "toolchain_digest": request.toolchain_digest,
        "suite_digest": request.suite_digest,
        "runner_digest": request.runner_digest,
        "environment_digest": request.environment_digest,
        "worker_id": request.worker_id,
        "worker_image_digest": request.worker_image_digest,
        "status": status,
        "exit_code": exit_code,
        "started_at": NOW + timedelta(seconds=1),
        "completed_at": NOW + timedelta(seconds=5),
        "output_digest": content_digest({"summary": "synthetic result"}),
        "signature": "A" * 86,
    }
    values.update(overrides or {})
    unsigned = RuntimeReceipt.model_validate(values)
    signature = private_key.sign(canonical_runtime_receipt_payload(unsigned))
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return RuntimeReceipt.model_validate(
        values | {"signature": encoded_signature}
    )


def test_signed_runtime_receipt_is_verified_but_never_authorizes_merge() -> None:
    private_key = Ed25519PrivateKey.generate()
    request = _request()
    receipt = _receipt(request, private_key)

    decision = verify_runtime_receipt(
        request=request,
        receipt=receipt,
        trusted_worker_id=request.worker_id,
        trusted_worker_public_key=private_key.public_key(),
        now=NOW + timedelta(seconds=8),
    )

    assert decision.status is RuntimeDecisionStatus.VERIFIED_PASS
    assert decision.request_id == request.request_id
    assert decision.source_sha == request.source_sha
    assert decision.authorizes_merge is False


@pytest.mark.parametrize(
    ("receipt_change", "reason"),
    [
        ({"source_sha": "b" * 40}, "BINDING_MISMATCH"),
        ({"gate": "acceptance"}, "BINDING_MISMATCH"),
        ({"environment_digest": "sha256:" + "f" * 64}, "BINDING_MISMATCH"),
    ],
)
def test_receipt_for_other_candidate_or_gate_is_rejected(
    receipt_change: dict[str, object],
    reason: str,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    request = _request()
    changed = _receipt(request, private_key, overrides=receipt_change)

    decision = verify_runtime_receipt(
        request=request,
        receipt=changed,
        trusted_worker_id=request.worker_id,
        trusted_worker_public_key=private_key.public_key(),
        now=NOW + timedelta(seconds=8),
    )

    assert decision.status is RuntimeDecisionStatus.REJECTED
    assert reason in decision.reason_codes
    assert decision.authorizes_merge is False


def test_invalid_signature_expired_request_and_untrusted_worker_fail_closed() -> None:
    trusted_key = Ed25519PrivateKey.generate()
    other_key = Ed25519PrivateKey.generate()
    request = _request()
    receipt = _receipt(request, other_key)

    invalid_signature = verify_runtime_receipt(
        request=request,
        receipt=receipt,
        trusted_worker_id=request.worker_id,
        trusted_worker_public_key=trusted_key.public_key(),
        now=NOW + timedelta(seconds=8),
    )
    expired = verify_runtime_receipt(
        request=request,
        receipt=_receipt(request, trusted_key),
        trusted_worker_id=request.worker_id,
        trusted_worker_public_key=trusted_key.public_key(),
        now=NOW + timedelta(minutes=11),
    )
    wrong_worker = verify_runtime_receipt(
        request=request,
        receipt=_receipt(request, trusted_key),
        trusted_worker_id="untrusted-worker",
        trusted_worker_public_key=trusted_key.public_key(),
        now=NOW + timedelta(seconds=8),
    )

    assert invalid_signature.status is RuntimeDecisionStatus.REJECTED
    assert "SIGNATURE_INVALID" in invalid_signature.reason_codes
    assert expired.status is RuntimeDecisionStatus.REJECTED
    assert "REQUEST_EXPIRED" in expired.reason_codes
    assert wrong_worker.status is RuntimeDecisionStatus.REJECTED
    assert "WORKER_IDENTITY_MISMATCH" in wrong_worker.reason_codes


def test_failed_worker_result_is_valid_evidence_but_not_a_pass() -> None:
    private_key = Ed25519PrivateKey.generate()
    request = _request()
    receipt = _receipt(request, private_key, status="FAIL", exit_code=2)

    decision = verify_runtime_receipt(
        request=request,
        receipt=receipt,
        trusted_worker_id=request.worker_id,
        trusted_worker_public_key=private_key.public_key(),
        now=NOW + timedelta(seconds=8),
    )

    assert decision.status is RuntimeDecisionStatus.VERIFIED_FAIL
    assert decision.authorizes_merge is False


def test_local_subprocess_toolchain_is_not_an_isolated_runtime_port() -> None:
    local_executor = SubprocessToolchain(
        ToolchainSpec(language="python", commands={GateName.UNIT: "pytest -q"}),
    )

    assert not isinstance(local_executor, RuntimeExecutionPort)