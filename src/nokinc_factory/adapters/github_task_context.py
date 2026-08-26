"""GitHub CLI TaskContext loader for deterministic Slice A preflight."""

from __future__ import annotations

import json
import re
from subprocess import CalledProcessError, run
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nokinc_factory.domain.preflight import TaskContext
from nokinc_factory.ports.task_context import (
    InvalidTaskContextId,
    TaskContextAuthenticationError,
    TaskContextNotFound,
    TaskContextProviderError,
    TaskContextRepositoryMismatch,
)

_ISSUE_NUMBER_PATTERN = re.compile(r"[1-9][0-9]*")
_WEB_HOST_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")


class GitHubCliCommandError(RuntimeError):
    """Raised when the GitHub CLI cannot satisfy one provider request."""


class GitHubCliRunner(Protocol):
    """Narrow GitHub CLI boundary for loading issue data as untrusted JSON."""

    def run(self, arguments: tuple[str, ...]) -> bytes:
        """Return CLI stdout or raise a provider boundary diagnostic."""
        ...


class GitHubTaskContextLabel(BaseModel):
    """Owned label representation from GitHub issue JSON."""

    model_config = ConfigDict(strict=True, extra="ignore")

    name: str = Field(min_length=1)


class GitHubTaskContextIssue(BaseModel):
    """Owned issue data required for authoritative preflight TaskContext."""

    model_config = ConfigDict(strict=True, extra="ignore")

    number: int = Field(gt=0)
    title: str = Field(min_length=1)
    body: str | None
    labels: list[GitHubTaskContextLabel]
    url: str = Field(min_length=1)


class SubprocessGitHubCliRunner:
    """Run GitHub CLI requests without interpreting issue text as commands."""

    def __init__(self, executable: str = "gh") -> None:
        self._executable = executable

    def run(self, arguments: tuple[str, ...]) -> bytes:
        try:
            completed = run([self._executable, *arguments], capture_output=True, check=True)
        except FileNotFoundError as exc:
            raise GitHubCliCommandError("GitHub CLI executable is unavailable") from exc
        except CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace").strip()
            raise GitHubCliCommandError(stderr or "GitHub CLI command failed") from exc
        return completed.stdout


class GitHubIssueTaskContextLoader:
    """Load one explicit GitHub issue as authoritative, non-executable review data."""

    def __init__(
        self,
        repository: str,
        *,
        runner: GitHubCliRunner | None = None,
        web_host: str = "github.com",
    ) -> None:
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            raise ValueError("GitHub repository must be owner/name")
        if not _WEB_HOST_PATTERN.fullmatch(web_host):
            raise ValueError("GitHub web host must be a hostname")
        self._repository = repository
        self._runner = runner or SubprocessGitHubCliRunner()
        self._web_host = web_host.casefold()

    def load(self, work_item_id: str) -> TaskContext:
        number = _parse_issue_number(work_item_id)
        try:
            raw_issue = self._runner.run(
                (
                    "issue",
                    "view",
                    str(number),
                    "--repo",
                    self._repository,
                    "--json",
                    "number,title,body,labels,url",
                )
            )
        except GitHubCliCommandError as exc:
            self._raise_classified_provider_error(exc)
        issue = _parse_issue(raw_issue)
        if issue.number != number:
            raise TaskContextRepositoryMismatch(
                f"GitHub issue identity mismatch: requested {number}, received {issue.number}"
            )
        self._require_issue_source_identity(issue.url, number)
        return TaskContext.create(
            provider="github",
            repository=self._repository,
            work_item_id=str(number),
            title=issue.title,
            body=issue.body or "",
            labels=tuple(label.name for label in issue.labels),
            source_url=issue.url,
        )

    @staticmethod
    def _raise_classified_provider_error(error: GitHubCliCommandError) -> None:
        message = str(error)
        normalized = message.casefold()
        if any(token in normalized for token in ("not found", "could not resolve", "http 404")):
            raise TaskContextNotFound("Authoritative TaskContext was not found") from error
        authentication_tokens = (
            "authentication",
            "not logged",
            "auth login",
            "http 401",
            "http 403",
        )
        if any(token in normalized for token in authentication_tokens):
            raise TaskContextAuthenticationError(
                "GitHub authentication/access is unavailable"
            ) from error
        raise TaskContextProviderError("GitHub TaskContext provider request failed") from error

    def _require_issue_source_identity(self, source_url: str, number: int) -> None:
        try:
            parts = urlsplit(source_url)
        except ValueError as exc:
            raise TaskContextRepositoryMismatch(
                "GitHub TaskContext source URL is invalid"
            ) from exc
        owner, repository = self._repository.split("/", maxsplit=1)
        expected_path = f"/{owner}/{repository}/issues/{number}"
        if (
            parts.scheme != "https"
            or parts.hostname != self._web_host
            or parts.username is not None
            or parts.password is not None
            or "?" in source_url
            or "#" in source_url
            or parts.path != expected_path
        ):
            raise TaskContextRepositoryMismatch(
                "GitHub TaskContext source URL does not match configured source identity"
            )


def _parse_issue_number(work_item_id: str) -> int:
    if not isinstance(work_item_id, str) or _ISSUE_NUMBER_PATTERN.fullmatch(work_item_id) is None:
        raise InvalidTaskContextId(
            "TaskContext work item id must be a canonical positive issue number"
        )
    try:
        return int(work_item_id)
    except ValueError as exc:
        raise InvalidTaskContextId(
            "TaskContext work item id must be a canonical positive issue number"
        ) from exc


def _parse_issue(raw_issue: bytes) -> GitHubTaskContextIssue:
    try:
        decoded_issue = raw_issue.decode("utf-8")
        response_data = cast(
            object,
            json.loads(
                decoded_issue,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_object_keys,
            ),
        )
        return GitHubTaskContextIssue.model_validate(response_data)
    except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise TaskContextProviderError("GitHub TaskContext response is invalid") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    object_data: dict[str, Any] = {}
    for key, value in pairs:
        if key in object_data:
            raise ValueError(f"duplicate JSON object key: {key}")
        object_data[key] = value
    return object_data