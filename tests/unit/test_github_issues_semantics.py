"""Semantic WorkItemPort regressions for the GitHub Issues adapter."""

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
)
from nokinc_factory.domain.states import StaleTransition, Transition, WorkItemState
from nokinc_factory.domain.story import (
    ApprovedIntentDelta,
    BusinessReady,
    ContainmentDelta,
    DataClassification,
    NonFunctionalTarget,
    ObservabilitySpec,
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


def _issue(
    *labels: str,
    number: int = 17,
    repository_url: str = "https://api.github.com/repos/acme/factory",
) -> GitHubIssue:
    return GitHubIssue(
        number=number,
        html_url=f"https://github.com/acme/factory/issues/{number}",
        repository_url=repository_url,
        labels=[GitHubLabel(name=label) for label in labels],
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
        observability=ObservabilitySpec(spans=["refund.validate"], metrics=[]),
        approved_intent_delta=ApprovedIntentDelta(checked=True),
        containment_delta=ContainmentDelta(),
        rollback_strategy=RollbackStrategy.FEATURE_FLAG,
        rollback_justification="Disable the feature flag",
        deployment_impact="No infrastructure change",
        final_tier=RiskTier.T2,
        changeset_id="changeset-1",
    )


def _transition(expected: WorkItemState = WorkItemState.BUSINESS_READY) -> Transition:
    return Transition(
        work_item_id="17",
        workflow_run_id="workflow-1",
        transition_id="transition-1",
        expected_current=expected,
        target=WorkItemState.DESIGNING,
        event_id="event-1",
        actor="user:1",
        approval_id="approval-1",
        occurred_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
    )


def test_create_story_requires_business_ready_evidence_and_maps_work_item_ref() -> None:
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        transport=FakeTransport(_issue("story", "stage:business-ready", number=71)),
    )

    result = adapter.create_story(_story())

    assert (result.id, result.url, result.state) == (
        "71",
        "https://github.com/acme/factory/issues/71",
        WorkItemState.BUSINESS_READY,
    )


def test_create_story_rejects_response_without_business_ready_evidence() -> None:
    adapter = GitHubIssuesAdapter(
        "acme", "factory", "token", transport=FakeTransport(_issue("story"))
    )

    with pytest.raises(GitHubApiError, match="created issue response.*lifecycle state"):
        adapter.create_story(_story())


@pytest.mark.parametrize("labels", [("design",), ("design", "stage:business-ready")])
def test_create_design_rejects_missing_or_wrong_solution_ready_evidence(
    labels: tuple[str, ...],
) -> None:
    adapter = GitHubIssuesAdapter(
        "acme", "factory", "token", transport=FakeTransport(_issue(*labels))
    )

    with pytest.raises(GitHubApiError, match="created issue response.*lifecycle state"):
        adapter.create_design(_design())


def test_stale_transition_performs_only_authoritative_get() -> None:
    transport = FakeTransport(_issue("stage:solution-ready"))
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(StaleTransition):
        adapter.apply(_transition())

    assert transport.calls == [("GET", "/repos/acme/factory/issues/17", GitHubIssue, None)]


def test_capabilities_remain_conservative() -> None:
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=FakeTransport())

    assert adapter.capabilities().model_dump() == {
        "verified_identity": False,
        "separation_of_duties": False,
        "immutable_audit": False,
        "time_bound": False,
    }


def test_comment_uses_exact_endpoint_and_owned_payload() -> None:
    transport = FakeTransport(GitHubCommentResponse(id=99))
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    adapter.comment("17", "The story is ready.")

    assert transport.calls == [
        (
            "POST",
            "/repos/acme/factory/issues/17/comments",
            GitHubCommentResponse,
            GitHubCommentPayload(body="The story is ready."),
        )
    ]


def test_create_payloads_remain_owned_provider_dtos() -> None:
    transport = FakeTransport(
        _issue("story", "stage:business-ready"),
        _issue("design", "stage:solution-ready"),
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    adapter.create_story(_story())
    adapter.create_design(_design())

    assert isinstance(transport.calls[0][3], CreateStoryPayload)
    assert isinstance(transport.calls[1][3], CreateDesignPayload)
