"""Spec Part 1 -- approval binding and TOCTOU revalidation."""

from datetime import UTC, datetime, timedelta

import pytest

from nokinc_factory.domain.authorization import (
    ActuatorClass,
    Authorization,
    ExpiredAuthorization,
    HumanApproval,
    OnExpiry,
    StaleAuthorization,
    TargetBinding,
)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _auth(**kw: object) -> Authorization:
    base = dict(
        decision_id="d1",
        action="isolate_replica",
        actuator_class=ActuatorClass.SECURITY_EXTERNAL,
        target=TargetBinding(service="payments", target_runtime_uid="pod-abc"),
        evidence_snapshot="e1",
        coverage_snapshot="c1",
        policy_version="1",
        authorized_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        on_expiry=OnExpiry.HOLD_AND_ESCALATE,
        nonce="n1",
        signature="s1",
    )
    base.update(kw)
    return Authorization(**base)  # type: ignore[arg-type]


def test_target_identity_change_is_rejected() -> None:
    """A rolling deploy can replace the replica between authorize and execute."""
    auth = _auth()
    replacement = TargetBinding(service="payments", target_runtime_uid="pod-xyz")
    with pytest.raises(StaleAuthorization):
        auth.revalidate(replacement, NOW + timedelta(seconds=5))


def test_exceeding_max_staleness_is_rejected() -> None:
    auth = _auth()
    with pytest.raises(ExpiredAuthorization):
        auth.revalidate(auth.target, NOW + timedelta(seconds=120))


def test_two_person_control_requires_distinct_approvers() -> None:
    auth = _auth()
    digest = auth.decision_digest()
    same_person = [
        HumanApproval(
            approval_id=f"a{i}",
            approver_identity="user:1",
            decision_digest=digest,
            action=auth.action,
            target=auth.target,
            approved_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
            signature="x",
        )
        for i in (1, 2)
    ]
    auth.human_approvals = same_person
    with pytest.raises(ExpiredAuthorization, match="distinct"):
        auth.check_two_person(NOW)


def test_target_binding_rejects_image_and_config_drift() -> None:
    """Reject a matching runtime UID when image or configuration identity has drifted."""
    auth = _auth(
        target=TargetBinding(
            service="payments",
            target_runtime_uid="pod-abc",
            image_digest="sha256:good",
            config_fingerprint="cfg-A",
        )
    )
    evil = TargetBinding(
        service="payments",
        target_runtime_uid="pod-abc",
        image_digest="sha256:evil",
        config_fingerprint="cfg-B",
    )
    assert not auth.target.matches(evil)
    with pytest.raises(StaleAuthorization):
        auth.revalidate(evil, NOW + timedelta(seconds=5))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("coverage_snapshot", "coverage-CHANGED"),
        ("code_model_snapshot", "snapshot-CHANGED"),
        ("max_staleness_seconds", 3600),
        ("on_expiry", OnExpiry.RESTORE_PREVIOUS),
    ],
)
def test_decision_digest_binds_security_relevant_authorization_fields(
    field: str,
    value: object,
) -> None:
    """Security-relevant authorization changes must change the approval digest."""
    original = _auth(code_model_snapshot="snapshot-A")
    changed = original.model_copy(update={field: value})
    assert original.decision_digest() != changed.decision_digest()


def test_future_dated_human_approval_is_not_valid() -> None:
    auth = _auth()
    approval = HumanApproval(
        approval_id="future",
        approver_identity="user:2",
        decision_digest=auth.decision_digest(),
        action=auth.action,
        target=auth.target,
        approved_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
        signature="x",
    )
    assert not approval.is_valid_for(auth.decision_digest(), NOW)


def test_two_person_control_rejects_approval_metadata_for_different_target() -> None:
    auth = _auth()
    digest = auth.decision_digest()
    approvals = [
        HumanApproval(
            approval_id="a1",
            approver_identity="user:1",
            decision_digest=digest,
            action=auth.action,
            target=auth.target,
            approved_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
            signature="x",
        ),
        HumanApproval(
            approval_id="a2",
            approver_identity="user:2",
            decision_digest=digest,
            action=auth.action,
            target=TargetBinding(service="payments", target_runtime_uid="pod-other"),
            approved_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
            signature="x",
        ),
    ]
    auth.human_approvals = approvals
    with pytest.raises(ExpiredAuthorization, match="distinct"):
        auth.check_two_person(NOW)
