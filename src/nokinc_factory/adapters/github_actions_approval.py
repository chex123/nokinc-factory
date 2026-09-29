"""GitHub Actions Environment approval adapter. See Spec Part 1 and Part 11.

This adapter trusts only authenticated workflow-run and Environment approval
API responses from the configured repository. Issue comments, labels, and a
successful workflow alone are not approval evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol, cast
from urllib.parse import quote

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, RootModel, StrictBool, StrictInt

from nokinc_factory.adapters.github_app_broker import (
    BrokeredGitHubTransport,
    GitHubAppCredentialPort,
)
from nokinc_factory.domain.approval import (
    ApprovalConfig,
    ApprovalDispatch,
    ApprovalEvidence,
    ApprovalIntent,
    ApprovalRun,
    EnvironmentReview,
    verify_github_approval,
)
from nokinc_factory.ports.approval import ApprovalPort


class ApprovalAdapterError(RuntimeError):
    """The provider request or its identity/configuration check failed closed."""


class _StrictProviderModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class _IssueResponse(_StrictProviderModel):
    number: StrictInt = Field(gt=0)
    html_url: AnyUrl
    body: str | None = None
    state: Literal["open", "closed"]
    pull_request: dict[str, object] | None = None


class _ReviewerIdentity(_StrictProviderModel):
    login: str = Field(min_length=1)
    id: StrictInt = Field(gt=0)


class _EnvironmentReviewer(_StrictProviderModel):
    type: Literal["User", "Team"]
    reviewer: _ReviewerIdentity


class _EnvironmentRule(_StrictProviderModel):
    type: str
    prevent_self_review: StrictBool | None = None
    reviewers: list[_EnvironmentReviewer] = Field(default_factory=list)


class _DeploymentBranchPolicy(_StrictProviderModel):
    protected_branches: StrictBool
    custom_branch_policies: StrictBool


class _EnvironmentResponse(_StrictProviderModel):
    protection_rules: list[_EnvironmentRule] = Field(default_factory=list)
    deployment_branch_policy: _DeploymentBranchPolicy | None = None


class _DispatchPayload(_StrictProviderModel):
    ref: str = Field(min_length=1)
    inputs: dict[str, str]


class _DispatchResponse(_StrictProviderModel):
    workflow_run_id: StrictInt = Field(gt=0)
    run_url: AnyUrl
    html_url: AnyUrl


class _RepositoryIdentity(_StrictProviderModel):
    full_name: str = Field(min_length=1)


class _RunResponse(_StrictProviderModel):
    id: StrictInt = Field(gt=0)
    run_attempt: StrictInt = Field(ge=1)
    repository: _RepositoryIdentity
    path: str = Field(min_length=1)
    head_branch: str = Field(min_length=1)
    head_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    event: str = Field(min_length=1)
    display_title: str = Field(min_length=1)
    status: Literal[
        "queued",
        "in_progress",
        "waiting",
        "requested",
        "pending",
        "action_required",
        "completed",
    ]
    conclusion: str | None = None


class _ApprovalEnvironment(_StrictProviderModel):
    name: str = Field(min_length=1)


class _ApprovalUser(_StrictProviderModel):
    login: str = Field(min_length=1)
    id: StrictInt = Field(gt=0)


class _ApprovalRecord(_StrictProviderModel):
    state: Literal["approved", "rejected", "pending"]
    user: _ApprovalUser
    environments: list[_ApprovalEnvironment] = Field(min_length=1, max_length=4)


class _ApprovalRecords(RootModel[list[_ApprovalRecord]]):
    model_config = ConfigDict(strict=True)


class _ApprovalTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        response_model: type[BaseModel],
        body: BaseModel | None = None,
    ) -> BaseModel: ...


class GitHubActionsApprovalAdapter(ApprovalPort):
    """Dispatch to a fixed App-covered repo and verify provider review records."""

    def __init__(
        self,
        *,
        config: ApprovalConfig,
        broker: GitHubAppCredentialPort,
        transport_factory: (
            Callable[[GitHubAppCredentialPort, str], _ApprovalTransport] | None
        ) = None,
    ) -> None:
        self._config = ApprovalConfig.model_validate(config)
        self._broker = broker
        self._transport_factory = transport_factory or self._default_transport
        self._transport = self._transport_factory(broker, self._config.repository)

    @staticmethod
    def _default_transport(
        broker: GitHubAppCredentialPort,
        repository: str,
    ) -> _ApprovalTransport:
        return BrokeredGitHubTransport(broker=broker, repository=repository)

    def config(self) -> ApprovalConfig:
        return self._config

    def dispatch(self, intent: ApprovalIntent) -> ApprovalDispatch:
        self._require_intent(intent)
        self._verify_issue_binding(intent)
        self._verify_environment_configuration(intent)
        body = _DispatchPayload(
            ref=intent.ref,
            inputs={
                "request_id": intent.request_id,
                "work_item_id": intent.work_item_id,
                "issue_number": str(intent.issue_number),
                "gate": intent.gate,
                "decision_digest": intent.decision_digest,
            },
        )
        owner, repository_name = self._repo_parts
        path = (
            f"/repos/{quote(owner, safe='')}/{quote(repository_name, safe='')}"
            f"/actions/workflows/{quote(self._config.workflow_path, safe='/')}/dispatches"
        )
        response = cast(_DispatchResponse, self._transport.request(
            "POST", path, _DispatchResponse, body,
        ))
        return ApprovalDispatch(
            request_id=intent.request_id,
            intent_digest=intent.content_digest,
            repository=self._config.repository,
            run_id=response.workflow_run_id,
            run_attempt=1,
            run_url=str(response.run_url),
        )

    def inspect(
        self,
        intent: ApprovalIntent,
        dispatch: ApprovalDispatch,
    ) -> ApprovalEvidence:
        self._require_intent(intent)
        if (
            dispatch.request_id != intent.request_id
            or dispatch.intent_digest != intent.content_digest
            or dispatch.repository != self._config.repository
        ):
            raise ApprovalAdapterError("approval dispatch receipt does not match the request")
        owner, repository_name = self._repo_parts
        base = f"/repos/{quote(owner, safe='')}/{quote(repository_name, safe='')}"
        run = cast(_RunResponse, self._transport.request(
            "GET", f"{base}/actions/runs/{dispatch.run_id}", _RunResponse,
        ))
        run_snapshot = ApprovalRun(
            run_id=run.id,
            run_attempt=run.run_attempt,
            repository=run.repository.full_name,
            workflow_path=run.path,
            head_branch=run.head_branch,
            head_sha=run.head_sha,
            event=run.event,
            display_title=run.display_title,
            status=run.status,
            conclusion=run.conclusion,
        )
        if run.status != "completed":
            records: tuple[EnvironmentReview, ...] = ()
        else:
            raw = self._transport.request(
                "GET", f"{base}/actions/runs/{dispatch.run_id}/approvals", _ApprovalRecords,
            )
            approvals = cast(_ApprovalRecords, raw).root
            records = tuple(EnvironmentReview(
                state=record.state,
                reviewer_login=record.user.login,
                reviewer_id=record.user.id,
                environments=tuple(environment.name for environment in record.environments),
            ) for record in approvals)
        return verify_github_approval(
            intent=intent,
            config=self._config,
            run=run_snapshot,
            reviews=records,
            expected_run_id=dispatch.run_id,
            expected_run_attempt=dispatch.run_attempt,
        )

    @property
    def _repo_parts(self) -> tuple[str, str]:
        owner, repository = self._config.repository.split("/", 1)
        return owner, repository

    def _require_intent(self, intent: ApprovalIntent) -> None:
        if intent.repository != self._config.repository:
            raise ApprovalAdapterError("approval request repository is not configured")
        if intent.ref != self._config.default_branch:
            raise ApprovalAdapterError("approval request must target the protected default branch")

    def _verify_issue_binding(self, intent: ApprovalIntent) -> None:
        owner, repository_name = self._repo_parts
        path = (
            f"/repos/{quote(owner, safe='')}/{quote(repository_name, safe='')}"
            f"/issues/{intent.issue_number}"
        )
        issue = cast(_IssueResponse, self._transport.request("GET", path, _IssueResponse))
        expected_url = f"https://github.com/{self._config.repository}/issues/{intent.issue_number}"
        expected_marker = f"<!-- factory-work-item-id: {intent.work_item_id} -->"
        if issue.number != intent.issue_number or str(issue.html_url) != expected_url:
            raise ApprovalAdapterError(
                "ALM issue identity does not match the configured repository"
            )
        if issue.pull_request is not None or issue.state != "open":
            raise ApprovalAdapterError("approval target must be an open GitHub issue")
        if issue.body is None or expected_marker not in issue.body.splitlines():
            raise ApprovalAdapterError("ALM issue is not canonically linked to the work item")

    def _verify_environment_configuration(self, intent: ApprovalIntent) -> None:
        owner, repository_name = self._repo_parts
        base = f"/repos/{quote(owner, safe='')}/{quote(repository_name, safe='')}/environments"
        for reviewer in self._config.reviewers:
            environment_name = f"{intent.gate}-{reviewer.login}"
            environment_path = f"{base}/{quote(environment_name, safe='')}"
            environment = cast(_EnvironmentResponse, self._transport.request(
                "GET", environment_path, _EnvironmentResponse,
            ))
            reviewer_rules = [
                rule for rule in environment.protection_rules
                if rule.type == "required_reviewers"
            ]
            if len(reviewer_rules) != 1:
                raise ApprovalAdapterError("Environment must have one required-reviewer rule")
            rule = reviewer_rules[0]
            if rule.prevent_self_review is not True:
                raise ApprovalAdapterError("Environment must prevent self-review")
            if (
                len(rule.reviewers) != 1
                or rule.reviewers[0].type != "User"
                or rule.reviewers[0].reviewer.login.casefold() != reviewer.login.casefold()
                or rule.reviewers[0].reviewer.id != reviewer.user_id
            ):
                raise ApprovalAdapterError("Environment reviewer identity does not match policy")
            branch_policy = environment.deployment_branch_policy
            if (
                branch_policy is None
                or branch_policy.protected_branches is not True
                or branch_policy.custom_branch_policies is not False
            ):
                raise ApprovalAdapterError("Environment must allow protected branches only")
