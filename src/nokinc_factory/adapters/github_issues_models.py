"""Owned schemas for the GitHub Issues provider boundary.

The factory owns its provider formats so malformed GitHub data cannot cross
into lifecycle logic. See Spec Part 11 and the fail-closed requirements in
Part 1.
"""

from __future__ import annotations

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, RootModel, field_validator


class GitHubLabel(BaseModel):
    """A GitHub label as returned by the Issues API."""

    model_config = ConfigDict(strict=True, extra="ignore")

    name: str = Field(min_length=1)


class GitHubIssue(BaseModel):
    """The issue fields authoritative for adapter lifecycle observation."""

    model_config = ConfigDict(strict=True, extra="ignore")

    number: int = Field(gt=0)
    html_url: AnyUrl
    labels: list[GitHubLabel]

    @field_validator("html_url")
    @classmethod
    def _require_https_web_url(cls, value: AnyUrl) -> AnyUrl:
        if value.scheme != "https" or value.host is None:
            raise ValueError("html_url must be an absolute HTTPS URL")
        return value


class CreateStoryPayload(BaseModel):
    """Owned request payload for a Business Ready GitHub issue."""

    model_config = ConfigDict(strict=True, extra="forbid")

    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    labels: list[str] = Field(min_length=1)


class CreateDesignPayload(BaseModel):
    """Owned request payload for a Solution Ready GitHub issue."""

    model_config = ConfigDict(strict=True, extra="forbid")

    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    labels: list[str] = Field(min_length=1)


class LifecycleLabelMutationPayload(BaseModel):
    """Owned request payload for GitHub's label-specific add operation."""

    model_config = ConfigDict(strict=True, extra="forbid")

    labels: list[str] = Field(min_length=1)


class LifecycleLabelMutationResponse(RootModel[list[GitHubLabel]]):
    """Owned response from GitHub label-specific mutation endpoints."""

    model_config = ConfigDict(strict=True)


class GitHubCommentPayload(BaseModel):
    """Owned request payload for a GitHub issue comment."""

    model_config = ConfigDict(strict=True, extra="forbid")

    body: str = Field(min_length=1)


class GitHubCommentResponse(BaseModel):
    """Minimum owned response shape from GitHub comment creation."""

    model_config = ConfigDict(strict=True, extra="ignore")

    id: int = Field(gt=0)
