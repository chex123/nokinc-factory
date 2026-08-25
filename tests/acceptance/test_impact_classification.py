"""FROZEN CONTRACT. Do not modify in an implementation PR.

These encode the invariant from spec Part 3: evidence invalidation is
impact-based, not surface-based.
"""

from nokinc_factory.policy.impact import (
    Evidence,
    FileChange,
    ImpactClass,
    SecuritySensitiveRegistry,
    classify,
)


def test_deleted_authorization_check_invalidates_security_review() -> None:
    """A removed ownership check has NO interface surface change.

    If this is classified as an ordinary implementation change, a deleted
    authorization check ships with stale security review evidence.
    """
    result = classify(
        [FileChange(path="src/api/refund.py", removed_lines=["-    if user.id == order.owner:"])],
        SecuritySensitiveRegistry(),
    )
    assert ImpactClass.AUTHZ_CHANGED in result.classes
    assert Evidence.SECURITY_REVIEW in result.invalidated
    assert Evidence.GATE_2_APPROVAL in result.invalidated


def test_unknown_file_type_fails_closed() -> None:
    """Unknown must never mean safe."""
    result = classify(
        [FileChange(path="deploy/mystery.qqq", added_lines=["+x"])],
        SecuritySensitiveRegistry(),
    )
    assert result.requires_human
    assert result.invalidated == set(Evidence)


def test_comment_only_change_invalidates_nothing() -> None:
    result = classify(
        [FileChange(path="src/util/fmt.py", added_lines=["+# tidy up"])],
        SecuritySensitiveRegistry(),
    )
    assert result.classes == {ImpactClass.COSMETIC}
    assert result.invalidated == set()


def test_root_level_security_sensitive_paths_are_not_missed() -> None:
    registry = SecuritySensitiveRegistry()
    for path in ("auth/check.py", "payments/refund.py", "migrations/001.sql"):
        changes = [FileChange(path=path, added_lines=["+def changed(): pass"])]
        result = classify(changes, registry)
        assert (
            ImpactClass.SECURITY_SENSITIVE in result.classes
            or ImpactClass.DB_MIGRATION in result.classes
        )
        assert Evidence.SECURITY_REVIEW in result.invalidated
