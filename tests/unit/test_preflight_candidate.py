"""Real-Git behavior tests for Slice A preflight candidate capture."""

import subprocess
from base64 import b64decode
from pathlib import Path

import pytest

from nokinc_factory.adapters.git_candidate import (
    GitBaseResolutionError,
    GitCandidateCapturer,
    GitExecutableUnavailable,
    NotGitRepository,
)
from nokinc_factory.domain.preflight import CandidateChangeKind, TaskContext


def _git(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "preflight@example.test")
    _git(repository, "config", "user.name", "Preflight Test")
    for name in ("committed.txt", "staged.txt", "unstaged.txt"):
        (repository / name).write_text(f"base {name}\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "base")
    return repository, _git(repository, "rev-parse", "HEAD").decode("ascii").strip()


def _task_context(
    *,
    title: str = "Factory Preflight Core",
    body: str = "Capture the complete candidate before push.",
    labels: tuple[str, ...] = ("story", "stage:business-ready"),
) -> TaskContext:
    return TaskContext.create(
        provider="github",
        repository="acme/factory",
        work_item_id="6",
        title=title,
        body=body,
        labels=labels,
        source_url="https://github.com/acme/factory/issues/6",
    )


def _capture(repository: Path, base_sha: str, context: TaskContext | None = None):
    return GitCandidateCapturer().capture(
        repository,
        base_ref=base_sha,
        task_context=context or _task_context(),
    )


def test_capture_includes_committed_base_to_head_changes(tmp_path: Path) -> None:
    repository, base_sha = _repository(tmp_path)
    (repository / "committed.txt").write_text("committed change\n", encoding="utf-8")
    _git(repository, "add", "committed.txt")
    _git(repository, "commit", "-m", "committed candidate change")

    candidate = _capture(repository, base_sha)

    assert candidate.base_sha == base_sha
    assert candidate.committed.kind is CandidateChangeKind.COMMITTED
    assert candidate.committed.paths == ("committed.txt",)
    assert b"committed change" in b64decode(candidate.committed.patch_base64)


def test_capture_includes_staged_and_unstaged_changes(tmp_path: Path) -> None:
    repository, base_sha = _repository(tmp_path)
    (repository / "staged.txt").write_text("staged change\n", encoding="utf-8")
    _git(repository, "add", "staged.txt")
    (repository / "unstaged.txt").write_text("unstaged change\n", encoding="utf-8")

    candidate = _capture(repository, base_sha)

    assert candidate.staged.kind is CandidateChangeKind.STAGED
    assert candidate.staged.paths == ("staged.txt",)
    assert candidate.unstaged.kind is CandidateChangeKind.UNSTAGED
    assert candidate.unstaged.paths == ("unstaged.txt",)


def test_capture_includes_untracked_file_content_and_binary_representation(tmp_path: Path) -> None:
    repository, base_sha = _repository(tmp_path)
    content = b"\x00preflight\xff"
    (repository / "untracked.bin").write_bytes(content)

    candidate = _capture(repository, base_sha)

    assert candidate.untracked_files[0].path == "untracked.bin"
    assert candidate.untracked_files[0].is_binary is True
    assert b64decode(candidate.untracked_files[0].content_base64) == content


def test_capture_includes_all_candidate_categories_without_mutating_git_state(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    (repository / "committed.txt").write_text("committed change\n", encoding="utf-8")
    _git(repository, "add", "committed.txt")
    _git(repository, "commit", "-m", "committed candidate change")
    (repository / "staged.txt").write_text("staged change\n", encoding="utf-8")
    _git(repository, "add", "staged.txt")
    (repository / "unstaged.txt").write_text("unstaged change\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("untracked change\n", encoding="utf-8")
    before_status = _git(repository, "status", "--porcelain=v1", "--untracked-files=all")
    before_index = _git(repository, "diff", "--cached", "--binary")

    candidate = _capture(repository, base_sha)

    assert candidate.committed.paths == ("committed.txt",)
    assert candidate.staged.paths == ("staged.txt",)
    assert candidate.unstaged.paths == ("unstaged.txt",)
    assert tuple(file.path for file in candidate.untracked_files) == ("untracked.txt",)
    assert _git(repository, "status", "--porcelain=v1", "--untracked-files=all") == before_status
    assert _git(repository, "diff", "--cached", "--binary") == before_index


def test_candidate_digest_changes_for_untracked_and_tracked_content(tmp_path: Path) -> None:
    repository, base_sha = _repository(tmp_path)
    (repository / "untracked.txt").write_text("first\n", encoding="utf-8")
    first = _capture(repository, base_sha)
    (repository / "untracked.txt").write_text("second\n", encoding="utf-8")
    second = _capture(repository, base_sha)
    (repository / "unstaged.txt").write_text("tracked change\n", encoding="utf-8")
    third = _capture(repository, base_sha)

    assert first.digest != second.digest
    assert second.digest != third.digest


def test_candidate_digest_is_stable_and_binds_task_context_content(tmp_path: Path) -> None:
    repository, base_sha = _repository(tmp_path)
    (repository / "z.txt").write_text("z\n", encoding="utf-8")
    (repository / "a.txt").write_text("a\n", encoding="utf-8")
    original = _task_context()

    first = _capture(repository, base_sha, original)
    second = _capture(repository, base_sha, original)
    changed_title = _capture(repository, base_sha, _task_context(title="Changed title"))
    changed_body = _capture(repository, base_sha, _task_context(body="Changed body"))
    changed_labels = _capture(repository, base_sha, _task_context(labels=("story", "changed")))

    assert first.digest == second.digest
    assert tuple(file.path for file in first.untracked_files) == ("a.txt", "z.txt")
    assert first.digest != changed_title.digest
    assert first.digest != changed_body.digest
    assert first.digest != changed_labels.digest


def test_not_a_git_repository_has_distinct_diagnostic(tmp_path: Path) -> None:
    with pytest.raises(NotGitRepository):
        _capture(tmp_path, "HEAD")

    with pytest.raises(NotGitRepository):
        _capture(tmp_path / "missing", "HEAD")


def test_unresolved_base_has_distinct_diagnostic(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)

    with pytest.raises(GitBaseResolutionError):
        _capture(repository, "missing-base")


def test_missing_git_executable_has_distinct_diagnostic(tmp_path: Path) -> None:
    repository, base_sha = _repository(tmp_path)
    capturer = GitCandidateCapturer(git_executable="missing-preflight-git")

    with pytest.raises(GitExecutableUnavailable):
        capturer.capture(repository, base_ref=base_sha, task_context=_task_context())