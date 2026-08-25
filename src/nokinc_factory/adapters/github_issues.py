"""GitHub Issues implementation of the provider-neutral WorkItemPort.

GitHub Issues is the MVP ALM system: the ALM owns the business lifecycle and
human history, and this adapter observes and advances that lifecycle through
labels. Labels cannot provide approval identity, separation of duties, or
immutable approval evidence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from nokinc_factory.domain.states import Transition, WorkItemState
from nokinc_factory.domain.story import BusinessReady, SolutionReady
from nokinc_factory.ports.work_item import (
    ApprovalCapabilities,
    WorkItemPort,
    WorkItemRef,
)

JsonObject = dict[str, object]


class GitHubApiError(RuntimeError):
    """Raised when GitHub returns an unusable response or transport error."""


class InvalidLifecycleLabel(ValueError):
    """Raised when an issue does not have exactly one known state label."""


class GitHubTransport(Protocol):
    """Small JSON transport boundary that keeps HTTP out of adapter behavior tests."""

    def request(self, method: str, path: str, body: JsonObject | None = None) -> object:
        """Send one GitHub API request and return its decoded JSON response."""
        ...


class UrllibGitHubTransport:
    """Minimal GitHub REST transport using the Python standard library."""

    def __init__(
        self,
        token: str,
        *,
        api_url: str = "https://api.github.com",
        timeout: float = 30.0,
    ) -> None:
        if not token:
            raise ValueError("GitHub token must not be empty")
        if timeout <= 0:
            raise ValueError("GitHub request timeout must be positive")
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    def request(self, method: str, path: str, body: JsonObject | None = None) -> object:
        encoded_body = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            f"{self._api_url}{path}",
            data=encoded_body,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "nokinc-factory",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                raw_response = response.read()
        except HTTPError as exc:
            raise GitHubApiError(f"GitHub API request failed with status {exc.code}") from exc
        except URLError as exc:
            raise GitHubApiError("GitHub API request failed") from exc

        if not raw_response:
            return {}
        try:
            return cast(object, json.loads(raw_response))
        except json.JSONDecodeError as exc:
            raise GitHubApiError("GitHub API returned invalid JSON") from exc


_STATE_LABELS: dict[str, WorkItemState] = {
    f"stage:{state.value.lower().replace('_', '-')}": state for state in WorkItemState
}


def _state_label(state: WorkItemState) -> str:
    return f"stage:{state.value.lower().replace('_', '-')}"


def _as_issue(response: object) -> Mapping[str, object]:
    if not isinstance(response, Mapping):
        raise GitHubApiError("GitHub API returned a non-object issue response")
    return response


def _label_names(issue: Mapping[str, object]) -> list[str]:
    labels = issue.get("labels")
    if not isinstance(labels, list):
        raise GitHubApiError("GitHub issue response has invalid labels")

    names: list[str] = []
    for label in labels:
        if not isinstance(label, Mapping) or not isinstance(label.get("name"), str):
            raise GitHubApiError("GitHub issue response has an invalid label")
        names.append(label["name"])
    return names


def _lifecycle_state(issue: Mapping[str, object]) -> WorkItemState:
    state_labels = [label for label in _label_names(issue) if label in _STATE_LABELS]
    if len(state_labels) != 1 or state_labels[0] not in _STATE_LABELS:
        raise InvalidLifecycleLabel(
            "GitHub issue must have exactly one known lifecycle label"
        )
    return _STATE_LABELS[state_labels[0]]


def _require_lifecycle_state(
    issue: Mapping[str, object],
    expected_state: WorkItemState,
    *,
    response_name: str,
    expectation_name: str,
) -> WorkItemState:
    try:
        actual_state = _lifecycle_state(issue)
    except (GitHubApiError, InvalidLifecycleLabel) as exc:
        raise GitHubApiError(
            f"GitHub {response_name} has no valid lifecycle state"
        ) from exc
    if actual_state is not expected_state:
        raise GitHubApiError(
            f"GitHub {response_name} lifecycle state {actual_state.value} "
            f"does not match {expectation_name} {expected_state.value}"
        )
    return actual_state


def _issue_ref(issue: Mapping[str, object], state: WorkItemState) -> WorkItemRef:
    number = issue.get("number")
    url = issue.get("html_url")
    if isinstance(number, bool) or not isinstance(number, (int, str)) or not str(number):
        raise GitHubApiError("GitHub issue response has no valid issue number")
    if not isinstance(url, str) or not url:
        raise GitHubApiError("GitHub issue response has no valid issue URL")
    return WorkItemRef(id=str(number), url=url, state=state)


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
        self._issues_path = (
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/issues"
        )
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
        issue = _as_issue(
            self._transport.request(
                "POST",
                self._issues_path,
                {
                    "title": f"[STORY] {story.work_item_id}",
                    "body": story.model_dump_json(indent=2),
                    "labels": ["story", _state_label(WorkItemState.BUSINESS_READY)],
                },
            )
        )
        actual_state = _require_lifecycle_state(
            issue,
            WorkItemState.BUSINESS_READY,
            response_name="created issue response",
            expectation_name="expected state",
        )
        return _issue_ref(issue, actual_state)

    def create_design(self, design: SolutionReady) -> WorkItemRef:
        issue = _as_issue(
            self._transport.request(
                "POST",
                self._issues_path,
                {
                    "title": f"[DESIGN] {design.work_item_id}",
                    "body": design.model_dump_json(indent=2),
                    "labels": ["design", _state_label(WorkItemState.SOLUTION_READY)],
                },
            )
        )
        actual_state = _require_lifecycle_state(
            issue,
            WorkItemState.SOLUTION_READY,
            response_name="created issue response",
            expectation_name="expected state",
        )
        return _issue_ref(issue, actual_state)

    def get_state(self, work_item_id: str) -> WorkItemState:
        """Read the business lifecycle state recorded by GitHub Issues."""
        return _lifecycle_state(self._get_issue(work_item_id))

    def apply(self, transition: Transition) -> WorkItemRef:
        """Optimistically update and verify an ALM lifecycle transition.

        GitHub Issue PATCH has no provider-side conditional-write primitive.
        The adapter therefore checks the expected state before mutation and
        verifies the returned issue's lifecycle label after mutation.
        """
        issue = self._get_issue(transition.work_item_id)
        current_state = _lifecycle_state(issue)
        transition.validate_against(current_state)

        labels = [label for label in _label_names(issue) if label not in _STATE_LABELS]
        labels.append(_state_label(transition.target))
        updated_issue = _as_issue(
            self._transport.request(
                "PATCH",
                self._issue_path(transition.work_item_id),
                {"labels": labels},
            )
        )
        actual_state = _require_lifecycle_state(
            updated_issue,
            transition.target,
            response_name="PATCH response",
            expectation_name="requested target",
        )
        return _issue_ref(updated_issue, actual_state)

    def comment(self, work_item_id: str, body: str) -> None:
        self._transport.request(
            "POST",
            f"{self._issue_path(work_item_id)}/comments",
            {"body": body},
        )

    def _get_issue(self, work_item_id: str) -> Mapping[str, object]:
        return _as_issue(self._transport.request("GET", self._issue_path(work_item_id)))

    def _issue_path(self, work_item_id: str) -> str:
        return f"{self._issues_path}/{quote(work_item_id, safe='')}"
