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

from urllib.parse import quote

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


class InvalidLifecycleLabel(ValueError):
    """Raised when an issue does not have exactly one known state label."""


_STATE_LABELS: dict[str, WorkItemState] = {
    f"stage:{state.value.lower().replace('_', '-')}": state for state in WorkItemState
}
_GATE_CONTROL_LABELS: frozenset[str] = frozenset(
    {
        "stage:gate-1-approved",
        "stage:gate-2-approved",
        "stage:gate-3-approved",
        "stage:gate-4-approved",
    }
)
_PERMITTED_STAGE_LABELS: frozenset[str] = frozenset(_STATE_LABELS) | _GATE_CONTROL_LABELS


def _state_label(state: WorkItemState) -> str:
    return f"stage:{state.value.lower().replace('_', '-')}"


def _label_names(labels: list[GitHubLabel]) -> list[str]:
    return [label.name for label in labels]


def _validate_stage_namespace(labels: list[GitHubLabel]) -> None:
    unknown_stage_labels = sorted(
        {label.name for label in labels if label.name.startswith("stage:")}
        - _PERMITTED_STAGE_LABELS
    )
    if unknown_stage_labels:
        raise InvalidLifecycleLabel(
            f"GitHub issue has unknown stage label(s): {', '.join(unknown_stage_labels)}"
        )


def _lifecycle_state(issue: GitHubIssue) -> WorkItemState:
    _validate_stage_namespace(issue.labels)
    state_labels = [label for label in _label_names(issue.labels) if label in _STATE_LABELS]
    if len(state_labels) != 1:
        raise InvalidLifecycleLabel(
            "GitHub issue must have exactly one known lifecycle label"
        )
    return _STATE_LABELS[state_labels[0]]


def _require_lifecycle_state(
    issue: GitHubIssue,
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


def _issue_ref(issue: GitHubIssue, state: WorkItemState) -> WorkItemRef:
    return WorkItemRef(id=str(issue.number), url=str(issue.html_url), state=state)


def _require_mutation_lifecycle_labels(
    response: LifecycleLabelMutationResponse,
    expected_labels: frozenset[str],
    *,
    response_name: str,
) -> None:
    _validate_stage_namespace(response.root)
    lifecycle_labels = [label for label in _label_names(response.root) if label in _STATE_LABELS]
    actual_labels = frozenset(lifecycle_labels)
    missing_labels = expected_labels - actual_labels
    if missing_labels:
        raise GitHubApiError(
            f"GitHub {response_name} is missing target lifecycle label(s): "
            f"{', '.join(sorted(missing_labels))}"
        )
    if len(lifecycle_labels) != len(expected_labels) or actual_labels != expected_labels:
        raise GitHubApiError(
            f"GitHub {response_name} has unexpected lifecycle labels"
        )


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
        issue = self._transport.request(
            "POST",
            self._issues_path,
            GitHubIssue,
            CreateStoryPayload(
                title=f"[STORY] {story.work_item_id}",
                body=story.model_dump_json(indent=2),
                labels=["story", _state_label(WorkItemState.BUSINESS_READY)],
            ),
        )
        actual_state = _require_lifecycle_state(
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
                labels=["design", _state_label(WorkItemState.SOLUTION_READY)],
            ),
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
        """Perform a verified optimistic ALM lifecycle transition.

        GitHub label mutation has no atomic provider-side CAS. The adapter checks
        the expected state before individual label mutations, then re-reads the
        authoritative issue. Divergence fails closed for an external reconciler
        or human path; this adapter does not reconcile it itself.
        """
        issue = self._get_issue(transition.work_item_id)
        current_state = _lifecycle_state(issue)
        transition.validate_against(current_state)

        current_label = _state_label(current_state)
        target_label = _state_label(transition.target)
        added_labels = self._transport.request(
            "POST",
            f"{self._issue_path(transition.work_item_id)}/labels",
            LifecycleLabelMutationResponse,
            LifecycleLabelMutationPayload(labels=[target_label]),
        )
        _require_mutation_lifecycle_labels(
            added_labels,
            frozenset({current_label, target_label}),
            response_name="label addition response",
        )
        removed_labels = self._transport.request(
            "DELETE",
            f"{self._issue_path(transition.work_item_id)}/labels/{quote(current_label, safe='')}",
            LifecycleLabelMutationResponse,
        )
        _require_mutation_lifecycle_labels(
            removed_labels,
            frozenset({target_label}),
            response_name="label removal response",
        )
        updated_issue = self._get_issue(transition.work_item_id)
        actual_state = _require_lifecycle_state(
            updated_issue,
            transition.target,
            response_name="authoritative reread",
            expectation_name="requested target",
        )
        return _issue_ref(updated_issue, actual_state)

    def comment(self, work_item_id: str, body: str) -> None:
        self._transport.request(
            "POST",
            f"{self._issue_path(work_item_id)}/comments",
            GitHubCommentResponse,
            GitHubCommentPayload(body=body),
        )

    def _get_issue(self, work_item_id: str) -> GitHubIssue:
        return self._transport.request("GET", self._issue_path(work_item_id), GitHubIssue)

    def _issue_path(self, work_item_id: str) -> str:
        return f"{self._issues_path}/{quote(work_item_id, safe='')}"
