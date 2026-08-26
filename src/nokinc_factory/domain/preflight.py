"""Deterministic Slice A preflight data models.

These artifacts bind local candidate content to an explicit authoritative
TaskContext before any future gates or semantic review can run. A digest binds
content, not labels, so a changed working tree or changed task makes old review
evidence stale. See Spec Part 1.
"""

from __future__ import annotations

import hashlib
import json
from base64 import b64encode
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CandidateChangeKind(StrEnum):
    """The Git comparison that produced a tracked candidate patch."""

    COMMITTED = "committed"
    STAGED = "staged"
    UNSTAGED = "unstaged"


class CandidateChange(BaseModel):
    """A binary-safe tracked patch category captured without mutating Git state."""

    model_config = ConfigDict(strict=True, extra="forbid")

    kind: CandidateChangeKind
    paths: tuple[str, ...]
    patch_base64: str
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CandidateFile(BaseModel):
    """A complete untracked file represented as deterministic base64 content."""

    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1)
    content_base64: str
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    is_binary: bool


class TaskContext(BaseModel):
    """Authoritative review data loaded from one explicit work item.

    Issue text is captured as data for later reviewers. It is never interpreted
    as executable instructions by this deterministic Slice A model.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    provider: str = Field(min_length=1)
    repository: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    work_item_id: str = Field(pattern=r"^[1-9][0-9]*$")
    title: str = Field(min_length=1)
    body: str
    labels: tuple[str, ...]
    source_url: str
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("source_url")
    @classmethod
    def _require_https_source_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.hostname is None:
            raise ValueError("source_url must be an absolute HTTPS URL")
        return value

    @model_validator(mode="after")
    def _require_content_digest(self) -> TaskContext:
        if self.content_digest != _task_context_digest_payload(
            self.provider,
            self.repository,
            self.work_item_id,
            self.title,
            self.body,
            self.labels,
            self.source_url,
        ):
            raise ValueError("content_digest does not match TaskContext content")
        return self

    @classmethod
    def create(
        cls,
        *,
        provider: str,
        repository: str,
        work_item_id: str,
        title: str,
        body: str,
        labels: tuple[str, ...],
        source_url: str,
    ) -> TaskContext:
        canonical_labels = tuple(sorted(labels))
        content_digest = _task_context_digest_payload(
            provider,
            repository,
            work_item_id,
            title,
            body,
            canonical_labels,
            source_url,
        )
        return cls(
            provider=provider,
            repository=repository,
            work_item_id=work_item_id,
            title=title,
            body=body,
            labels=canonical_labels,
            source_url=source_url,
            content_digest=content_digest,
        )


class PreflightCandidate(BaseModel):
    """Complete local candidate bound to its TaskContext and deterministic digest."""

    model_config = ConfigDict(strict=True, extra="forbid")

    base_sha: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    task_context: TaskContext
    committed: CandidateChange
    staged: CandidateChange
    unstaged: CandidateChange
    untracked_files: tuple[CandidateFile, ...]
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _require_candidate_digest(self) -> PreflightCandidate:
        if self.digest != candidate_digest(
            base_sha=self.base_sha,
            head_sha=self.head_sha,
            task_context=self.task_context,
            committed=self.committed,
            staged=self.staged,
            unstaged=self.unstaged,
            untracked_files=self.untracked_files,
        ):
            raise ValueError("digest does not match candidate content")
        return self

    @classmethod
    def create(
        cls,
        *,
        base_sha: str,
        head_sha: str,
        task_context: TaskContext,
        committed: CandidateChange,
        staged: CandidateChange,
        unstaged: CandidateChange,
        untracked_files: tuple[CandidateFile, ...],
    ) -> PreflightCandidate:
        digest = candidate_digest(
            base_sha=base_sha,
            head_sha=head_sha,
            task_context=task_context,
            committed=committed,
            staged=staged,
            unstaged=unstaged,
            untracked_files=untracked_files,
        )
        return cls(
            base_sha=base_sha,
            head_sha=head_sha,
            task_context=task_context,
            committed=committed,
            staged=staged,
            unstaged=unstaged,
            untracked_files=untracked_files,
            digest=digest,
        )


def candidate_change(
    kind: CandidateChangeKind,
    paths: tuple[str, ...],
    patch: bytes,
) -> CandidateChange:
    """Create a binary-safe tracked patch category from Git's raw bytes."""
    return CandidateChange(
        kind=kind,
        paths=tuple(sorted(paths)),
        patch_base64=b64encode(patch).decode("ascii"),
        content_digest=_sha256(patch),
    )


def candidate_file(path: str, content: bytes) -> CandidateFile:
    """Create a complete untracked file representation without lossy decoding."""
    return CandidateFile(
        path=path,
        content_base64=b64encode(content).decode("ascii"),
        content_digest=_sha256(content),
        is_binary=b"\x00" in content,
    )


def candidate_digest(
    *,
    base_sha: str,
    head_sha: str,
    task_context: TaskContext,
    committed: CandidateChange,
    staged: CandidateChange,
    unstaged: CandidateChange,
    untracked_files: tuple[CandidateFile, ...],
) -> str:
    """Return a SHA-256 digest of all preflight-relevant deterministic content."""
    payload: dict[str, Any] = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "task_context": {
            "provider": task_context.provider,
            "repository": task_context.repository,
            "work_item_id": task_context.work_item_id,
            "content_digest": task_context.content_digest,
        },
        "changes": [
            _change_payload(change)
            for change in (committed, staged, unstaged)
        ],
        "untracked_files": [
            _file_payload(file)
            for file in sorted(untracked_files, key=lambda candidate: candidate.path)
        ],
    }
    return _sha256(_canonical_json(payload))


def _task_context_digest_payload(
    provider: str,
    repository: str,
    work_item_id: str,
    title: str,
    body: str,
    labels: tuple[str, ...],
    source_url: str,
) -> str:
    return _sha256(
        _canonical_json(
            {
                "provider": provider,
                "repository": repository,
                "work_item_id": work_item_id,
                "title": title,
                "body": body,
                "labels": list(labels),
                "source_url": source_url,
            }
        )
    )


def _change_payload(change: CandidateChange) -> dict[str, Any]:
    return {
        "kind": change.kind.value,
        "paths": list(change.paths),
        "patch_base64": change.patch_base64,
        "content_digest": change.content_digest,
    }


def _file_payload(file: CandidateFile) -> dict[str, Any]:
    return {
        "path": file.path,
        "content_base64": file.content_base64,
        "content_digest": file.content_digest,
        "is_binary": file.is_binary,
    }


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"