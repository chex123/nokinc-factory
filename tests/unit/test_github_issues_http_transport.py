"""Security regressions for GitHub Issues HTTP transport handling."""

from email.message import Message
from urllib.error import HTTPError

import pytest
from pydantic import ValidationError

from nokinc_factory.adapters.github_issues_models import GitHubIssue
from nokinc_factory.adapters.github_issues_transport import (
    GitHubApiError,
    UrllibGitHubTransport,
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
    def __init__(self, *responses: FakeResponse | HTTPError) -> None:
        self._responses = list(responses)
        self.requests: list[object] = []

    def open(self, request: object, *, timeout: float) -> FakeResponse:
        self.requests.append(request)
        response = self._responses.pop(0)
        if isinstance(response, HTTPError):
            raise response
        return response


def _issue_response() -> FakeResponse:
    return FakeResponse(
        b'{"number":17,"html_url":"https://github.com/acme/factory/issues/17",'
        b'"repository_url":"https://api.github.com/repos/acme/factory",'
        b'"labels":[{"name":"stage:business-ready"}]}'
    )


def _redirect(location: str) -> HTTPError:
    headers = Message()
    headers["Location"] = location
    return HTTPError("https://github.example.com/api/v3/issues/17", 302, "Found", headers, None)


@pytest.mark.parametrize(
    "api_url",
    [
        "http://api.github.com",
        "https:///api/v3",
        "https://" + "user" + ":" + "placeholder" + "@" + "api.github.com",
        "https://api.github.com/api/v3?query=1",
        "https://api.github.com/api/v3#fragment",
    ],
)
def test_transport_rejects_unsafe_api_urls(api_url: str) -> None:
    with pytest.raises(ValueError, match="api_url"):
        UrllibGitHubTransport("token", api_url=api_url)


def test_transport_preserves_github_enterprise_api_path_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = FakeOpener(_issue_response())
    monkeypatch.setattr(
        "nokinc_factory.adapters.github_issues_transport.build_opener",
        lambda handler: opener,
    )
    transport = UrllibGitHubTransport("token", api_url="https://github.example.com/api/v3")

    transport.request("GET", "/issues/17", GitHubIssue)

    assert opener.requests[0].full_url == "https://github.example.com/api/v3/issues/17"


def test_transport_follows_same_origin_https_redirect_with_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = FakeOpener(_redirect("/api/v3/redirected"), _issue_response())
    monkeypatch.setattr(
        "nokinc_factory.adapters.github_issues_transport.build_opener",
        lambda handler: opener,
    )
    transport = UrllibGitHubTransport("token", api_url="https://github.example.com/api/v3")

    transport.request("GET", "/issues/17", GitHubIssue)

    assert [request.full_url for request in opener.requests] == [
        "https://github.example.com/api/v3/issues/17",
        "https://github.example.com/api/v3/redirected",
    ]
    assert all(request.get_header("Authorization") == "Bearer token" for request in opener.requests)


@pytest.mark.parametrize(
    ("location", "message"),
    [
        ("https://evil.example.com/redirected", "cross-origin"),
        ("http://github.example.com/api/v3/redirected", "HTTPS"),
    ],
)
def test_transport_rejects_unsafe_redirects_without_forwarding_credentials(
    monkeypatch: pytest.MonkeyPatch,
    location: str,
    message: str,
) -> None:
    opener = FakeOpener(_redirect(location))
    monkeypatch.setattr(
        "nokinc_factory.adapters.github_issues_transport.build_opener",
        lambda handler: opener,
    )
    transport = UrllibGitHubTransport("token", api_url="https://github.example.com/api/v3")

    with pytest.raises(GitHubApiError, match=message):
        transport.request("GET", "/issues/17", GitHubIssue)

    assert len(opener.requests) == 1
    assert opener.requests[0].get_header("Authorization") == "Bearer token"


def test_transport_normalizes_malformed_utf8_to_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = FakeOpener(FakeResponse(b"\xff"))
    monkeypatch.setattr(
        "nokinc_factory.adapters.github_issues_transport.build_opener",
        lambda handler: opener,
    )

    with pytest.raises(GitHubApiError, match="invalid response"):
        UrllibGitHubTransport("token").request("GET", "/issues/17", GitHubIssue)


@pytest.mark.parametrize(
    "html_url",
    [
        "not-a-url",
        "/acme/factory/issues/17",
        "http://github.com/acme/factory/issues/17",
    ],
)
def test_issue_web_url_requires_absolute_https(html_url: str) -> None:
    with pytest.raises(ValidationError):
        GitHubIssue.model_validate(
            {
                "number": 17,
                "html_url": html_url,
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
def test_issue_web_url_accepts_github_and_enterprise_https_urls(html_url: str) -> None:
    issue = GitHubIssue.model_validate(
        {
            "number": 17,
            "html_url": html_url,
            "repository_url": "https://api.github.com/repos/acme/factory",
            "labels": [],
        }
    )

    assert str(issue.html_url) == html_url
