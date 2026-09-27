"""Advisory local capture (Spec Part 2), not provenance or an authorization gate.

Logical patches use Git builtin normalization, never executable helpers. Real
captures additionally bind v2 raw-worktree bytes, use one private view and two
bounded raw scans, and check metadata at command boundaries. The OS/object store
must be trusted; transient ABA mutations are not atomic isolation. Data-only
injected run(tuple, Path) implementations retain the v1 contract, without claiming
the real runner's raw observation. No caller-wide Git configuration is disabled.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired
from time import monotonic
from typing import Protocol

from nokinc_factory.adapters.git_capture_io import (
    git_arguments,
    git_environment,
    read_candidate_file,
)
from nokinc_factory.adapters.git_capture_limits import CaptureBudget, CaptureLimits
from nokinc_factory.adapters.git_capture_process import bounded_run as run
from nokinc_factory.adapters.git_capture_snapshot import GitCaptureObservation
from nokinc_factory.adapters.git_capture_view import GitCaptureSource, is_config_read
from nokinc_factory.domain.preflight import (
    CandidateChange,
    CandidateChangeKind,
    CandidateFile,
    PreflightCandidate,
    RawWorktree,
    TaskContext,
    candidate_change,
    candidate_file,
    validate_candidate_path,
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


class GitCaptureIntegrityError(GitCommandFailure):
    """Unsupported or changing evidence must not be mislabeled as a bad base ref."""


class GitCommandRunner(Protocol):
    """Narrow read-only Git subprocess boundary for candidate capture."""

    def run(self, arguments: tuple[str, ...], repository: Path) -> bytes:
        """Return command stdout or raise a specific capture diagnostic."""
        ...


class SubprocessGitCommandRunner:
    """Optional scoped lifecycle amortizes real reads; the runner protocol is unchanged."""

    def __init__(
        self, executable: str = "git", *, limits: CaptureLimits | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._executable = executable
        self.limits = limits or CaptureLimits()
        self._clock = clock
        self._observation: GitCaptureObservation | None = None
        self._stack: ExitStack | None = None
        self._budget: CaptureBudget | None = None

    @property
    def raw_worktree(self) -> RawWorktree | None:
        return self._observation.raw_worktree if self._observation is not None else None

    @contextmanager
    def capture_session(self) -> Iterator[None]:
        if self._stack is not None:
            raise GitCaptureIntegrityError("A Git runner cannot capture concurrently")
        with ExitStack() as stack:
            self._stack = stack
            self._budget = CaptureBudget(self.limits, self._clock)
            try:
                yield
                if self._observation is not None:
                    self._observation.finish(self._execute)
            except (OSError, ValueError) as exc:
                raise GitCaptureIntegrityError(str(exc)) from exc
            finally:
                self._observation = None
                self._budget = None
                self._stack = None

    def run(self, arguments: tuple[str, ...], repository: Path) -> bytes:
        try:
            if arguments == ("rev-parse", "--is-inside-work-tree"):
                return self._execute(arguments, repository)
            if self._stack is None:
                # Standalone command clients remain supported without retaining a
                # temporary view indefinitely. The capturer shares one session.
                with self.capture_session():
                    return self.run(arguments, repository)
            if self._observation is None:
                try:
                    assert self._budget is not None
                    source = GitCaptureSource.capture(repository, self._execute, self._budget)
                    view = self._stack.enter_context(source.view(self._execute))
                    self._observation = GitCaptureObservation.capture(source, view, self._budget)
                except GitCommandFailure as exc:
                    raise GitCaptureIntegrityError(
                        f"Git capture/index initialization: {exc}",
                    ) from exc
            if repository != self._observation.source.repository:
                raise ValueError("Git capture repository changed during observation")
            return self._observation.run(arguments, self._execute)
        except (OSError, ValueError) as exc:
            raise GitCaptureIntegrityError(
                str(exc) or "Unable to establish a stable Git capture"
            ) from exc

    def _execute(self, arguments: tuple[str, ...], repository: Path) -> bytes:
        try:
            timeout = self._budget.remaining() if self._budget is not None else 30.0
            if self._budget is not None:
                self._budget.commands += 1
            completed = run(
                git_arguments(self._executable, arguments),
                cwd=repository,
                capture_output=True,
                check=True,
                env=git_environment(read_config=is_config_read(arguments)),
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise GitExecutableUnavailable("Git executable is unavailable") from exc
        except TimeoutExpired as exc:
            raise GitCommandFailure("Git command timed out during capture") from exc
        except CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
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
        self._runner = runner if runner is not None else SubprocessGitCommandRunner(git_executable)

    def capture(
        self,
        repository: Path,
        *,
        base_ref: str,
        task_context: TaskContext,
    ) -> PreflightCandidate:
        task_context = TaskContext.model_validate(task_context)
        repository = repository.resolve()
        if not repository.is_dir():
            raise NotGitRepository("Path is not a Git repository")
        # Optional capability, not a new requirement on injected data-only runners.
        with ExitStack() as stack:
            session = getattr(self._runner, "capture_session", None)
            if session is not None:
                stack.enter_context(session())
            self._ensure_repository(repository)
            first = self._capture_candidate(repository, base_ref, task_context)
            second = self._capture_candidate(repository, base_ref, task_context)
            if (first != second
                    or self._resolve_commit(repository, "HEAD^{commit}") != first.head_sha):
                raise GitCommandFailure("Git candidate changed during capture; retry a stable tree")
        return first

    def _capture_candidate(
        self, repository: Path, base_ref: str, task_context: TaskContext,
    ) -> PreflightCandidate:
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
            raw_worktree=self._raw_worktree(),
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
        if not base_ref or base_ref.startswith("-"):
            raise GitBaseResolutionError("A non-empty base reference is required")
        try:
            return self._decode_sha(
                self._run(("rev-parse", "--verify", f"{base_ref}^{{commit}}"), repository)
            )
        except GitCaptureIntegrityError:
            raise
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
        raw = self._raw_worktree()
        if raw is not None:
            files_by_path = {file.path: file for file in raw.files}
            if not set(paths) <= files_by_path.keys():
                raise GitCaptureIntegrityError("Untracked inventory differs from raw observation")
            return tuple(files_by_path[path] for path in paths)
        if len(paths) > CaptureLimits().max_files:
            raise GitCaptureIntegrityError("Untracked inventory file limit exceeded")
        files = []
        remaining = CaptureLimits().max_total_bytes
        for relative_path in paths:
            try:
                file = read_candidate_file(
                    repository, relative_path,
                    max_bytes=min(CaptureLimits().max_file_bytes, remaining),
                )
                if file is None:
                    raise ValueError("Untracked file disappeared during capture")
            except (OSError, ValueError) as exc:
                raise GitCommandFailure(f"Unable to read untracked file: {relative_path}") from exc
            files.append(candidate_file(relative_path, file[0]))
            remaining -= len(file[0])
        return tuple(sorted(files, key=lambda file: file.path))

    def _raw_worktree(self) -> RawWorktree | None:
        raw = getattr(self._runner, "raw_worktree", None)
        return RawWorktree.model_validate(raw) if raw is not None else None

    def _run(self, arguments: tuple[str, ...], repository: Path) -> bytes:
        return self._runner.run(arguments, repository)

    @staticmethod
    def _decode_sha(raw: bytes) -> str:
        try:
            sha = raw.decode("ascii", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise GitCommandFailure("Git returned a non-ASCII commit identity") from exc
        if not sha:
            raise GitCommandFailure("Git returned an empty commit SHA")
        return sha

    @staticmethod
    def _decode_paths(raw: bytes) -> tuple[str, ...]:
        try:
            paths = tuple(
                validate_candidate_path(path.decode("utf-8", errors="strict"))
                for path in raw.split(b"\0") if path
            )
            if (raw and not raw.endswith(b"\0")) or len(set(paths)) != len(paths):
                raise ValueError("Noncanonical Git path inventory")
        except UnicodeDecodeError as exc:
            raise GitCommandFailure("Git returned a non-UTF-8 path") from exc
        except ValueError as exc:
            raise GitCommandFailure("Git returned a noncanonical candidate path") from exc
        return tuple(sorted(paths))