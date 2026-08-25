"""Unit contract for the GitHub Issues WorkItemPort adapter."""

from datetime import UTC, datetime
from urllib.error import HTTPError, URLError

import pytest

from nokinc_factory.adapters.github_issues import (
    GitHubApiError,
    GitHubIssuesAdapter,
    UrllibGitHubTransport,
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
from nokinc_factory.ports.work_item import WorkItemRef

JsonObject = dict[str, object]


class FakeTransport:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, JsonObject | None]] = []

    def request(self, method: str, path: str, body: JsonObject | None = None) -> object:
        self.calls.append((method, path, body))
        return self.responses.pop(0)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


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
            StoryTestDataNeed(
                description="duplicate charges",
                source="fixture",
                volume="small",
            )
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


def _transition(
    expected: WorkItemState,
    target: WorkItemState,
    *,
    work_item_id: str = "17",
    approval_id: str | None = None,
) -> Transition:
    return Transition(
        work_item_id=work_item_id,
        workflow_run_id="workflow-1",
        transition_id="transition-1",
        expected_current=expected,
        target=target,
        event_id="event-1",
        actor="user:1",
        approval_id=approval_id,
        occurred_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )


def test_create_story_maps_business_ready_to_a_github_issue() -> None:
    transport = FakeTransport(
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "labels": [{"name": "stage:business-ready"}],
        }
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    result = adapter.create_story(_story())

    assert result == WorkItemRef(
        id="17",
        url="https://github.com/acme/factory/issues/17",
        state=WorkItemState.BUSINESS_READY,
    )
    assert transport.calls == [
        (
            "POST",
            "/repos/acme/factory/issues",
            {
                "title": "[STORY] story-1",
                "body": _story().model_dump_json(indent=2),
                "labels": ["story", "stage:business-ready"],
            },
        )
    ]


def test_create_story_rejects_response_without_a_lifecycle_label() -> None:
    transport = FakeTransport(
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "labels": [{"name": "story"}],
        }
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="created issue.*lifecycle state"):
        adapter.create_story(_story())


def test_create_design_maps_solution_ready_to_a_github_issue() -> None:
    transport = FakeTransport(
        {
            "number": 18,
            "html_url": "https://github.com/acme/factory/issues/18",
            "labels": [{"name": "stage:solution-ready"}],
        }
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    result = adapter.create_design(_design())

    assert result.id == "18"
    assert result.state is WorkItemState.SOLUTION_READY
    assert transport.calls[0] == (
        "POST",
        "/repos/acme/factory/issues",
        {
            "title": "[DESIGN] design-1",
            "body": _design().model_dump_json(indent=2),
            "labels": ["design", "stage:solution-ready"],
        },
    )


@pytest.mark.parametrize(
    "labels",
    [
        [{"name": "design"}],
        [{"name": "stage:business-ready"}],
    ],
)
def test_create_design_rejects_response_without_expected_lifecycle_state(
    labels: list[JsonObject],
) -> None:
    transport = FakeTransport(
        {
            "number": 18,
            "html_url": "https://github.com/acme/factory/issues/18",
            "labels": labels,
        }
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="created issue.*lifecycle state"):
        adapter.create_design(_design())


def test_get_state_reads_the_lifecycle_label_not_github_open_state() -> None:
    transport = FakeTransport(
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "state": "closed",
            "labels": [{"name": "story"}, {"name": "stage:business-ready"}],
        }
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    assert adapter.get_state("17") is WorkItemState.BUSINESS_READY
    assert transport.calls == [("GET", "/repos/acme/factory/issues/17", None)]


def test_get_state_ignores_gate_approval_labels() -> None:
    transport = FakeTransport(
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "labels": [
                {"name": "story"},
                {"name": "stage:business-ready"},
                {"name": "stage:gate-1-approved"},
            ],
        }
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    assert adapter.get_state("17") is WorkItemState.BUSINESS_READY


@pytest.mark.parametrize(
    "labels",
    [
        [],
        [{"name": "story"}],
        [{"name": "stage:business-ready"}, {"name": "stage:solution-ready"}],
        [{"name": "stage:not-a-state"}],
    ],
)
def test_get_state_fails_closed_for_invalid_lifecycle_labels(labels: list[JsonObject]) -> None:
    transport = FakeTransport(
        {"number": 17, "html_url": "https://example.test/17", "labels": labels}
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(ValueError, match="lifecycle label"):
        adapter.get_state("17")


def test_apply_validates_then_replaces_only_the_lifecycle_label() -> None:
    transport = FakeTransport(
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "labels": [
                {"name": "story"},
                {"name": "stage:gate-1-approved"},
                {"name": "priority"},
                {"name": "stage:business-ready"},
            ],
        },
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "labels": [
                {"name": "story"},
                {"name": "stage:gate-1-approved"},
                {"name": "priority"},
                {"name": "stage:designing"},
            ],
        },
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    result = adapter.apply(
        _transition(
            WorkItemState.BUSINESS_READY,
            WorkItemState.DESIGNING,
            approval_id="approval-1",
        )
    )

    assert result == WorkItemRef(
        id="17",
        url="https://github.com/acme/factory/issues/17",
        state=WorkItemState.DESIGNING,
    )
    assert transport.calls == [
        ("GET", "/repos/acme/factory/issues/17", None),
        (
            "PATCH",
            "/repos/acme/factory/issues/17",
            {
                "labels": [
                    "story",
                    "stage:gate-1-approved",
                    "priority",
                    "stage:designing",
                ]
            },
        ),
    ]


def test_apply_rejects_patch_response_when_target_label_was_not_applied() -> None:
    transport = FakeTransport(
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "labels": [{"name": "stage:business-ready"}],
        },
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "labels": [{"name": "stage:business-ready"}],
        },
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="does not match requested target"):
        adapter.apply(
            _transition(
                WorkItemState.BUSINESS_READY,
                WorkItemState.DESIGNING,
                approval_id="approval-1",
            )
        )


def test_apply_rejects_patch_response_without_a_valid_lifecycle_state() -> None:
    transport = FakeTransport(
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "labels": [{"name": "stage:business-ready"}],
        },
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "labels": [{"name": "stage:gate-1-approved"}],
        },
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="no valid lifecycle state"):
        adapter.apply(
            _transition(
                WorkItemState.BUSINESS_READY,
                WorkItemState.DESIGNING,
                approval_id="approval-1",
            )
        )


def test_apply_does_not_mutate_after_a_stale_state_check() -> None:
    transport = FakeTransport(
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "labels": [{"name": "stage:business-ready"}],
        }
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(StaleTransition):
        adapter.apply(_transition(WorkItemState.SOLUTION_READY, WorkItemState.IMPLEMENTING))

    assert len(transport.calls) == 1


def test_comment_posts_to_the_issue_comments_endpoint() -> None:
    transport = FakeTransport({"id": 99})
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    assert adapter.comment("17", "The design is ready.") is None
    assert transport.calls == [
        (
            "POST",
            "/repos/acme/factory/issues/17/comments",
            {"body": "The design is ready."},
        )
    ]


def test_github_issues_cannot_prove_approval_capabilities() -> None:
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=FakeTransport())

    assert adapter.capabilities().model_dump() == {
        "verified_identity": False,
        "separation_of_duties": False,
        "immutable_audit": False,
        "time_bound": False,
    }


def test_urllib_transport_builds_authenticated_json_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[object] = []

    def fake_urlopen(request: object, timeout: float) -> FakeResponse:
        requests.append((request, timeout))
        return FakeResponse(b'{"ok": true}')

    monkeypatch.setattr("nokinc_factory.adapters.github_issues.urlopen", fake_urlopen)
    transport = UrllibGitHubTransport(
        "token",
        api_url="https://github.test/",
        timeout=5.0,
    )

    assert transport.request("POST", "/issues", {"title": "Test"}) == {"ok": True}
    request, timeout = requests[0]
    assert timeout == 5.0
    assert request.full_url == "https://github.test/issues"
    assert request.get_method() == "POST"
    assert request.data == b'{"title": "Test"}'
    assert request.get_header("Authorization") == "Bearer token"
    assert request.get_header("User-agent") == "nokinc-factory"


def test_urllib_transport_returns_empty_object_for_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nokinc_factory.adapters.github_issues.urlopen",
        lambda request, timeout: FakeResponse(b""),
    )

    assert UrllibGitHubTransport("token").request("POST", "/issues") == {}


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (HTTPError("https://github.test", 500, "failure", {}, None), "status 500"),
        (URLError("offline"), "request failed"),
    ],
)
def test_urllib_transport_wraps_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    message: str,
) -> None:
    def fail_urlopen(request: object, timeout: float) -> FakeResponse:
        raise error

    monkeypatch.setattr("nokinc_factory.adapters.github_issues.urlopen", fail_urlopen)

    with pytest.raises(GitHubApiError, match=message):
        UrllibGitHubTransport("token").request("GET", "/issues/17")


def test_urllib_transport_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "nokinc_factory.adapters.github_issues.urlopen",
        lambda request, timeout: FakeResponse(b"not-json"),
    )

    with pytest.raises(GitHubApiError, match="invalid JSON"):
        UrllibGitHubTransport("token").request("GET", "/issues/17")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"token": ""},
        {"token": "token", "timeout": 0},
    ],
)
def test_urllib_transport_rejects_invalid_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        UrllibGitHubTransport(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("owner, repository", [("", "factory"), ("acme", "")])
def test_adapter_rejects_empty_repository_identity(owner: str, repository: str) -> None:
    with pytest.raises(ValueError, match="owner and repository"):
        GitHubIssuesAdapter(owner, repository, "token", transport=FakeTransport())


def test_adapter_rejects_non_object_issue_response() -> None:
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=FakeTransport([]))

    with pytest.raises(GitHubApiError, match="non-object"):
        adapter.create_story(_story())


@pytest.mark.parametrize(
    "response",
    [
        {"html_url": "https://example.test/17", "labels": [{"name": "stage:business-ready"}]},
        {
            "number": True,
            "html_url": "https://example.test/17",
            "labels": [{"name": "stage:business-ready"}],
        },
        {"number": 17, "labels": [{"name": "stage:business-ready"}]},
    ],
)
def test_adapter_rejects_incomplete_issue_response(response: JsonObject) -> None:
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=FakeTransport(response))

    with pytest.raises(GitHubApiError, match="issue response"):
        adapter.create_story(_story())


@pytest.mark.parametrize("labels", [None, [{}]])
def test_adapter_rejects_malformed_label_response(labels: object) -> None:
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        transport=FakeTransport({"labels": labels}),
    )

    with pytest.raises(GitHubApiError, match="label"):
        adapter.get_state("17")
