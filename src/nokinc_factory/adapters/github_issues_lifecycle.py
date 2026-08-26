"""Fail-closed GitHub Issues lifecycle-label policy.

GitHub Issues owns ALM business lifecycle state through this adapter's reserved
``stage:`` vocabulary. Provider labels are case-normalized for classification,
but non-ASCII labels that casefold into the owned vocabulary are rejected so a
confusable provider label cannot become authoritative lifecycle evidence.
"""

from __future__ import annotations

from nokinc_factory.adapters.github_issues_models import (
    GitHubIssue,
    GitHubLabel,
    LifecycleLabelMutationResponse,
)
from nokinc_factory.adapters.github_issues_transport import GitHubApiError
from nokinc_factory.domain.states import WorkItemState


class InvalidLifecycleLabel(ValueError):
    """Raised when provider stage labels cannot establish one safe lifecycle state."""


STATE_LABELS: dict[str, WorkItemState] = {
    f"stage:{state.value.lower().replace('_', '-')}": state for state in WorkItemState
}
GATE_CONTROL_LABELS: frozenset[str] = frozenset(
    {
        "stage:gate-1-approved",
        "stage:gate-2-approved",
        "stage:gate-3-approved",
        "stage:gate-4-approved",
    }
)
PERMITTED_STAGE_LABELS: frozenset[str] = frozenset(STATE_LABELS) | GATE_CONTROL_LABELS


def state_label(state: WorkItemState) -> str:
    """Return the owned lowercase lifecycle label for a domain state."""
    return f"stage:{state.value.lower().replace('_', '-')}"


def canonical_label_name(label: GitHubLabel) -> str:
    """Classify provider labels using the owned vocabulary's casefolded form."""
    return label.name.casefold()


def lifecycle_label(issue: GitHubIssue) -> GitHubLabel:
    """Return exactly one authoritative lifecycle label or fail closed.

    A mixed-case ASCII label may represent an owned stage label. A non-ASCII
    provider label that folds into ``stage:`` is untrusted: case folding alone
    must not promote a confusable external string into Factory lifecycle state.
    """
    validate_stage_namespace(issue.labels)
    labels = [label for label in issue.labels if canonical_label_name(label) in STATE_LABELS]
    if len(labels) != 1:
        raise InvalidLifecycleLabel("GitHub issue must have exactly one known lifecycle label")
    return labels[0]


def lifecycle_state(issue: GitHubIssue) -> WorkItemState:
    """Read the owned lifecycle state established by ``lifecycle_label``."""
    return STATE_LABELS[canonical_label_name(lifecycle_label(issue))]


def require_lifecycle_state(
    issue: GitHubIssue,
    expected_state: WorkItemState,
    *,
    response_name: str,
    expectation_name: str,
) -> WorkItemState:
    """Require provider lifecycle evidence to match the expected domain state."""
    try:
        actual_state = lifecycle_state(issue)
    except (GitHubApiError, InvalidLifecycleLabel) as exc:
        raise GitHubApiError(f"GitHub {response_name} has no valid lifecycle state") from exc
    if actual_state is not expected_state:
        raise GitHubApiError(
            f"GitHub {response_name} lifecycle state {actual_state.value} "
            f"does not match {expectation_name} {expected_state.value}"
        )
    return actual_state


def require_mutation_lifecycle_labels(
    response: LifecycleLabelMutationResponse,
    expected_labels: frozenset[str],
    *,
    response_name: str,
) -> None:
    """Verify individual label mutation responses before the authoritative reread."""
    validate_stage_namespace(response.root)
    lifecycle_labels = [
        canonical_name
        for canonical_name in canonical_label_names(response.root)
        if canonical_name in STATE_LABELS
    ]
    actual_labels = frozenset(lifecycle_labels)
    missing_labels = {label.casefold() for label in expected_labels} - actual_labels
    if missing_labels:
        raise GitHubApiError(
            f"GitHub {response_name} is missing target lifecycle label(s): "
            f"{', '.join(sorted(missing_labels))}"
        )
    if len(lifecycle_labels) != len(expected_labels) or actual_labels != expected_labels:
        raise GitHubApiError(f"GitHub {response_name} has unexpected lifecycle labels")


def validate_stage_namespace(labels: list[GitHubLabel]) -> None:
    """Reserve ``stage:`` for owned lifecycle and gate-control labels only."""
    unknown_stage_labels = sorted(
        {
            label.name
            for label in labels
            if canonical_label_name(label).startswith("stage:")
            and (
                not label.name.isascii()
                or canonical_label_name(label) not in PERMITTED_STAGE_LABELS
            )
        }
    )
    if unknown_stage_labels:
        raise InvalidLifecycleLabel(
            f"GitHub issue has unknown stage label(s): {', '.join(unknown_stage_labels)}"
        )


def canonical_label_names(labels: list[GitHubLabel]) -> list[str]:
    """Return provider labels normalized for policy classification."""
    return [canonical_label_name(label) for label in labels]