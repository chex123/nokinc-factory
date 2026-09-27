"""Immutable, revalidated local evidence (Spec Parts 1–2), not signed provenance.

Consumers must validate even existing instances: Pydantic's model_copy and
model_construct deliberately bypass validation. GitHub issue URL/path identity
is checked here; the loader remains responsible for its configured trusted host.
"""

from __future__ import annotations

import hashlib
import json
from base64 import b64decode, b64encode
from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_FROZEN = ConfigDict(strict=True, extra="forbid", frozen=True, revalidate_instances="always")


class CandidateChangeKind(StrEnum):
    """The Git comparison that produced a tracked candidate patch."""

    COMMITTED = "committed"
    STAGED = "staged"
    UNSTAGED = "unstaged"


class CandidateChange(BaseModel):
    """A binary-safe tracked patch category captured without mutating Git state."""

    model_config = _FROZEN

    kind: CandidateChangeKind
    paths: tuple[str, ...]
    patch_base64: str
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _require_integrity(self) -> CandidateChange:
        if self.paths != tuple(sorted(set(self.paths))):
            raise ValueError("Candidate paths must be sorted and unique")
        for path in self.paths:
            validate_candidate_path(path)
        _decoded_content(self.patch_base64, self.content_digest)
        return self


class CandidateFile(BaseModel):
    """A complete regular file represented as deterministic base64 content."""

    model_config = _FROZEN

    path: str = Field(min_length=1)
    content_base64: str
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    is_binary: bool

    @model_validator(mode="after")
    def _require_integrity(self) -> CandidateFile:
        validate_candidate_path(self.path)
        content = _decoded_content(self.content_base64, self.content_digest)
        if self.is_binary != (b"\x00" in content):
            raise ValueError("is_binary does not match decoded content")
        return self


class RawWorktree(BaseModel):
    """V2 companion to logical patches, not authorization or an atomic snapshot.

    Inventory: index paths, nonignored untracked files and local attribute inputs.
    A missing tracked file may have been replaced by a directory (or an ancestor
    by a file). Git builtin normalization is separate; helpers are never replayed.
    """

    model_config = _FROZEN

    version: Literal[2] = 2
    profile: Literal["git-builtins-no-helpers"] = "git-builtins-no-helpers"
    files: tuple[CandidateFile, ...]
    missing_paths: tuple[str, ...]
    executable_paths: tuple[str, ...]
    git_inputs_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _require_integrity(self) -> RawWorktree:
        present = {file.path for file in self.files}
        for paths in (tuple(file.path for file in self.files),
                      self.missing_paths, self.executable_paths):
            if paths != tuple(sorted(set(paths))):
                raise ValueError("Raw paths must be sorted and unique")
            for path in paths:
                validate_candidate_path(path)
        if present.intersection(self.missing_paths) or not set(self.executable_paths) <= present:
            raise ValueError("Inconsistent raw inventory")
        if any("/".join(path.split("/")[:i]) in present
               for path in present for i in range(1, len(path.split("/")))):
            raise ValueError("Raw files cannot also be ancestors of other files")
        if self.content_digest != _sha256(_canonical_json(
            self.model_dump(mode="json", exclude={"content_digest"}),
        )):
            raise ValueError("content_digest does not match raw worktree")
        return self

    @classmethod
    def create(
        cls, *, files: tuple[CandidateFile, ...], missing_paths: tuple[str, ...],
        executable_paths: tuple[str, ...], git_inputs_digest: str,
    ) -> RawWorktree:
        validated = tuple(CandidateFile.model_validate(file)
                          for file in sorted(files, key=lambda file: file.path))
        payload: dict[str, object] = {
            "version": 2, "profile": "git-builtins-no-helpers",
            "files": validated,
            "missing_paths": tuple(sorted(missing_paths)),
            "executable_paths": tuple(sorted(executable_paths)),
            "git_inputs_digest": git_inputs_digest,
        }
        encoded = payload | {"files": [file.model_dump(mode="json") for file in validated]}
        return cls.model_validate(payload | {"content_digest": _sha256(_canonical_json(encoded))})


class TaskContext(BaseModel):
    """Authoritative review data loaded from one explicit work item.

    Issue text is captured as data for later reviewers. It is never interpreted
    as executable instructions by this deterministic Slice A model.
    """

    model_config = _FROZEN

    provider: str = Field(min_length=1)
    repository: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    work_item_id: str = Field(pattern=r"^[1-9][0-9]*$")
    title: str = Field(min_length=1)
    body: str
    labels: tuple[str, ...]
    source_url: str
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _require_content_digest(self) -> TaskContext:
        parts = urlsplit(self.source_url)
        validate_candidate_path(self.repository)
        if (
            self.provider != "github"
            or not self.source_url.startswith("https://")
            or parts.hostname is None
            or parts.username is not None or parts.password is not None
            or parts.port is not None
            or any(c in self.source_url for c in "?#\\")
            or any(ord(c) <= 32 or ord(c) == 127 for c in self.source_url)
            or parts.path != f"/{self.repository}/issues/{self.work_item_id}"
        ):
            raise ValueError("Unsupported or inconsistent TaskContext source identity")
        if self.labels != tuple(sorted(set(self.labels))) or not all(self.labels):
            raise ValueError("TaskContext labels must be nonempty, sorted and unique")
        payload = self.model_dump(exclude={"content_digest"})
        if self.content_digest != _sha256(_canonical_json(payload)):
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
        payload: dict[str, object] = {
            "provider": provider, "repository": repository, "work_item_id": work_item_id,
            "title": title, "body": body, "labels": tuple(sorted(labels)), "source_url": source_url,
        }
        return cls.model_validate(payload | {"content_digest": _sha256(_canonical_json(payload))})


class PreflightCandidate(BaseModel):
    """Complete local candidate bound to its TaskContext and deterministic digest."""

    model_config = _FROZEN

    base_sha: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    task_context: TaskContext
    committed: CandidateChange
    staged: CandidateChange
    unstaged: CandidateChange
    untracked_files: tuple[CandidateFile, ...]
    raw_worktree: RawWorktree | None = None
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("untracked_files")
    @classmethod
    def _require_canonical_files(
        cls, files: tuple[CandidateFile, ...],
    ) -> tuple[CandidateFile, ...]:
        paths = tuple(file.path for file in files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("Untracked paths must be sorted and unique")
        return files

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
            raw_worktree=self.raw_worktree,
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
        raw_worktree: RawWorktree | None = None,
    ) -> PreflightCandidate:
        digest = candidate_digest(
            base_sha=base_sha, head_sha=head_sha, task_context=task_context,
            committed=committed, staged=staged, unstaged=unstaged, untracked_files=untracked_files,
            raw_worktree=raw_worktree,
        )
        return cls(
            base_sha=base_sha, head_sha=head_sha, task_context=task_context,
            committed=committed, staged=staged, unstaged=unstaged,
            untracked_files=tuple(sorted(untracked_files, key=lambda file: file.path)),
            raw_worktree=raw_worktree,
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
    raw_worktree: RawWorktree | None = None,
) -> str:
    """Revalidate children before binding them, including unvalidated model copies."""
    task_context = TaskContext.model_validate(task_context)
    changes = tuple(CandidateChange.model_validate(item) for item in (committed, staged, unstaged))
    if tuple(item.kind for item in changes) != tuple(CandidateChangeKind):
        raise ValueError("Candidate change categories do not match their parent slots")
    if not isinstance(untracked_files, tuple):
        raise ValueError("Untracked files must be an immutable tuple")
    files = tuple(CandidateFile.model_validate(file) for file in untracked_files)
    if len({file.path for file in files}) != len(files):
        raise ValueError("Untracked paths must be unique")
    if not all(isinstance(sha, str) and sha for sha in (base_sha, head_sha)):
        raise ValueError("Candidate commit identities must be nonempty strings")
    payload: dict[str, object] = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "task_context": {
            "provider": task_context.provider,
            "repository": task_context.repository,
            "work_item_id": task_context.work_item_id,
            "content_digest": task_context.content_digest,
        },
        "changes": [change.model_dump(mode="json") for change in changes],
        "untracked_files": [
            file.model_dump(mode="json") for file in sorted(files, key=lambda file: file.path)
        ],
    }
    if raw_worktree is not None:
        # An absent extension preserves the original helper/golden digest exactly.
        raw_worktree = RawWorktree.model_validate(raw_worktree)
        raw_files = {file.path: file for file in raw_worktree.files}
        if any(raw_files.get(file.path) != file for file in files):
            raise ValueError("Untracked content disagrees with raw worktree")
        payload["raw_worktree"] = raw_worktree.model_dump(mode="json")
    return _sha256(_canonical_json(payload))


def validate_candidate_path(path: str) -> str:
    """Reject ambiguous/escaping Git paths on both POSIX and Windows consumers."""
    if (
        any(c in path for c in "\\:") or any(ord(c) < 32 or ord(c) == 127 for c in path)
        or any(part in ("", ".", "..") or part.casefold() == ".git" or part.endswith((".", " "))
               for part in path.split("/"))
    ):
        raise ValueError("Candidate path must be a canonical repository-relative path")
    return path


def _decoded_content(encoded: str, digest: str) -> bytes:
    content = b64decode(encoded, validate=True)
    if b64encode(content).decode("ascii") != encoded or _sha256(content) != digest:
        raise ValueError("content_digest or canonical base64 does not match decoded content")
    return content


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"