"""Deterministic, read-only local Git candidate capture for Slice A preflight."""

from __future__ import annotations

from pathlib import Path
from subprocess import CalledProcessError, run
from typing import Protocol

from nokinc_factory.domain.preflight import (
    CandidateChange,
    CandidateChangeKind,
    CandidateFile,
    PreflightCandidate,
    TaskContext,
    candidate_change,
    candidate_file,
)


class GitCaptureError(RuntimeError):
    """Base class for deterministic local Git candidate diagnostics."""


class GitExecutableUnavailable(GitCaptureError):
    """Raised when the configured Git executable cannot be started."""


class NotGitRepository(GitCaptureError):
    """Raised when the requested path is not a Git worktree."""


class GitBaseResolutionError(GitCaptureError):
    """Raised when the caller's explicit base reference cannot resolve to a commit."""


class GitCommandFailure(GitCaptureError):
    """Raised for a Git command failure not covered by a narrower diagnostic."""


class GitCommandRunner(Protocol):
    """Narrow read-only Git subprocess boundary for candidate capture."""

    def run(self, arguments: tuple[str, ...], repository: Path) -> bytes:
        """Return command stdout or raise a specific capture diagnostic."""
        ...


class SubprocessGitCommandRunner:
    """Run Git without modifying the worktree or index."""

    def __init__(self, executable: str = "git") -> None:
        self._executable = executable

    def run(self, arguments: tuple[str, ...], repository: Path) -> bytes:
        try:
            completed = run(
                [self._executable, *arguments],
                cwd=repository,
                capture_output=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise GitExecutableUnavailable("Git executable is unavailable") from exc
        except CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace").strip()
            raise GitCommandFailure(stderr or f"Git command failed: {' '.join(arguments)}") from exc
        return completed.stdout


class GitCandidateCapturer:
    """Capture committed, staged, unstaged, and untracked candidate content."""

    def __init__(
        self,
        *,
        runner: GitCommandRunner | None = None,
        git_executable: str = "git",
    ) -> None:
        self._runner = runner or SubprocessGitCommandRunner(git_executable)

    def capture(
        self,
        repository: Path,
        *,
        base_ref: str,
        task_context: TaskContext,
    ) -> PreflightCandidate:
        repository = repository.resolve()
        if not repository.is_dir():
            raise NotGitRepository("Path is not a Git repository")
        self._ensure_repository(repository)
        base_sha = self._resolve_base(repository, base_ref)
        head_sha = self._resolve_commit(repository, "HEAD^{commit}")
        committed = self._capture_change(
            repository,
            CandidateChangeKind.COMMITTED,
            ("diff", "--binary", "--full-index", "--no-ext-diff", base_sha, head_sha),
            ("diff", "--name-only", "-z", base_sha, head_sha),
        )
        staged = self._capture_change(
            repository,
            CandidateChangeKind.STAGED,
            ("diff", "--cached", "--binary", "--full-index", "--no-ext-diff"),
            ("diff", "--cached", "--name-only", "-z"),
        )
        unstaged = self._capture_change(
            repository,
            CandidateChangeKind.UNSTAGED,
            ("diff", "--binary", "--full-index", "--no-ext-diff"),
            ("diff", "--name-only", "-z"),
        )
        untracked_files = self._capture_untracked_files(repository)
        return PreflightCandidate.create(
            base_sha=base_sha,
            head_sha=head_sha,
            task_context=task_context,
            committed=committed,
            staged=staged,
            unstaged=unstaged,
            untracked_files=untracked_files,
        )

    def _ensure_repository(self, repository: Path) -> None:
        try:
            inside_work_tree = self._run(("rev-parse", "--is-inside-work-tree"), repository)
        except GitCommandFailure as exc:
            if "not a git repository" in str(exc).casefold():
                raise NotGitRepository("Path is not a Git repository") from exc
            raise
        if inside_work_tree.strip() != b"true":
            raise NotGitRepository("Path is not a Git worktree")

    def _resolve_base(self, repository: Path, base_ref: str) -> str:
        if not base_ref:
            raise GitBaseResolutionError("A non-empty base reference is required")
        try:
            return self._decode_sha(
                self._run(("rev-parse", "--verify", f"{base_ref}^{{commit}}"), repository)
            )
        except GitCommandFailure as exc:
            raise GitBaseResolutionError(f"Unable to resolve base reference: {base_ref}") from exc

    def _resolve_commit(self, repository: Path, reference: str) -> str:
        return self._decode_sha(self._run(("rev-parse", "--verify", reference), repository))

    def _capture_change(
        self,
        repository: Path,
        kind: CandidateChangeKind,
        diff_arguments: tuple[str, ...],
        paths_arguments: tuple[str, ...],
    ) -> CandidateChange:
        patch = self._run(diff_arguments, repository)
        paths = self._decode_paths(self._run(paths_arguments, repository))
        return candidate_change(kind, paths, patch)

    def _capture_untracked_files(self, repository: Path) -> tuple[CandidateFile, ...]:
        paths = self._decode_paths(
            self._run(("ls-files", "--others", "--exclude-standard", "-z"), repository)
        )
        files = []
        for relative_path in paths:
            candidate_path = (repository / relative_path).resolve()
            try:
                candidate_path.relative_to(repository)
            except ValueError as exc:
                raise GitCommandFailure("Untracked path escapes the repository") from exc
            try:
                content = candidate_path.read_bytes()
            except OSError as exc:
                raise GitCommandFailure(f"Unable to read untracked file: {relative_path}") from exc
            files.append(candidate_file(relative_path, content))
        return tuple(sorted(files, key=lambda file: file.path))

    def _run(self, arguments: tuple[str, ...], repository: Path) -> bytes:
        return self._runner.run(arguments, repository)

    @staticmethod
    def _decode_sha(raw: bytes) -> str:
        sha = raw.decode("ascii", errors="strict").strip()
        if not sha:
            raise GitCommandFailure("Git returned an empty commit SHA")
        return sha

    @staticmethod
    def _decode_paths(raw: bytes) -> tuple[str, ...]:
        try:
            paths = tuple(
                path.decode("utf-8", errors="strict") for path in raw.split(b"\0") if path
            )
        except UnicodeDecodeError as exc:
            raise GitCommandFailure("Git returned a non-UTF-8 path") from exc
        return tuple(sorted(paths))