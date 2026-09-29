from __future__ import annotations

import pytest

from nokinc_factory.domain.approval import (
    ApprovalConfig,
    ApprovalIntent,
    ApprovalRun,
    ApprovalStatus,
    EnvironmentReview,
    ExpectedReviewer,
    verify_github_approval,
)

REQUEST = ApprovalIntent(
    request_id="1" * 32,
    work_item_id="wi-0123456789abcdef0123456789abcdef",
    repository="NOK-Apps/flur-backend",
    issue_number=42,
    gate="gate-1",
    decision_digest="sha256:" + "a" * 64,
    requester_login="chexudeze",
    ref="main",
)

CONFIG = ApprovalConfig(
    repository="NOK-Apps/flur-backend",
    workflow_path=".github/workflows/gate-approval.yml",
    default_branch="main",
    reviewers=(
        ExpectedReviewer(login="triplexapps", user_id=22742979),
        ExpectedReviewer(login="chex123", user_id=12345678),
    ),
)

RUN = ApprovalRun(
    run_id=1001,
    run_attempt=1,
    repository="NOK-Apps/flur-backend",
    workflow_path=".github/workflows/gate-approval.yml",
    head_branch="main",
    head_sha="a" * 40,
    event="workflow_dispatch",
    display_title=REQUEST.display_title,
    status="completed",
    conclusion="success",
)

REVIEWS = (
    EnvironmentReview(
        state="approved",
        reviewer_login="triplexapps",
        reviewer_id=22742979,
        environments=("gate-1-triplexapps",),
    ),
    EnvironmentReview(
        state="approved",
        reviewer_login="chex123",
        reviewer_id=12345678,
        environments=("gate-1-chex123",),
    ),
)


def _replace_review(review: EnvironmentReview, **changes: object) -> EnvironmentReview:
    return EnvironmentReview.model_validate(
        review.model_dump(exclude={"content_digest"}) | changes
    )


def test_approval_requires_exact_run_binding_and_two_environment_reviewers() -> None:
    evidence = verify_github_approval(
        intent=REQUEST,
        config=CONFIG,
        run=RUN,
        reviews=REVIEWS,
        expected_run_id=1001,
        expected_run_attempt=1,
    )

    assert evidence.status is ApprovalStatus.APPROVED
    assert evidence.request_id == REQUEST.request_id
    assert evidence.decision_digest == REQUEST.decision_digest
    assert evidence.work_item_id == REQUEST.work_item_id
    assert evidence.issue_number == REQUEST.issue_number
    assert evidence.approver_logins == ("chex123", "triplexapps")
    assert evidence.authorizes_execution is False


@pytest.mark.parametrize(
    ("run_change", "review_change", "reason"),
    [
        ({"display_title": "factory-approval/wrong"}, None, "RUN_BINDING_MISMATCH"),
        ({"repository": "chex123/nokinc-factory"}, None, "RUN_BINDING_MISMATCH"),
        ({"head_branch": "feature/unprotected"}, None, "RUN_BINDING_MISMATCH"),
        ({"workflow_path": ".github/workflows/other.yml"}, None, "RUN_BINDING_MISMATCH"),
        (None, (), "REVIEW_SET_INCOMPLETE"),
        (
            None,
            (
                REVIEWS[0],
                _replace_review(
                    REVIEWS[1],
                    reviewer_login="triplexapps",
                    reviewer_id=22742979,
                    environments=("gate-1-triplexapps",),
                ),
            ),
            "REVIEWER_NOT_DISTINCT",
        ),
        (
            None,
            (
                _replace_review(REVIEWS[0], environments=("gate-2-triplexapps",)),
                REVIEWS[1],
            ),
            "REVIEW_ENVIRONMENT_MISMATCH",
        ),
        (
            None,
            (REVIEWS[0], _replace_review(REVIEWS[1], state="rejected")),
            "REVIEW_NOT_APPROVED",
        ),
    ],
)
def test_mismatched_run_or_review_never_authorizes(
    run_change: dict[str, object] | None,
    review_change: tuple[EnvironmentReview, ...] | None,
    reason: str,
) -> None:
    run = RUN.model_copy(update=run_change or {})
    reviews = review_change if review_change is not None else REVIEWS

    evidence = verify_github_approval(
        intent=REQUEST,
        config=CONFIG,
        run=run,
        reviews=reviews,
        expected_run_id=1001,
        expected_run_attempt=1,
    )

    assert evidence.status is ApprovalStatus.INVALID
    assert reason in evidence.reason_codes
    assert evidence.authorizes_execution is False


def test_pending_run_remains_pending_even_if_some_reviews_arrived() -> None:
    run = RUN.model_copy(update={"status": "in_progress", "conclusion": None})

    evidence = verify_github_approval(
        intent=REQUEST,
        config=CONFIG,
        run=run,
        reviews=(REVIEWS[0],),
        expected_run_id=1001,
        expected_run_attempt=1,
    )

    assert evidence.status is ApprovalStatus.PENDING
    assert evidence.authorizes_execution is False


def test_failed_provider_run_is_not_an_approval() -> None:
    run = RUN.model_copy(update={"conclusion": "failure"})

    evidence = verify_github_approval(
        intent=REQUEST,
        config=CONFIG,
        run=run,
        reviews=REVIEWS,
        expected_run_id=1001,
        expected_run_attempt=1,
    )

    assert evidence.status is ApprovalStatus.FAILED
    assert evidence.authorizes_execution is False