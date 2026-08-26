"""Case-normalized lifecycle and provider issue-identity regressions."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from nokinc_factory.adapters.github_issues import (
    GitHubApiError,
    GitHubIssue,
    GitHubIssuesAdapter,
    GitHubLabel,
    InvalidLifecycleLabel,
    LifecycleLabelMutationResponse,
)
from nokinc_factory.domain.states import Transition, WorkItemState


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
    number: int = 17,
    *labels: str,
    repository_url: str = "https://api.github.com/repos/acme/factory",
) -> GitHubIssue:
    return GitHubIssue(
        number=number,
        html_url=f"https://github.com/acme/factory/issues/{number}",
        repository_url=repository_url,
        labels=[GitHubLabel(name=label) for label in labels],
    )


def _transition(work_item_id: str = "17") -> Transition:
    return Transition(
        work_item_id=work_item_id,
        workflow_run_id="workflow-1",
        transition_id="transition-1",
        expected_current=WorkItemState.BUSINESS_READY,
        target=WorkItemState.DESIGNING,
        event_id="event-1",
        actor="user:1",
        approval_id="approval-1",
        occurred_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
    )


def test_mixed_case_lifecycle_label_is_accepted() -> None:
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        transport=FakeTransport(_issue(17, "Stage:Business-Ready")),
    )

    assert adapter.get_state("17") is WorkItemState.BUSINESS_READY


def test_mixed_case_gate_control_is_not_a_lifecycle_state() -> None:
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        transport=FakeTransport(
            _issue(17, "STAGE:BUSINESS-READY", "Stage:Gate-1-Approved")
        ),
    )

    assert adapter.get_state("17") is WorkItemState.BUSINESS_READY


def test_mixed_case_unknown_stage_label_fails_closed() -> None:
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        transport=FakeTransport(_issue(17, "stage:business-ready", "Stage:Future-State")),
    )

    with pytest.raises(InvalidLifecycleLabel, match="unknown stage"):
        adapter.get_state("17")


@pytest.mark.parametrize(
    "label",
    [
        "stage:bu\u017fine\u017f\u017f-ready",
        "\u017ftage:gate-1-approved",
    ],
)
def test_unicode_casefold_stage_confusables_fail_closed(label: str) -> None:
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        transport=FakeTransport(_issue(17, "stage:business-ready", label)),
    )

    with pytest.raises(InvalidLifecycleLabel, match="unknown stage"):
        adapter.get_state("17")


def test_case_variant_duplicate_lifecycle_labels_fail_closed() -> None:
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        transport=FakeTransport(
            _issue(17, "stage:business-ready", "Stage:Business-Ready")
        ),
    )

    with pytest.raises(InvalidLifecycleLabel, match="exactly one"):
        adapter.get_state("17")


def test_canonical_single_digit_issue_number_is_accepted() -> None:
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        transport=FakeTransport(_issue(1, "stage:business-ready")),
    )

    assert adapter.get_state("1") is WorkItemState.BUSINESS_READY


@pytest.mark.parametrize(
    "api_url",
    [
        "http://github.example.com/api/v3",
        "https:///api/v3",
        "https://" + "user" + ":" + "placeholder" + "@" + "github.example.com",
        "https://github.example.com/api/v3?query=1",
        "https://github.example.com/api/v3#fragment",
    ],
)
def test_adapter_rejects_unsafe_api_url_before_injected_transport_requests(api_url: str) -> None:
    transport = FakeTransport()

    with pytest.raises(ValueError, match="api_url"):
        GitHubIssuesAdapter("acme", "factory", "token", api_url=api_url, transport=transport)

    assert transport.calls == []


@pytest.mark.parametrize("work_item_id", ["0", "-1", "017", "+17", "17 ", "issue-17"])
@pytest.mark.parametrize("operation", ["get_state", "apply", "comment"])
def test_noncanonical_issue_ids_fail_before_any_request(
    work_item_id: str,
    operation: str,
) -> None:
    transport = FakeTransport()
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(ValueError, match="canonical positive"):
        if operation == "get_state":
            adapter.get_state(work_item_id)
        elif operation == "apply":
            adapter.apply(_transition(work_item_id))
        else:
            adapter.comment(work_item_id, "The story is ready.")

    assert transport.calls == []


def test_get_state_rejects_provider_issue_number_mismatch() -> None:
    transport = FakeTransport(_issue(18, "stage:business-ready"))
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="identity mismatch"):
        adapter.get_state("17")

    assert transport.calls == [("GET", "/repos/acme/factory/issues/17", GitHubIssue, None)]


def test_get_state_rejects_provider_repository_mismatch() -> None:
    transport = FakeTransport(
        _issue(
            17,
            "stage:business-ready",
            repository_url="https://api.github.com/repos/acme/other",
        )
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="repository"):
        adapter.get_state("17")


@pytest.mark.parametrize(
    "html_url",
    [
        "https://github.com/other/acme/factory/issues/17",
        "https://github.com/acme/other/issues/17",
        "https://evil.example/acme/factory/issues/17",
    ],
)
def test_get_state_rejects_provider_issue_web_url_repository_or_host_mismatch(
    html_url: str,
) -> None:
    issue = GitHubIssue.model_validate(
        {
            "number": 17,
            "html_url": html_url,
            "repository_url": "https://api.github.com/repos/acme/factory",
            "labels": [{"name": "stage:business-ready"}],
        }
    )
    transport = FakeTransport(issue)
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="repository"):
        adapter.get_state("17")


def test_apply_rejects_final_authoritative_reread_issue_number_mismatch() -> None:
    transport = FakeTransport(
        _issue(17, "stage:business-ready"),
        LifecycleLabelMutationResponse(
            root=[GitHubLabel(name="stage:business-ready"), GitHubLabel(name="stage:designing")]
        ),
        LifecycleLabelMutationResponse(root=[GitHubLabel(name="stage:designing")]),
        _issue(18, "stage:designing"),
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="identity mismatch"):
        adapter.apply(_transition())


def test_apply_rejects_final_authoritative_reread_repository_mismatch() -> None:
    transport = FakeTransport(
        _issue(17, "stage:business-ready"),
        LifecycleLabelMutationResponse(
            root=[GitHubLabel(name="stage:business-ready"), GitHubLabel(name="stage:designing")]
        ),
        LifecycleLabelMutationResponse(root=[GitHubLabel(name="stage:designing")]),
        _issue(
            17,
            "stage:designing",
            repository_url="https://api.github.com/repos/acme/other",
        ),
    )
    adapter = GitHubIssuesAdapter("acme", "factory", "token", transport=transport)

    with pytest.raises(GitHubApiError, match="repository"):
        adapter.apply(_transition())


def test_issue_html_url_must_match_provider_issue_number() -> None:
    with pytest.raises(ValidationError, match="issue number"):
        GitHubIssue.model_validate(
            {
                "number": 17,
                "html_url": "https://github.com/acme/factory/issues/18",
                "repository_url": "https://api.github.com/repos/acme/factory",
                "labels": [],
            }
        )


@pytest.mark.parametrize(
    "html_url",
    [
        "https://github.com/acme/factory/issues/17",
        "https://github.example.com/acme/factory/issues/17",
    ],
)
def test_issue_html_url_supports_github_and_enterprise_issue_pages(html_url: str) -> None:
    issue = GitHubIssue.model_validate(
        {
            "number": 17,
            "html_url": html_url,
            "repository_url": "https://github.example.com/api/v3/repos/acme/factory",
            "labels": [],
        }
    )

    assert str(issue.html_url) == html_url


def test_enterprise_issue_web_url_is_bound_to_enterprise_api_repository() -> None:
    issue = GitHubIssue.model_validate(
        {
            "number": 17,
            "html_url": "https://github.example.com/acme/factory/issues/17",
            "repository_url": "https://github.example.com/api/v3/repos/acme/factory",
            "labels": [{"name": "stage:business-ready"}],
        }
    )
    adapter = GitHubIssuesAdapter(
        "acme",
        "factory",
        "token",
        api_url="https://github.example.com/api/v3",
        transport=FakeTransport(issue),
    )

    assert adapter.get_state("17") is WorkItemState.BUSINESS_READY
