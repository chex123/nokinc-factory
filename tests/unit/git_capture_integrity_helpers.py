"""Small typed fixtures; all Git writes are confined to pytest temporary repositories."""

import subprocess
from pathlib import Path
from typing import TypedDict

from nokinc_factory.adapters.git_candidate import GitCandidateCapturer, SubprocessGitCommandRunner
from nokinc_factory.adapters.git_capture_limits import CaptureBudget
from nokinc_factory.domain.preflight import PreflightCandidate, TaskContext


class RunOptions(TypedDict):
    cwd: Path
    capture_output: bool
    check: bool
    env: dict[str, str]
    timeout: float


class ReadOptions(TypedDict, total=False):
    max_bytes: int
    budget: CaptureBudget | None


def git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=", *arguments], cwd=repository,
        check=True, capture_output=True, timeout=15,
    ).stdout


def make_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = (tmp_path / "repository").resolve()
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.name", "Final Integrity")
    git(repository, "config", "user.email", "integrity@example.invalid")
    git(repository, "config", "core.autocrlf", "false")
    (repository / "item").write_bytes(b"before\n")
    git(repository, "add", "item")
    git(repository, "commit", "-qm", "baseline")
    return repository, git(repository, "rev-parse", "HEAD").decode().strip()


def capture(
    repository: Path, base: str, *, runner: SubprocessGitCommandRunner | None = None,
) -> PreflightCandidate:
    return GitCandidateCapturer(runner=runner).capture(
        repository, base_ref=base,
        task_context=TaskContext.create(
            provider="github", repository="acme/factory", work_item_id="6", title="Final review",
            body="Bounded local evidence.", labels=(),
            source_url="https://github.com/acme/factory/issues/6",
        ),
    )