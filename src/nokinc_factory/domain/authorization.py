"""Authorization artefacts. Security-critical -- read Spec Part 1 and 1.3.2 first.

Invariants enforced here:
  * A human approves an IMMUTABLE DECISION, never a general concept of approval.
  * A signature proves origin, not authorization.
  * Two-person control requires a shared, unexpired window.
  * Expiry of security containment goes to a SAFE STATE, not the previous state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field


class OnExpiry(StrEnum):
    """What happens when a temporary action's lease runs out.

    RESTORE_PREVIOUS is correct for cooperative and operational controls.
    It is WRONG for security containment: if the plane dies, a network deny must
    not lift and reconnect a compromised process. That is an attack path.
    """

    RESTORE_PREVIOUS = "RESTORE_PREVIOUS"
    HOLD_AND_ESCALATE = "HOLD_AND_ESCALATE"
    FAIL_CLOSED = "FAIL_CLOSED"


class ActuatorClass(StrEnum):
    COOPERATIVE = "COOPERATIVE"  # in-app; NOT trusted if process compromised
    OPERATIONAL_EXTERNAL = "OPERATIONAL_EXTERNAL"
    SECURITY_EXTERNAL = "SECURITY_EXTERNAL"


class TargetBinding(BaseModel):
    """Identity of the exact runtime object the evidence came from.

    Naming a service is not enough: a rolling deploy can replace the replica
    between authorization and execution (TOCTOU). Revalidate immediately
    before acting.
    """

    service: str
    target_runtime_uid: str
    container_id: str | None = None
    deployment_revision: str | None = None
    image_digest: str | None = None
    config_fingerprint: str | None = None

    def matches(self, observed: TargetBinding) -> bool:
        return (
            self.service == observed.service
            and self.target_runtime_uid == observed.target_runtime_uid
            and (self.container_id is None or self.container_id == observed.container_id)
            and (
                self.deployment_revision is None
                or self.deployment_revision == observed.deployment_revision
            )
            and (self.image_digest is None or self.image_digest == observed.image_digest)
            and (
                self.config_fingerprint is None
                or self.config_fingerprint == observed.config_fingerprint
            )
        )


class HumanApproval(BaseModel):
    """A signed approval bound to one exact decision.

    `decision_digest` is the whole point. Without it, an approval for
    "isolate replica A" can be replayed against a mutated proposal for
    "isolate the service".
    """

    approval_id: str
    approver_identity: str
    decision_digest: str
    action: str
    target: TargetBinding
    approved_at: datetime
    expires_at: datetime
    signature: str

    def is_valid_for(self, digest: str, now: datetime) -> bool:
        # Future-dated approvals are not valid yet. This also prevents a malformed
        # provider record from satisfying two-person control before it existed.
        return self.decision_digest == digest and self.approved_at <= now < self.expires_at


class StaleAuthorization(Exception):
    """Target identity changed between authorization and execution."""


class ExpiredAuthorization(Exception):
    """Authorization or approval outlived its window."""


class Authorization(BaseModel):
    """Issued by the POLICY domain. Executed by the ACTUATION domain.

    Analysis may propose. Only policy may authorize. Only actuation may execute.
    No domain does all three.
    """

    decision_id: str
    action: str
    actuator_class: ActuatorClass
    target: TargetBinding
    parameters: dict[str, object] = Field(default_factory=dict)

    evidence_snapshot: str
    coverage_snapshot: str
    code_model_snapshot: str | None = None
    policy_version: str

    authorized_at: datetime
    expires_at: datetime
    max_staleness_seconds: int = 60
    on_expiry: OnExpiry
    nonce: str
    signature: str

    human_approvals: list[HumanApproval] = Field(default_factory=list)

    def decision_digest(self) -> str:
        """Digest of the fields an approver is actually agreeing to.

        Deliberately excludes signature, nonce and the approvals themselves.
        """
        payload = {
            "decision_id": self.decision_id,
            "action": self.action,
            "actuator_class": self.actuator_class.value,
            "target": self.target.model_dump(exclude_none=True),
            "parameters": self.parameters,
            "evidence_snapshot": self.evidence_snapshot,
            "coverage_snapshot": self.coverage_snapshot,
            "code_model_snapshot": self.code_model_snapshot,
            "policy_version": self.policy_version,
            "authorized_at": self.authorized_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "max_staleness_seconds": self.max_staleness_seconds,
            "on_expiry": self.on_expiry.value,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def check_two_person(self, now: datetime) -> None:
        """Two distinct approvers, same decision, overlapping unexpired window.

        Approver A at 09:00 and approver B at 17:00 on a decision that expired
        at 09:15 is not two-person control.
        """
        digest = self.decision_digest()
        valid = [
            a
            for a in self.human_approvals
            if a.is_valid_for(digest, now)
            and a.action == self.action
            and a.target == self.target
        ]
        identities = {a.approver_identity for a in valid}
        if len(identities) < 2:
            raise ExpiredAuthorization(
                f"{self.decision_id}: two-person control requires 2 distinct valid approvers, "
                f"found {len(identities)}"
            )
        if min(a.expires_at for a in valid) <= max(a.approved_at for a in valid):
            raise ExpiredAuthorization(f"{self.decision_id}: approval windows do not overlap")

    def revalidate(self, observed: TargetBinding, now: datetime) -> None:
        """Call IMMEDIATELY before execution. Never cache the result."""
        if now > self.expires_at:
            raise ExpiredAuthorization(f"{self.decision_id}: authorization expired")
        if now > self.authorized_at + timedelta(seconds=self.max_staleness_seconds):
            raise ExpiredAuthorization(
                f"{self.decision_id}: exceeded max_staleness of {self.max_staleness_seconds}s"
            )
        if not self.target.matches(observed):
            raise StaleAuthorization(
                f"{self.decision_id}: target identity changed since authorization"
            )
