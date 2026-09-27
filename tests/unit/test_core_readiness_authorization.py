"""A01 / R04: execution windows fail closed without changing snapshot semantics."""

from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from nokinc_factory.domain.authorization import (
    ActuatorClass,
    Authorization,
    ExpiredAuthorization,
    HumanApproval,
    OnExpiry,
    TargetBinding,
)

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
END = NOW + timedelta(minutes=2)
TICK = timedelta(microseconds=1)


def _authorization(**updates: object) -> Authorization:
    payload: dict[str, object] = {
        "decision_id": "synthetic-decision",
        "action": "isolate_replica",
        "actuator_class": ActuatorClass.SECURITY_EXTERNAL,
        "target": TargetBinding(service="synthetic", target_runtime_uid="instance-1"),
        "evidence_snapshot": "evidence-1",
        "coverage_snapshot": "coverage-1",
        "policy_version": "policy-1",
        "authorized_at": NOW,
        "expires_at": END,
        "max_staleness_seconds": 60,
        "on_expiry": OnExpiry.HOLD_AND_ESCALATE,
        "nonce": "synthetic-nonce",
        "signature": "unverified-signature",
    }
    payload.update(updates)
    return Authorization.model_validate(payload)


def _approval(auth: Authorization, identity: str = "human-1", **updates: object) -> HumanApproval:
    payload: dict[str, object] = {
        "approval_id": f"approval-{identity}",
        "approver_identity": identity,
        "decision_digest": auth.decision_digest(),
        "action": auth.action,
        "target": auth.target,
        "approved_at": NOW - timedelta(minutes=1),
        "expires_at": END + timedelta(minutes=1),
        "signature": "unverified-signature",
    }
    payload.update(updates)
    return HumanApproval.model_validate(payload)


@pytest.mark.parametrize("now", [NOW - TICK, END, END + TICK])
def test_execution_requires_issued_at_inclusive_expiry_exclusive(now: datetime) -> None:
    auth = _authorization(max_staleness_seconds=3600)
    with pytest.raises(ExpiredAuthorization):
        auth.revalidate(auth.target, now)


@pytest.mark.parametrize("now", [NOW, NOW + timedelta(seconds=60)])
def test_valid_window_and_inclusive_maximum_age_remain_usable(now: datetime) -> None:
    auth = _authorization()
    auth.revalidate(auth.target, now)


def test_age_just_beyond_maximum_is_rejected() -> None:
    auth = _authorization()
    with pytest.raises(ExpiredAuthorization, match="staleness"):
        auth.revalidate(auth.target, NOW + timedelta(seconds=60) + TICK)


@pytest.mark.parametrize(
    ("issued", "expires", "now", "max_age"),
    [
        (NOW.replace(tzinfo=None), END, NOW, 60),
        (NOW, END.replace(tzinfo=None), NOW, 60),
        (NOW, END, NOW.replace(tzinfo=None), 60),
        (NOW.replace(tzinfo=None), END.replace(tzinfo=None), NOW.replace(tzinfo=None), 60),
        (NOW, NOW, NOW, 60),
        (END, NOW + timedelta(seconds=30), NOW, 60),
        (NOW, END, NOW, -1),
    ],
    ids=["naive-issued", "naive-expiry", "naive-now", "all-naive", "empty", "reversed", "age"],
)
def test_invalid_authorization_windows_fail_closed_at_use(
    issued: datetime, expires: datetime, now: datetime, max_age: int,
) -> None:
    auth = _authorization().model_copy(update={
        "authorized_at": issued,
        "expires_at": expires,
        "max_staleness_seconds": max_age,
    })
    with pytest.raises(ExpiredAuthorization):
        auth.revalidate(auth.target, now)


def test_zero_staleness_allows_only_the_issuance_instant() -> None:
    auth = _authorization(max_staleness_seconds=0)
    auth.revalidate(auth.target, NOW)
    with pytest.raises(ExpiredAuthorization):
        auth.revalidate(auth.target, NOW + TICK)


def test_staleness_comparison_does_not_overflow_for_large_limits() -> None:
    auth = _authorization(max_staleness_seconds=10**20)
    auth.revalidate(auth.target, NOW + TICK)


def test_long_staleness_limits_do_not_round_away_expired_microseconds() -> None:
    issued = datetime(1000, 1, 1, tzinfo=UTC)
    age = NOW - issued
    auth = _authorization(
        authorized_at=issued, max_staleness_seconds=age.days * 86400 + age.seconds,
    )
    auth.revalidate(auth.target, NOW)
    with pytest.raises(ExpiredAuthorization, match="staleness"):
        auth.revalidate(auth.target, NOW + TICK)


def test_aware_offsets_are_compared_as_instants() -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    auth = _authorization(authorized_at=NOW.astimezone(offset), expires_at=END)
    auth.revalidate(auth.target, NOW)
    with pytest.raises(ExpiredAuthorization):
        auth.revalidate(auth.target, (NOW - TICK).astimezone(offset))


@pytest.mark.parametrize(
    ("approved", "expires", "now"),
    [
        (NOW.replace(tzinfo=None), END, NOW),
        (NOW, END.replace(tzinfo=None), NOW),
        (NOW, END, NOW.replace(tzinfo=None)),
        (NOW.replace(tzinfo=None), END.replace(tzinfo=None), NOW.replace(tzinfo=None)),
        (NOW, NOW, NOW),
        (END, NOW, NOW),
        (NOW + TICK, END, NOW),
        (NOW, END, END),
    ],
)
def test_invalid_approval_windows_return_false(
    approved: datetime, expires: datetime, now: datetime,
) -> None:
    auth = _authorization()
    approval = _approval(auth, approved_at=approved, expires_at=expires)
    assert not approval.is_valid_for(auth.decision_digest(), now)


def test_approval_issuance_and_offset_equivalence_remain_valid() -> None:
    auth = _authorization()
    approval = _approval(auth, approved_at=NOW.astimezone(timezone(timedelta(hours=-4))))
    assert approval.is_valid_for(auth.decision_digest(), NOW)
    assert not approval.is_valid_for("different-decision", NOW)


@pytest.mark.parametrize("now", [NOW - TICK, NOW + timedelta(seconds=60) + TICK, END])
def test_two_approvals_cannot_extend_the_authorization_window(now: datetime) -> None:
    auth = _authorization()
    auth.human_approvals = [_approval(auth, identity) for identity in ("human-1", "human-2")]
    with pytest.raises(ExpiredAuthorization):
        auth.check_two_person(now)


def test_two_current_bound_approvals_preserve_existing_metadata_contract() -> None:
    auth = _authorization()
    auth.human_approvals = [_approval(auth, identity) for identity in ("human-1", "human-2")]
    auth.check_two_person(NOW)


class _FoldedTimezone(tzinfo):
    """Synthetic clock rollback, independent of machine timezone databases."""

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=-5 if dt is not None and dt.fold else -4)

    def dst(self, dt: datetime | None) -> timedelta:
        return self.utcoffset(dt) + timedelta(hours=5)

    def tzname(self, dt: datetime | None) -> str:
        return "synthetic-fold"


def test_two_person_windows_overlap_by_instant_not_repeated_wall_time() -> None:
    zone = _FoldedTimezone()
    approved = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)
    expires = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    now = datetime(2026, 11, 1, 6, tzinfo=UTC)
    auth = _authorization(
        authorized_at=now, expires_at=now + timedelta(minutes=5),
    )
    auth.human_approvals = [
        _approval(auth, identity, approved_at=approved, expires_at=expires)
        for identity in ("human-1", "human-2")
    ]
    auth.check_two_person(now)


def test_naive_approval_cannot_supply_the_second_person() -> None:
    auth = _authorization()
    auth.human_approvals = [
        _approval(auth, "human-1"),
        _approval(auth, "human-2", approved_at=NOW.replace(tzinfo=None)),
    ]
    with pytest.raises(ExpiredAuthorization, match="distinct"):
        auth.check_two_person(NOW)


def test_unsafe_containment_copy_can_be_hashed_but_not_executed() -> None:
    original = _authorization()
    snapshot = original.model_copy(update={"on_expiry": OnExpiry.RESTORE_PREVIOUS})

    assert snapshot.decision_digest() != original.decision_digest()
    with pytest.raises(ExpiredAuthorization, match="containment"):
        snapshot.revalidate(snapshot.target, NOW)


@pytest.mark.parametrize(
    ("actuator", "expiry"),
    [
        (ActuatorClass.SECURITY_EXTERNAL, OnExpiry.HOLD_AND_ESCALATE),
        (ActuatorClass.SECURITY_EXTERNAL, OnExpiry.FAIL_CLOSED),
        (ActuatorClass.OPERATIONAL_EXTERNAL, OnExpiry.RESTORE_PREVIOUS),
        (ActuatorClass.COOPERATIVE, OnExpiry.RESTORE_PREVIOUS),
    ],
)
def test_safe_expiry_policies_preserve_valid_execution(
    actuator: ActuatorClass, expiry: OnExpiry,
) -> None:
    auth = _authorization(actuator_class=actuator, on_expiry=expiry)
    auth.revalidate(auth.target, NOW)