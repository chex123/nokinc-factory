"""Unit tests for the typed GitHub Issues provider transport boundary."""

from urllib.error import HTTPError, URLError

import pytest
from pydantic import BaseModel, ValidationError

from nokinc_factory.adapters.github_issues import (
    CreateStoryPayload,
    GitHubApiError,
    GitHubIssue,
    UrllibGitHubTransport,
)
from nokinc_factory.adapters.github_issues_models import (
    GitHubCommentResponse,
    GitHubLabel,
    LifecycleLabelMutationResponse,
)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class FakeOpener:
    def __init__(self, *responses: FakeResponse | Exception) -> None:
        self._responses = list(responses)

    def open(self, request: object, *, timeout: float) -> FakeResponse:
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _story_payload() -> CreateStoryPayload:
    return CreateStoryPayload(
        title="[STORY] story-1",
        body="story body",
        labels=["story", "stage:business-ready"],
    )


def test_transport_serializes_owned_payload_and_validates_issue_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[object] = []

    class RecordingOpener(FakeOpener):
        def open(self, request: object, *, timeout: float) -> FakeResponse:
            requests.append((request, timeout))
            return super().open(request, timeout=timeout)

    opener = RecordingOpener(
        FakeResponse(
            b'{"number":17,"html_url":"https://github.com/acme/factory/issues/17",'
            b'"repository_url":"https://api.github.com/repos/acme/factory",'
            b'"labels":[{"name":"stage:business-ready"}]}'
        )
    )
    monkeypatch.setattr(
        "nokinc_factory.adapters.github_issues_transport.build_opener",
        lambda handler: opener,
    )
    transport = UrllibGitHubTransport("token", api_url="https://github.test/", timeout=5.0)

    issue = transport.request("POST", "/issues", GitHubIssue, _story_payload())

    assert issue.number == 17
    request, timeout = requests[0]
    assert timeout == 5.0
    assert request.full_url == "https://github.test/issues"
    assert request.get_method() == "POST"
    assert request.data == (
        b'{"title":"[STORY] story-1","body":"story body",'
        b'"labels":["story","stage:business-ready"]}'
    )
    assert request.get_header("Authorization") == "Bearer token"
    assert request.get_header("User-agent") == "nokinc-factory"


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not-json",
        b'{"number":17,"labels":[]}',
    ],
)
def test_transport_fails_closed_for_empty_invalid_or_unowned_responses(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    opener = FakeOpener(FakeResponse(body))
    monkeypatch.setattr(
        "nokinc_factory.adapters.github_issues_transport.build_opener",
        lambda handler: opener,
    )

    with pytest.raises(GitHubApiError, match="response"):
        UrllibGitHubTransport("token").request("GET", "/issues/17", GitHubIssue)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (HTTPError("https://github.test", 500, "failure", {}, None), "status 500"),
        (URLError("offline"), "request failed"),
    ],
)
def test_transport_wraps_network_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    message: str,
) -> None:
    opener = FakeOpener(error)
    monkeypatch.setattr(
        "nokinc_factory.adapters.github_issues_transport.build_opener",
        lambda handler: opener,
    )

    with pytest.raises(GitHubApiError, match=message):
        UrllibGitHubTransport("token").request("GET", "/issues/17", GitHubIssue)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"token": ""},
        {"token": "token", "timeout": 0},
    ],
)
def test_transport_rejects_invalid_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        UrllibGitHubTransport(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "model, payload",
    [
        (
            GitHubIssue,
            {
                "number": "17",
                "html_url": "https://github.com/acme/factory/issues/17",
                "repository_url": "https://api.github.com/repos/acme/factory",
                "labels": [],
            },
        ),
        (GitHubLabel, {"name": 17}),
        (GitHubCommentResponse, {"id": "99"}),
        (LifecycleLabelMutationResponse, [{"name": 17}]),
    ],
)
def test_provider_response_dtos_reject_coerced_types(
    model: type[BaseModel],
    payload: object,
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_provider_response_dtos_accept_real_shapes_with_additional_fields() -> None:
    issue = GitHubIssue.model_validate(
        {
            "number": 17,
            "html_url": "https://github.com/acme/factory/issues/17",
            "repository_url": "https://api.github.com/repos/acme/factory",
            "state": "open",
            "labels": [{"name": "stage:business-ready", "color": "0e8a16"}],
        }
    )
    comment = GitHubCommentResponse.model_validate(
        {"id": 99, "body": "created", "user": {"login": "octocat"}}
    )

    assert issue.labels == [GitHubLabel(name="stage:business-ready")]
    assert comment.id == 99
