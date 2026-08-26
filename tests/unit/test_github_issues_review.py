"""Regression tests for GitHub Issues lifecycle governance findings F1-F3."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from nokinc_factory.adapters.github_issues import (
    CreateDesignPayload,
    CreateStoryPayload,
    GitHubApiError,
    GitHubCommentPayload,
    GitHubCommentResponse,
    GitHubIssue,
    GitHubIssuesAdapter,
    GitHubLabel,
    InvalidLifecycleLabel,
    LifecycleLabelMutationPayload,
    LifecycleLabelMutationResponse,
)
from nokinc_factory.domain.states import Transition, WorkItemState
from nokinc_factory.domain.story import (
    BusinessReady,
    DataClassification,
    NonFunctionalTarget,
    RiskTier,
    RollbackStrategy,
    Scenario,
    SolutionReady,
)
from nokinc_factory.domain.story import (
    TestDataNeed as StoryTestDataNeed,
)


class FakeTransport:
    def __init__(self, *responses: BaseModel) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, type[BaseModel], BaseModel | None]] = []

    def request(
        self,
        method: str,
        path: str,
        response_model: type[BaseModel],
        body: BaseModel | None = None,
    ) -> BaseModel:
        self.calls.append((method, path, response_model, body))
        response = self.responses.pop(0)
        assert isinstance(response, response_model)
        return response


def _issue(*labels: str) -> GitHubIssue:
    return GitHubIssue(
        number=17,
        html_url="https://github.com/acme/factory/issues/17",
        labels=[GitHubLabel(name=label) for label in labels],
    )


def _transition() -> Transition:
    return Transition(
        work_item_id="17",
        workflow_run_id="workflow-1",
        transition_id="transition-1",
        expected_current=WorkItemState.BUSINESS_READY,
        target=WorkItemState.DESIGNING,
        event_id="event-1",
        actor="user:1",
        approval_id="approval-1",
        occurred_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
    )


def _story() -> BusinessReady:
    return BusinessReady(
        work_item_id="story-1",
        problem_and_value="Prevent duplicate refunds",
        scope_in=["duplicate refund detection"],
        scope_out=["payment processor changes"],
        scenarios=[
            Scenario(
                name="duplicate is refused",
                gherkin="Given a duplicate refund When requested Then it is refused",
                is_failure_case=True,
            )
        ],
        business_rules=["A duplicate refund is never issued twice"],
        test_data_needs=[
            StoryTestDataNeed(description="duplicate charges", source="fixture", volume="small")
        ],
        nfr_impact=[NonFunctionalTarget(metric="latency", target="unchanged", unchanged=True)],
        data_classification=DataClassification.PAYMENT,
        preliminary_tier=RiskTier.T2,
        rough_size="S",
        size_confidence="high",
    )


def _design() -> SolutionReady:
    return SolutionReady(
        work_item_id="design-1",
        affected_repositories=["payments"],
        service_boundary_decision="Keep the decision in the payments service",
        security_design="Preserve authorization checks",
        observability={"spans": ["refund.validate"], "metrics": []},
        approved_intent_delta={"checked": True},
        containment_delta={},
        rollback_strategy=RollbackStrategy.FEATURE_FLAG,
        rollback_justification="Disable the feature flag",
        deployment_impact="No infrastructure change",
        final_tier=RiskTier.T2,
        changeset_id="changeset-1",
    )


@pytest.mark.parametrize(
    "gate_label",
    [
        "stage:gate-1-approved",
        "stage:gate-2-approved",
        "stage:gate-3-approved",
        "stage:gate-4-approved",
    ],
)
def test_permitted_gate_control_labels_are_not_lifecycle_states(gate_label: str) -> None:
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        transport=FakeTransport(_issue("stage:business-ready", gate_label)),
    )

    assert adapter.get_state("17") is WorkItemState.BUSINESS_READY


def test_unknown_stage_label_fails_closed_even_with_known_lifecycle_state() -> None:
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        transport=FakeTransport(_issue("stage:business-ready", "stage:gate-99-approved")),
    )

    with pytest.raises(InvalidLifecycleLabel, match="unknown stage"):
        adapter.get_state("17")


@pytest.mark.parametrize(
    "labels",
    [
        ("stage:business-ready", "stage:solution-ready"),
        ("stage:not-a-state",),
    ],
)
def test_multiple_or_unknown_lifecycle_stage_labels_fail_closed(labels: tuple[str, ...]) -> None:
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        transport=FakeTransport(_issue(*labels)),
    )

    with pytest.raises(InvalidLifecycleLabel):
        adapter.get_state("17")


def test_apply_uses_label_specific_operations_and_rereads_authoritative_issue() -> None:
    target_label = "stage:designing"
    transport = FakeTransport(
        _issue("story", "priority", "stage:gate-1-approved", "stage:business-ready"),
        LifecycleLabelMutationResponse(
            root=[
                GitHubLabel(name="story"),
                GitHubLabel(name="priority"),
                GitHubLabel(name="stage:gate-1-approved"),
                GitHubLabel(name="stage:business-ready"),
                GitHubLabel(name=target_label),
            ]
        ),
        LifecycleLabelMutationResponse(
            root=[
                GitHubLabel(name="story"),
                GitHubLabel(name="priority"),
                GitHubLabel(name="stage:gate-1-approved"),
                GitHubLabel(name=target_label),
            ]
        ),
        _issue("story", "priority", "stage:gate-1-approved", target_label),
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    result = adapter.apply(_transition())

    assert result.state is WorkItemState.DESIGNING
    assert transport.calls == [
        ("GET", "/repos/acme/factory/issues/17", GitHubIssue, None),
        (
            "POST",
            "/repos/acme/factory/issues/17/labels",
            LifecycleLabelMutationResponse,
            LifecycleLabelMutationPayload(labels=[target_label]),
        ),
        (
            "DELETE",
            "/repos/acme/factory/issues/17/labels/stage%3Abusiness-ready",
            LifecycleLabelMutationResponse,
            None,
        ),
        ("GET", "/repos/acme/factory/issues/17", GitHubIssue, None),
    ]
    assert all(method != "PATCH" for method, _, _, _ in transport.calls)


def test_create_operations_establish_verified_lifecycle_states_with_owned_payloads() -> None:
    transport = FakeTransport(
        _issue("story", "stage:business-ready"),
        _issue("design", "stage:solution-ready"),
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    story_ref = adapter.create_story(_story())
    design_ref = adapter.create_design(_design())

    assert story_ref.state is WorkItemState.BUSINESS_READY
    assert design_ref.state is WorkItemState.SOLUTION_READY
    assert isinstance(transport.calls[0][3], CreateStoryPayload)
    assert isinstance(transport.calls[1][3], CreateDesignPayload)


def test_apply_fails_when_label_mutation_response_omits_target() -> None:
    transport = FakeTransport(
        _issue("stage:business-ready"),
        LifecycleLabelMutationResponse(root=[GitHubLabel(name="stage:business-ready")]),
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="missing target"):
        adapter.apply(_transition())


def test_apply_fails_on_conflicting_lifecycle_label_mutation_response() -> None:
    transport = FakeTransport(
        _issue("stage:business-ready"),
        LifecycleLabelMutationResponse(
            root=[
                GitHubLabel(name="stage:business-ready"),
                GitHubLabel(name="stage:designing"),
                GitHubLabel(name="stage:solution-ready"),
            ]
        ),
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="unexpected lifecycle labels"):
        adapter.apply(_transition())


def test_apply_fails_when_authoritative_reread_has_conflicting_lifecycle_state() -> None:
    transport = FakeTransport(
        _issue("stage:business-ready"),
        LifecycleLabelMutationResponse(
            root=[GitHubLabel(name="stage:business-ready"), GitHubLabel(name="stage:designing")]
        ),
        LifecycleLabelMutationResponse(root=[GitHubLabel(name="stage:designing")]),
        _issue("stage:designing", "stage:solution-ready"),
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="no valid lifecycle state"):
        adapter.apply(_transition())


def test_create_and_comment_use_owned_payload_and_response_dtos() -> None:
    transport = FakeTransport(
        _issue("story", "stage:business-ready"),
        GitHubCommentResponse(id=99),
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    adapter.create_story(_story())
    adapter.comment("17", "The story is ready.")

    assert isinstance(transport.calls[0][3], CreateStoryPayload)
    assert isinstance(transport.calls[1][3], GitHubCommentPayload)
