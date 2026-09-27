"""Review-1 regressions: ordinary Git semantics and bounded raw evidence coexist."""

import subprocess
from base64 import b64decode
from collections import Counter
from pathlib import Path
from typing import Unpack

import pytest
from git_capture_integrity_helpers import ReadOptions

import nokinc_factory.adapters.git_candidate as adapter
import nokinc_factory.adapters.git_capture_snapshot as snapshot
from nokinc_factory.adapters.git_candidate import (
    GitCandidateCapturer,
    GitCaptureIntegrityError,
    SubprocessGitCommandRunner,
)
from nokinc_factory.adapters.git_capture_io import read_candidate_file
from nokinc_factory.adapters.git_capture_limits import CaptureLimits
from nokinc_factory.domain.preflight import PreflightCandidate, TaskContext


def _git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=", *arguments], cwd=repository,
        capture_output=True, check=True, timeout=15,
    ).stdout


def _repository(tmp_path: Path, *, nested: bool = False) -> tuple[Path, str]:
    repository = (tmp_path / "repository").resolve()
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Capture Review")
    _git(repository, "config", "user.email", "review@example.invalid")
    path = repository / ("item/leaf.txt" if nested else "item")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"before\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "baseline")
    return repository, _git(repository, "rev-parse", "HEAD").decode().strip()


def _context() -> TaskContext:
    return TaskContext.create(
        provider="github", repository="acme/factory", work_item_id="6", title="Review",
        body="Logical changes and raw bytes.", labels=(),
        source_url="https://github.com/acme/factory/issues/6",
    )


def _capture(repository: Path, base: str) -> PreflightCandidate:
    return GitCandidateCapturer().capture(repository, base_ref=base, task_context=_context())


@pytest.mark.parametrize("autocrlf", [None, "true", "input"])
def test_logical_eol_changes_and_raw_bytes_are_separately_bound(
    tmp_path: Path, autocrlf: str | None,
) -> None:
    repository, base = _repository(tmp_path)
    if autocrlf is not None:
        _git(repository, "config", "core.autocrlf", autocrlf)
    (repository / "item").write_bytes(b"before\r\n")
    expected = tuple(
        part.decode() for part in _git(repository, "diff", "--name-only", "-z").split(b"\0")
        if part
    )
    first = _capture(repository, base)
    assert first.unstaged.paths == expected
    assert first.raw_worktree is not None and first.raw_worktree.version == 2
    assert b64decode(first.raw_worktree.files[0].content_base64) == b"before\r\n"
    (repository / "item").write_bytes(b"before\n")
    second = _capture(repository, base)
    assert first.digest != second.digest
    assert second.raw_worktree is not None
    assert first.raw_worktree.content_digest != second.raw_worktree.content_digest


@pytest.mark.parametrize("nested", [False, True], ids=["file-to-directory", "directory-to-file"])
@pytest.mark.parametrize("state", ["unstaged", "staged", "committed"])
def test_legitimate_file_directory_replacements_are_capturable(
    tmp_path: Path, nested: bool, state: str,
) -> None:
    repository, base = _repository(tmp_path, nested=nested)
    if nested:
        (repository / "item/leaf.txt").unlink()
        (repository / "item").rmdir()
        (repository / "item").write_bytes(b"replacement\n")
        present, missing = "item", "item/leaf.txt"
    else:
        (repository / "item").unlink()
        (repository / "item").mkdir()
        (repository / "item/leaf.txt").write_bytes(b"replacement\n")
        present, missing = "item/leaf.txt", "item"
    if state != "unstaged":
        _git(repository, "add", "--all")
    if state == "committed":
        _git(repository, "commit", "-qm", "replacement")
    expected_unstaged = tuple(
        path.decode() for path in _git(repository, "diff", "--name-only", "-z").split(b"\0")
        if path
    )
    candidate = _capture(repository, base)
    assert candidate.unstaged.paths == expected_unstaged
    assert candidate.raw_worktree is not None
    assert tuple(file.path for file in candidate.raw_worktree.files) == (present,)
    assert b64decode(candidate.raw_worktree.files[0].content_base64) == b"replacement\n"
    assert candidate.raw_worktree.missing_paths == ((missing,) if state == "unstaged" else ())
    if state == "unstaged":
        assert tuple(file.path for file in candidate.untracked_files) == (present,)
    else:
        assert getattr(candidate, state).paths == ("item", "item/leaf.txt")


def test_split_index_is_an_integrity_failure_not_an_unresolved_base(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _git(repository, "update-index", "--split-index")
    assert tuple((repository / ".git").glob("sharedindex.*"))
    with pytest.raises(GitCaptureIntegrityError, match="(?i)split|index"):
        _capture(repository, base)


def test_capture_has_two_raw_scans_and_one_temporary_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base = _repository(tmp_path)
    for number in range(4):
        (repository / f"loose-{number}").write_bytes(b"raw\x00bytes")
    reads: Counter[str] = Counter()
    commands: list[tuple[str, ...]] = []
    native_read = read_candidate_file
    native_execute = SubprocessGitCommandRunner._execute

    def observed_read(
        root: Path, path: str, *, missing_ok: bool = False, **kwargs: Unpack[ReadOptions],
    ) -> tuple[bytes, int] | None:
        reads[path] += 1
        return native_read(root, path, missing_ok=missing_ok, **kwargs)

    def observed_execute(
        self: SubprocessGitCommandRunner, arguments: tuple[str, ...], root: Path,
    ) -> bytes:
        commands.append(arguments)
        return native_execute(self, arguments, root)

    monkeypatch.setattr(snapshot, "read_candidate_file", observed_read)
    monkeypatch.setattr(SubprocessGitCommandRunner, "_execute", observed_execute)
    candidate = _capture(repository, base)
    assert reads == Counter({"item": 2, **{f"loose-{number}": 2 for number in range(4)}})
    directories = {arg for command in commands for arg in command if arg.startswith("--git-dir=")}
    assert len(directories) == 1
    assert all(not Path(arg.partition("=")[2]).exists() for arg in directories)
    assert sum("write-tree" in command for command in commands) == 1
    assert sum("read-tree" in command for command in commands) == 1
    assert len(commands) <= 64
    assert all(
        f"--work-tree={repository}" not in command for command in commands if "diff" in command
    ), "Git must diff the copied worktree, not reopen live ancestors"
    assert len(candidate.untracked_files) == 4


@pytest.mark.parametrize("limit", ["max_files", "max_file_bytes", "max_total_bytes"])
def test_capture_fails_closed_at_inventory_limits(tmp_path: Path, limit: str) -> None:
    repository, base = _repository(tmp_path)
    (repository / "loose").write_bytes(b"12345678")
    limits_type = getattr(adapter, "CaptureLimits", None)
    assert limits_type is not None, "capture must expose explicit bounded limits"
    limits = limits_type(**{limit: 1 if limit == "max_files" else 7})
    runner = SubprocessGitCommandRunner(limits=limits)
    with pytest.raises(GitCaptureIntegrityError, match="(?i)limit|budget"):
        GitCandidateCapturer(runner=runner).capture(
            repository, base_ref=base, task_context=_context(),
        )


@pytest.mark.parametrize("key", ["core.autocrlf", "core.filemode"])
def test_builtin_boolean_settings_use_git_canonical_values(tmp_path: Path, key: str) -> None:
    repository, base = _repository(tmp_path)
    _git(repository, "config", key, "yes" if key == "core.autocrlf" else "off")
    assert _git(repository, "config", "--type=bool", "--get", key).strip() in (b"true", b"false")
    assert _capture(repository, base).unstaged.paths == ()


def test_ignored_attribute_input_is_bound_without_becoming_untracked(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _git(repository, "config", "core.autocrlf", "false")
    (repository / ".gitignore").write_bytes(b".gitattributes\n")
    (repository / ".gitattributes").write_bytes(b"item text eol=lf\n")
    (repository / "item").write_bytes(b"before\r\n")
    candidate = _capture(repository, base)
    assert candidate.unstaged.paths == ()
    assert tuple(file.path for file in candidate.untracked_files) == (".gitignore",)
    assert candidate.raw_worktree is not None
    assert tuple(file.path for file in candidate.raw_worktree.files) == (
        ".gitattributes", ".gitignore", "item",
    )


@pytest.mark.parametrize("seconds", [float("inf"), float("nan")])
def test_a_nonfinite_capture_deadline_cannot_disable_the_budget(seconds: float) -> None:
    with pytest.raises(ValueError, match="(?i)finite|limit|budget"):
        CaptureLimits(timeout_seconds=seconds)


def test_capture_has_a_total_deadline_with_an_injected_clock(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    limits_type = getattr(adapter, "CaptureLimits", None)
    assert limits_type is not None, "capture must bound the complete observation"
    ticks = iter((0.0, 61.0))
    runner = SubprocessGitCommandRunner(
        limits=limits_type(timeout_seconds=60), clock=lambda: next(ticks, 61.0),
    )
    with pytest.raises(GitCaptureIntegrityError, match="(?i)deadline|budget"):
        GitCandidateCapturer(runner=runner).capture(
            repository, base_ref=base, task_context=_context(),
        )