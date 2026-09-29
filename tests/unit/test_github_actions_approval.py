from __future__ import annotations

from typing import cast

import pytest
from pydantic import BaseModel

from nokinc_factory.adapters.github_actions_approval import (
    ApprovalAdapterError,
    GitHubActionsApprovalAdapter,
)
from nokinc_factory.domain.approval import (
    ApprovalConfig,
    ApprovalIntent,
    ApprovalStatus,
    ExpectedReviewer,
)

_REVIEWERS = (
    ExpectedReviewer(login="triplexapps", user_id=22742979),
    ExpectedReviewer(login="chex123", user_id=12345678),
)
_CONFIG = ApprovalConfig(
    repository="NOK-Apps/flur-backend",
    workflow_path=".github/workflows/gate-approval.yml",
    default_branch="main",
    reviewers=_REVIEWERS,
)
_INTENT = ApprovalIntent(
    request_id="1" * 32,
    work_item_id="wi-0123456789abcdef0123456789abcdef",
    repository="NOK-Apps/flur-backend",
    issue_number=42,
    gate="gate-1",
    decision_digest="sha256:" + "a" * 64,
    requester_login="chexudeze",
    ref="main",
)


class FakeBroker:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def token_for(self, repository: str) -> object:
        self.requested.append(repository)
        return object()


class FakeTransport:
    def __init__(self, *, issue_body: str | None = None) -> None:
        self.issue_body = (
            issue_body
            if issue_body is not None
            else f"BusinessReady description\n<!-- factory-work-item-id: {_INTENT.work_item_id} -->"
        )
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        self.run_display_title = _INTENT.display_title
        self.run_status = "completed"
        self.run_conclusion = "success"
        self.reviews = [
            {
                "state": "approved",
                "user": {"login": "triplexapps", "id": 22742979},
                "environments": [{"name": "gate-1-triplexapps"}],
            },
            {
                "state": "approved",
                "user": {"login": "chex123", "id": 12345678},
                "environments": [{"name": "gate-1-chex123"}],
            },
        ]
        self.environment_configuration_valid = True
        self.issue_state = "open"
        self.issue_pull_request: dict[str, object] | None = None

    def request(
        self,
        method: str,
        path: str,
        response_model: type[BaseModel],
        body: BaseModel | None = None,
    ) -> BaseModel:
        body_dict = None if body is None else cast(dict[str, object], body.model_dump(mode="json"))
        self.calls.append((method, path, body_dict))
        if path.endswith("/issues/42"):
            raw: object = {
                "number": 42,
                "html_url": "https://github.com/NOK-Apps/flur-backend/issues/42",
                "body": self.issue_body,
                "state": self.issue_state,
                "pull_request": self.issue_pull_request,
            }
        elif "/environments/" in path:
            reviewer = "triplexapps" if path.endswith("gate-1-triplexapps") else "chex123"
            reviewer_id = 22742979 if reviewer == "triplexapps" else 12345678
            raw = {
                "protection_rules": [{
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [{
                        "type": "User",
                        "reviewer": {"login": reviewer, "id": reviewer_id},
                    }],
                }],
                "deployment_branch_policy": {
                    "protected_branches": True,
                    "custom_branch_policies": False,
                },
            }
            if not self.environment_configuration_valid:
                raw["deployment_branch_policy"] = {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                }
        elif method == "POST" and path.endswith("/dispatches"):
            raw = {
                "workflow_run_id": 1001,
                "run_url": "https://api.github.com/repos/NOK-Apps/flur-backend/actions/runs/1001",
                "html_url": "https://github.com/NOK-Apps/flur-backend/actions/runs/1001",
            }
        elif path.endswith("/actions/runs/1001"):
            raw = {
                "id": 1001,
                "run_attempt": 1,
                "repository": {"full_name": "NOK-Apps/flur-backend"},
                "path": ".github/workflows/gate-approval.yml",
                "head_branch": "main",
                "head_sha": "b" * 40,
                "event": "workflow_dispatch",
                "display_title": self.run_display_title,
                "status": self.run_status,
                "conclusion": self.run_conclusion,
            }
        elif path.endswith("/actions/runs/1001/approvals"):
            raw = self.reviews
        else:
            raise AssertionError(f"unexpected GitHub request: {method} {path}")
        return response_model.model_validate(raw)


def _adapter(transport: FakeTransport) -> GitHubActionsApprovalAdapter:
    return GitHubActionsApprovalAdapter(
        config=_CONFIG,
        broker=FakeBroker(),
        transport_factory=lambda _broker, _repository: transport,
    )


def test_dispatch_checks_issue_binding_and_environment_policy_before_dispatch() -> None:
    transport = FakeTransport()
    adapter = _adapter(transport)

    dispatch = adapter.dispatch(_INTENT)

    assert dispatch.run_id == 1001
    assert dispatch.request_id == _INTENT.request_id
    assert dispatch.intent_digest == _INTENT.content_digest
    assert transport.calls[-1][0] == "POST"
    assert transport.calls[-1][1].endswith(
        "/actions/workflows/.github/workflows/gate-approval.yml/dispatches"
    )
    assert transport.calls[-1][2] == {
        "ref": "main",
        "inputs": {
            "request_id": _INTENT.request_id,
            "work_item_id": _INTENT.work_item_id,
            "issue_number": "42",
            "gate": "gate-1",
            "decision_digest": _INTENT.decision_digest,
        },
    }
    assert all(method == "GET" for method, _path, _body in transport.calls[:-1])


def test_unlinked_issue_blocks_dispatch() -> None:
    transport = FakeTransport(issue_body="Unlinked issue")

    with pytest.raises(ApprovalAdapterError, match="not canonically linked"):
        _adapter(transport).dispatch(_INTENT)

    assert not any(method == "POST" for method, _path, _body in transport.calls)


def test_unprotected_environment_blocks_dispatch() -> None:
    transport = FakeTransport()
    transport.environment_configuration_valid = False

    with pytest.raises(ApprovalAdapterError, match="protected branches only"):
        _adapter(transport).dispatch(_INTENT)

    assert not any(method == "POST" for method, _path, _body in transport.calls)


def test_inspect_accepts_only_exact_successful_run_and_two_provider_reviews() -> None:
    transport = FakeTransport()
    adapter = _adapter(transport)
    dispatch = adapter.dispatch(_INTENT)

    evidence = adapter.inspect(_INTENT, dispatch)

    assert evidence.status is ApprovalStatus.APPROVED
    assert evidence.request_id == _INTENT.request_id
    assert evidence.work_item_id == _INTENT.work_item_id
    assert evidence.decision_digest == _INTENT.decision_digest
    assert evidence.approver_logins == ("chex123", "triplexapps")
    assert evidence.authorizes_execution is False


def test_inspect_rejects_wrong_provider_run_name_and_pending_remains_pending() -> None:
    transport = FakeTransport()
    adapter = _adapter(transport)
    dispatch = adapter.dispatch(_INTENT)
    transport.run_display_title = "approval for another decision"

    invalid = adapter.inspect(_INTENT, dispatch)

    assert invalid.status is ApprovalStatus.INVALID
    assert "RUN_BINDING_MISMATCH" in invalid.reason_codes
    assert invalid.authorizes_execution is False

    transport.run_display_title = _INTENT.display_title
    transport.run_status = "in_progress"
    transport.run_conclusion = None
    pending = adapter.inspect(_INTENT, dispatch)
    assert pending.status is ApprovalStatus.PENDING
    assert pending.authorizes_execution is False


def test_dispatch_receipt_cannot_be_replayed_for_another_intent() -> None:
    transport = FakeTransport()
    adapter = _adapter(transport)
    dispatch = adapter.dispatch(_INTENT)
    other_intent = ApprovalIntent.model_validate(
        _INTENT.model_dump(exclude={"content_digest"})
        | {"decision_digest": "sha256:" + "c" * 64}
    )

    with pytest.raises(ApprovalAdapterError, match="does not match"):
        adapter.inspect(other_intent, dispatch)
