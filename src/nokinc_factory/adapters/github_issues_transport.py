"""Authenticated, fail-closed HTTP transport for the GitHub Issues adapter.

Authorization headers are only sent to the configured HTTPS origin. Redirects
are handled explicitly because implicit client redirects can forward a bearer
credential to an untrusted origin. See Spec Part 1 and Part 11.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from email.message import Message
from typing import IO, Protocol, TypeVar, cast
from urllib.error import HTTPError, URLError
from urllib.parse import SplitResult, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ValidationError

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
_MAX_REDIRECTS = 3
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


class GitHubApiError(RuntimeError):
    """Raised when GitHub transport or provider data cannot be safely used."""


class GitHubTransport(Protocol):
    """Typed provider transport boundary for the GitHub Issues adapter."""

    def request(
        self,
        method: str,
        path: str,
        response_model: type[ResponseModel],
        body: BaseModel | None = None,
    ) -> ResponseModel:
        """Send one request and validate its response at the provider boundary."""
        ...


@dataclass(frozen=True)
class _Origin:
    scheme: str
    hostname: str
    port: int


class _NoRedirectHandler(HTTPRedirectHandler):
    """Surface redirects so credentials cannot be forwarded implicitly."""

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        return None


class UrllibGitHubTransport:
    """Minimal GitHub REST transport with explicit redirect safety checks."""

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
        self._api_url, self._origin = _validate_api_url(api_url)
        self._token = token
        self._timeout = timeout
        self._opener = build_opener(_NoRedirectHandler())

    def request(
        self,
        method: str,
        path: str,
        response_model: type[ResponseModel],
        body: BaseModel | None = None,
    ) -> ResponseModel:
        """Request a typed provider resource without unsafe credential redirects."""
        if not path.startswith("/"):
            raise ValueError("GitHub API path must start with '/'")
        encoded_body = None if body is None else body.model_dump_json().encode("utf-8")
        request_url = f"{self._api_url}{path}"

        for redirect_count in range(_MAX_REDIRECTS + 1):
            request = self._build_request(request_url, method, encoded_body)
            try:
                with self._opener.open(request, timeout=self._timeout) as response:
                    raw_response = response.read()
            except HTTPError as exc:
                if exc.code not in _REDIRECT_STATUS_CODES:
                    raise GitHubApiError(
                        f"GitHub API request failed with status {exc.code}"
                    ) from exc
                if redirect_count == _MAX_REDIRECTS:
                    raise GitHubApiError("GitHub API exceeded redirect limit") from exc
                location = exc.headers.get("Location") if exc.headers is not None else None
                if not location:
                    raise GitHubApiError("GitHub API redirect has no Location") from exc
                request_url = self._safe_redirect_url(request_url, location)
                continue
            except URLError as exc:
                raise GitHubApiError("GitHub API request failed") from exc

            return _parse_response(raw_response, response_model)

        raise GitHubApiError("GitHub API redirect handling failed")

    def _build_request(self, url: str, method: str, body: bytes | None) -> Request:
        return Request(
            url,
            data=body,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "nokinc-factory",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method=method,
        )

    def _safe_redirect_url(self, current_url: str, location: str) -> str:
        redirect_url = urljoin(current_url, location)
        try:
            redirect_parts = _validate_https_url(redirect_url, subject="redirect URL")
        except ValueError as exc:
            raise GitHubApiError(str(exc)) from exc
        if _origin_from_parts(redirect_parts) != self._origin:
            raise GitHubApiError("GitHub API redirect is cross-origin")
        return redirect_url


def _parse_response[ParsedResponse: BaseModel](
    raw_response: bytes,
    response_model: type[ParsedResponse],
) -> ParsedResponse:
    if not raw_response:
        raise GitHubApiError("GitHub API returned an empty response")
    try:
        decoded_response = raw_response.decode("utf-8")
        response_data = cast(JsonValue, json.loads(decoded_response))
        return response_model.model_validate(response_data)
    except (UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise GitHubApiError("GitHub API returned an invalid response") from exc


def _validate_api_url(api_url: str) -> tuple[str, _Origin]:
    url_parts = _validate_https_url(api_url, subject="api_url")
    if url_parts.query or url_parts.fragment:
        raise ValueError("api_url must not contain a query or fragment")
    return api_url.rstrip("/"), _origin_from_parts(url_parts)


def _validate_https_url(url: str, *, subject: str) -> SplitResult:
    url_parts = urlsplit(url)
    if url_parts.scheme.lower() != "https":
        raise ValueError(f"{subject} must use HTTPS")
    if url_parts.hostname is None:
        raise ValueError(f"{subject} must include a hostname")
    if url_parts.username is not None or url_parts.password is not None:
        raise ValueError(f"{subject} must not include embedded credentials")
    try:
        _origin_from_parts(url_parts)
    except ValueError as exc:
        raise ValueError(f"{subject} has an invalid port") from exc
    return url_parts


def _origin_from_parts(url_parts: SplitResult) -> _Origin:
    if url_parts.hostname is None:
        raise ValueError("URL has no hostname")
    port = url_parts.port or 443
    return _Origin(
        scheme=url_parts.scheme.lower(),
        hostname=url_parts.hostname.lower(),
        port=port,
    )
