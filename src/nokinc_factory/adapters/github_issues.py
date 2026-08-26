"""GitHub Issues implementation of the provider-neutral WorkItemPort.

GitHub Issues is the MVP ALM system: the ALM owns the business lifecycle and
human history, and this adapter observes and advances that lifecycle through
labels. GitHub Issues has no provider-side conditional write for label changes,
so this adapter implements a verified optimistic transition: read and validate,
mutate lifecycle labels individually, then re-read and require the authoritative
target state. It surfaces divergence fail closed for an external reconciler or
human path; it does not perform reconciliation itself. It is not atomic
compare-and-swap.

Labels cannot provide approval identity, separation of duties, or immutable
approval evidence. See Spec Part 1 and Part 11.
"""

from __future__ import annotations

import re
from typing import NoReturn
from urllib.parse import quote, urlsplit

from nokinc_factory.adapters.github_issues_lifecycle import (
    InvalidLifecycleLabel,
    canonical_label_name,
    lifecycle_label,
    lifecycle_state,
    require_lifecycle_state,
    require_mutation_lifecycle_labels,
    state_label,
)
from nokinc_factory.adapters.github_issues_models import (
    CreateDesignPayload,
    CreateStoryPayload,
    GitHubCommentPayload,
    GitHubCommentResponse,
    GitHubIssue,
    GitHubLabel,
    LifecycleLabelMutationPayload,
    LifecycleLabelMutationResponse,
)
from nokinc_factory.adapters.github_issues_transport import (
    GitHubApiError,
    GitHubTransport,
    UrllibGitHubTransport,
)
from nokinc_factory.domain.states import Transition, WorkItemState
from nokinc_factory.domain.story import BusinessReady, SolutionReady
from nokinc_factory.ports.work_item import (
    ApprovalCapabilities,
    WorkItemPort,
    WorkItemRef,
)

__all__ = [
    "CreateDesignPayload",
    "CreateStoryPayload",
    "GitHubApiError",
    "GitHubCommentPayload",
    "GitHubCommentResponse",
    "GitHubIssue",
    "GitHubIssuesAdapter",
    "GitHubLabel",
    "GitHubTransport",
    "InvalidLifecycleLabel",
    "LifecycleLabelMutationPayload",
    "LifecycleLabelMutationResponse",
    "UrllibGitHubTransport",
]


_ISSUE_NUMBER_PATTERN = re.compile(r"[1-9][0-9]*")


def _parse_issue_number(work_item_id: str) -> int:
    if not isinstance(work_item_id, str) or _ISSUE_NUMBER_PATTERN.fullmatch(work_item_id) is None:
        raise ValueError("GitHub work_item_id must be a canonical positive issue number")
    try:
        return int(work_item_id)
    except ValueError as exc:
        raise ValueError("GitHub work_item_id must be a canonical positive issue number") from exc


def _issue_ref(issue: GitHubIssue, state: WorkItemState) -> WorkItemRef:
    return WorkItemRef(id=str(issue.number), url=str(issue.html_url), state=state)


class GitHubIssuesAdapter(WorkItemPort):
    """Map factory payloads to GitHub Issues, where the ALM owns lifecycle."""

    def __init__(
        self,
        owner: str,
        repository: str,
        token: str,
        *,
        transport: GitHubTransport | None = None,
        api_url: str = "https://api.github.com",
        timeout: float = 30.0,
    ) -> None:
        if not owner or not repository:
            raise ValueError("GitHub owner and repository must not be empty")
        api_parts = urlsplit(api_url)
        api_host = api_parts.hostname
        if api_host is None:
            raise ValueError("GitHub api_url must include a hostname")
        self._owner = owner
        self._repository = repository
        self._issues_path = (
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/issues"
        )
        self._repository_url = (
            f"{api_url.rstrip('/')}/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
        )
        self._web_host = (
            "github.com" if api_host.casefold() == "api.github.com" else api_host.casefold()
        )
        self._web_issue_path = f"/{quote(owner, safe='')}/{quote(repository, safe='')}"
        self._transport = transport or UrllibGitHubTransport(
            token,
            api_url=api_url,
            timeout=timeout,
        )

    def capabilities(self) -> ApprovalCapabilities:
        """GitHub Issues labels do not establish any approval capability."""
        return ApprovalCapabilities(
            verified_identity=False,
            separation_of_duties=False,
            immutable_audit=False,
        )

    def create_story(self, story: BusinessReady) -> WorkItemRef:
        issue = self._transport.request(
            "POST",
            self._issues_path,
            GitHubIssue,
            CreateStoryPayload(
                title=f"[STORY] {story.work_item_id}",
                body=story.model_dump_json(indent=2),
                labels=["story", state_label(WorkItemState.BUSINESS_READY)],
            ),
        )
        self._require_issue_repository(issue)
        actual_state = require_lifecycle_state(
            issue,
            WorkItemState.BUSINESS_READY,
            response_name="created issue response",
            expectation_name="expected state",
        )
        return _issue_ref(issue, actual_state)

    def create_design(self, design: SolutionReady) -> WorkItemRef:
        issue = self._transport.request(
            "POST",
            self._issues_path,
            GitHubIssue,
            CreateDesignPayload(
                title=f"[DESIGN] {design.work_item_id}",
                body=design.model_dump_json(indent=2),
                labels=["design", state_label(WorkItemState.SOLUTION_READY)],
            ),
        )
        self._require_issue_repository(issue)
        actual_state = require_lifecycle_state(
            issue,
            WorkItemState.SOLUTION_READY,
            response_name="created issue response",
            expectation_name="expected state",
        )
        return _issue_ref(issue, actual_state)

    def get_state(self, work_item_id: str) -> WorkItemState:
        """Read the business lifecycle state recorded by GitHub Issues."""
        issue_number = _parse_issue_number(work_item_id)
        return lifecycle_state(self._get_issue(issue_number))

    def apply(self, transition: Transition) -> WorkItemRef:
        """Perform a verified optimistic ALM lifecycle transition.

        GitHub label mutation has no atomic provider-side CAS. The adapter checks
        the expected state before individual label mutations, then re-reads the
        authoritative issue. Divergence fails closed for an external reconciler
        or human path; this adapter does not reconcile it itself.
        """
        issue_number = _parse_issue_number(transition.work_item_id)
        issue = self._get_issue(issue_number)
        current_lifecycle_label = lifecycle_label(issue)
        current_state = lifecycle_state(issue)
        transition.validate_against(current_state)

        current_label = current_lifecycle_label.name
        target_label = state_label(transition.target)
        try:
            added_labels = self._transport.request(
                "POST",
                f"{self._issue_path(issue_number)}/labels",
                LifecycleLabelMutationResponse,
                LifecycleLabelMutationPayload(labels=[target_label]),
            )
            require_mutation_lifecycle_labels(
                added_labels,
                frozenset({canonical_label_name(current_lifecycle_label), target_label}),
                response_name="label addition response",
            )
        except (GitHubApiError, InvalidLifecycleLabel) as exc:
            self._raise_after_mutation_attempt(issue_number, transition.target, exc)

        try:
            removed_labels = self._transport.request(
                "DELETE",
                f"{self._issue_path(issue_number)}/labels/{quote(current_label, safe='')}",
                LifecycleLabelMutationResponse,
            )
            require_mutation_lifecycle_labels(
                removed_labels,
                frozenset({target_label}),
                response_name="label removal response",
            )
        except (GitHubApiError, InvalidLifecycleLabel) as exc:
            self._raise_after_mutation_attempt(issue_number, transition.target, exc)
        updated_issue = self._get_issue(issue_number)
        actual_state = require_lifecycle_state(
            updated_issue,
            transition.target,
            response_name="authoritative reread",
            expectation_name="requested target",
        )
        return _issue_ref(updated_issue, actual_state)

    def comment(self, work_item_id: str, body: str) -> None:
        issue_number = _parse_issue_number(work_item_id)
        self._transport.request(
            "POST",
            f"{self._issue_path(issue_number)}/comments",
            GitHubCommentResponse,
            GitHubCommentPayload(body=body),
        )

    def _get_issue(self, issue_number: int) -> GitHubIssue:
        issue = self._transport.request("GET", self._issue_path(issue_number), GitHubIssue)
        if issue.number != issue_number:
            raise GitHubApiError(
                f"GitHub issue identity mismatch: requested {issue_number}, received {issue.number}"
            )
        self._require_issue_repository(issue)
        return issue

    def _require_issue_repository(self, issue: GitHubIssue) -> None:
        if str(issue.repository_url).rstrip("/") != self._repository_url:
            raise GitHubApiError(
                "GitHub issue identity mismatch: response repository does not "
                "match requested repository"
            )
        if issue.html_url.host is None or issue.html_url.host.casefold() != self._web_host:
            raise GitHubApiError(
                "GitHub issue identity mismatch: response web host does not match "
                "configured repository"
            )
        issue_path = issue.html_url.path or ""
        expected_issue_path = f"{self._web_issue_path}/issues/{issue.number}"
        if issue_path != expected_issue_path:
            raise GitHubApiError(
                "GitHub issue identity mismatch: response web URL does not match "
                "requested repository"
            )

    def _raise_after_mutation_attempt(
        self,
        issue_number: int,
        target_state: WorkItemState,
        mutation_error: Exception,
    ) -> NoReturn:
        try:
            reread_issue = self._get_issue(issue_number)
            require_lifecycle_state(
                reread_issue,
                target_state,
                response_name="authoritative reread",
                expectation_name="requested target",
            )
        except GitHubApiError as reread_error:
            raise GitHubApiError(
                f"GitHub lifecycle mutation failed after authoritative reread: "
                f"{mutation_error}; authoritative reread could not establish the "
                "requested target"
            ) from reread_error
        raise GitHubApiError(
            f"GitHub lifecycle mutation failed after authoritative reread: {mutation_error}"
        ) from mutation_error


    def _issue_path(self, issue_number: int) -> str:
        return f"{self._issues_path}/{issue_number}"
