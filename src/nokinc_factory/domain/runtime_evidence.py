"""Signed, content-bound evidence contracts for isolated runtime execution.

Spec Parts 8 and 14: a source checkout or local subprocess result is not runtime
proof. This module verifies a receipt produced by a separately trusted worker;
it does not implement sandboxing or authorize a merge.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import Field, StrictInt, model_validator

from nokinc_factory.domain.review_base import (
    Digest,
    Identifier,
    Moment,
    ReviewModel,
    utc,
)

RuntimeGate = Literal["build", "unit", "acceptance", "types", "contract", "e2e"]
RuntimeResult = Literal["PASS", "FAIL", "NOT_AVAILABLE"]
_SIGNATURE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{80,100}$")


class RuntimeDecisionStatus(StrEnum):
    VERIFIED_PASS = "VERIFIED_PASS"
    VERIFIED_FAIL = "VERIFIED_FAIL"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    REJECTED = "REJECTED"


class RuntimeRequest(ReviewModel):
    """Immutable execution request pinned to one candidate and worker image."""

    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    work_item_id: Identifier = Field(max_length=80)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    source_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    candidate_digest: Digest
    gate: RuntimeGate
    toolchain_digest: Digest
    suite_digest: Digest
    runner_digest: Digest
    environment_digest: Digest
    worker_id: Identifier
    worker_image_digest: Digest
    issued_at: Moment
    expires_at: Moment
    max_duration_seconds: StrictInt = Field(gt=0, le=3600)

    @model_validator(mode="after")
    def _window_is_ordered(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("runtime request expiry must follow issue time")
        return self


class RuntimeReceipt(ReviewModel):
    """Worker-signed status and evidence identity; raw output is never accepted."""

    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    request_digest: Digest
    work_item_id: Identifier = Field(max_length=80)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    source_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    candidate_digest: Digest
    gate: RuntimeGate
    toolchain_digest: Digest
    suite_digest: Digest
    runner_digest: Digest
    environment_digest: Digest
    worker_id: Identifier
    worker_image_digest: Digest
    status: RuntimeResult
    exit_code: StrictInt | None
    started_at: Moment
    completed_at: Moment
    output_digest: Digest
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{80,100}$")

    @model_validator(mode="after")
    def _result_fields(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("runtime receipt completion precedes start")
        if self.status == "PASS" and self.exit_code != 0:
            raise ValueError("passing runtime receipt requires exit code zero")
        if self.status == "FAIL" and (self.exit_code is None or self.exit_code == 0):
            raise ValueError("failed runtime receipt requires a nonzero exit code")
        if self.status == "NOT_AVAILABLE" and self.exit_code is not None:
            raise ValueError("unavailable runtime receipt cannot claim an exit code")
        return self


class RuntimeEvidenceDecision(ReviewModel):
    """Verifier output that remains advisory and cannot authorize a merge."""

    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    work_item_id: Identifier = Field(max_length=80)
    source_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    candidate_digest: Digest
    gate: RuntimeGate
    status: RuntimeDecisionStatus
    evidence_digest: Digest | None = None
    reason_codes: tuple[Identifier, ...] = ()
    authorizes_merge: Literal[False] = False


def canonical_runtime_receipt_payload(receipt: RuntimeReceipt) -> bytes:
    """Bytes the isolated worker signs; exclude signature and computed content hash."""
    value = RuntimeReceipt.model_validate(receipt)
    return json.dumps(
        value.model_dump(mode="json", exclude={"signature", "content_digest"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sign_runtime_receipt(
    receipt: RuntimeReceipt,
    private_key: Ed25519PrivateKey,
) -> RuntimeReceipt:
    """Worker-side signing helper for a receipt already built from final facts."""
    unsigned = RuntimeReceipt.model_validate(receipt)
    signature = base64.urlsafe_b64encode(
        private_key.sign(canonical_runtime_receipt_payload(unsigned))
    ).rstrip(b"=").decode("ascii")
    return RuntimeReceipt.model_validate(
        unsigned.model_dump(mode="json", exclude={"signature", "content_digest"})
        | {"signature": signature}
    )


def verify_runtime_receipt(
    *,
    request: RuntimeRequest,
    receipt: RuntimeReceipt,
    trusted_worker_id: str,
    trusted_worker_public_key: Ed25519PublicKey,
    now: datetime,
) -> RuntimeEvidenceDecision:
    """Verify origin, candidate binding, worker, and execution window fail-closed."""
    checked_request = RuntimeRequest.model_validate(request)
    checked_receipt = RuntimeReceipt.model_validate(receipt)
    current = utc(now)

    mismatch = (
        checked_receipt.request_id != checked_request.request_id
        or checked_receipt.request_digest != checked_request.content_digest
        or checked_receipt.work_item_id != checked_request.work_item_id
        or checked_receipt.repository != checked_request.repository
        or checked_receipt.source_sha != checked_request.source_sha
        or checked_receipt.candidate_digest != checked_request.candidate_digest
        or checked_receipt.gate != checked_request.gate
        or checked_receipt.toolchain_digest != checked_request.toolchain_digest
        or checked_receipt.suite_digest != checked_request.suite_digest
        or checked_receipt.runner_digest != checked_request.runner_digest
        or checked_receipt.environment_digest != checked_request.environment_digest
        or checked_receipt.worker_image_digest != checked_request.worker_image_digest
    )
    reasons: list[str] = []
    if mismatch:
        reasons.append("BINDING_MISMATCH")
    if (
        checked_request.worker_id != trusted_worker_id
        or checked_receipt.worker_id != trusted_worker_id
    ):
        reasons.append("WORKER_IDENTITY_MISMATCH")
    if not (
        checked_request.issued_at <= checked_receipt.started_at
        <= checked_receipt.completed_at <= checked_request.expires_at
    ) or current < checked_request.issued_at or current >= checked_request.expires_at:
        reasons.append("REQUEST_EXPIRED")
    duration = (checked_receipt.completed_at - checked_receipt.started_at).total_seconds()
    if duration > checked_request.max_duration_seconds:
        reasons.append("DURATION_EXCEEDED")
    if reasons:
        return _decision(checked_request, RuntimeDecisionStatus.REJECTED, reasons=tuple(reasons))

    try:
        signature = _decode_signature(checked_receipt.signature)
        trusted_worker_public_key.verify(
            signature,
            canonical_runtime_receipt_payload(checked_receipt),
        )
    except (ValueError, InvalidSignature):
        return _decision(
            checked_request,
            RuntimeDecisionStatus.REJECTED,
            reasons=("SIGNATURE_INVALID",),
        )

    if checked_receipt.status == "NOT_AVAILABLE":
        return _decision(
            checked_request,
            RuntimeDecisionStatus.NOT_AVAILABLE,
            evidence_digest=checked_receipt.content_digest,
        )
    if checked_receipt.status == "FAIL":
        return _decision(
            checked_request,
            RuntimeDecisionStatus.VERIFIED_FAIL,
            evidence_digest=checked_receipt.content_digest,
        )
    return _decision(
        checked_request,
        RuntimeDecisionStatus.VERIFIED_PASS,
        evidence_digest=checked_receipt.content_digest,
    )


def _decode_signature(signature: str) -> bytes:
    if _SIGNATURE_PATTERN.fullmatch(signature) is None:
        raise ValueError("runtime signature encoding is invalid")
    return base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))


def _decision(
    request: RuntimeRequest,
    status: RuntimeDecisionStatus,
    *,
    evidence_digest: str | None = None,
    reasons: tuple[str, ...] = (),
) -> RuntimeEvidenceDecision:
    return RuntimeEvidenceDecision(
        request_id=request.request_id,
        work_item_id=request.work_item_id,
        source_sha=request.source_sha,
        candidate_digest=request.candidate_digest,
        gate=request.gate,
        status=status,
        evidence_digest=evidence_digest,
        reason_codes=reasons,
    )
