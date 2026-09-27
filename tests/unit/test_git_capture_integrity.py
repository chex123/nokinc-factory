"""A01 real, disposable Git probes; helpers only write harmless temp markers."""

import os
import stat
import subprocess
import sys
from base64 import b64decode
from pathlib import Path
from typing import Unpack

import pytest
from git_capture_integrity_helpers import RunOptions

import nokinc_factory.adapters.git_candidate as git_candidate
from nokinc_factory.adapters.git_candidate import (
    GitCandidateCapturer,
    GitCommandFailure,
    SubprocessGitCommandRunner,
)
from nokinc_factory.domain.preflight import PreflightCandidate, TaskContext


def _git(repository: Path, *arguments: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=", *arguments], cwd=repository, input=data,
        check=True, capture_output=True, timeout=15,
    ).stdout


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Integrity Test")
    _git(repository, "config", "user.email", "integrity@example.invalid")
    _git(repository, "config", "core.autocrlf", "false")
    (repository / "tracked.txt").write_bytes(b"before\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "baseline")
    return repository, _git(repository, "rev-parse", "HEAD").decode().strip()


def _context() -> TaskContext:
    return TaskContext.create(
        provider="github", repository="acme/factory", work_item_id="6", title="Integrity",
        body="Raw local candidate.", labels=(),
        source_url="https://github.com/acme/factory/issues/6",
    )


def _capture(repository: Path, base: str) -> PreflightCandidate:
    return GitCandidateCapturer().capture(repository, base_ref=base, task_context=_context())


def _helper(tmp_path: Path) -> tuple[str, Path]:
    marker = tmp_path / "helper-invoked"
    script = tmp_path / "helper.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('invoked')\n"
        "print('fixed output')\n", encoding="utf-8",
    )
    return f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"', marker


@pytest.mark.parametrize("helper_kind", ["textconv", "external", "fsmonitor", "clean", "process"])
def test_capture_never_executes_configured_helpers(tmp_path: Path, helper_kind: str) -> None:
    repository, base = _repository(tmp_path)
    (repository / ".gitattributes").write_bytes(b"tracked.txt diff=audit filter=audit\n")
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-qm", "attributes")
    command, marker = _helper(tmp_path)
    key = {
        "textconv": "diff.audit.textconv", "external": "diff.audit.command",
        "fsmonitor": "core.fsmonitor", "clean": "filter.audit.clean",
        "process": "filter.audit.process",
    }[helper_kind]
    _git(repository, "config", key, command)
    (repository / "tracked.txt").write_bytes(b"after\n")
    try:
        candidate = _capture(repository, base)
    finally:
        assert not marker.exists(), f"capture executed {helper_kind}"
    patch = b64decode(candidate.unstaged.patch_base64)
    assert b"-before" in patch and b"+after" in patch
    assert candidate.unstaged.paths == ("tracked.txt",)


@pytest.mark.parametrize("cached", [False, True])
def test_capture_binds_raw_bytes_despite_attribute_normalization(
    tmp_path: Path, cached: bool,
) -> None:
    repository, base = _repository(tmp_path)
    (repository / ".gitattributes").write_bytes(b"tracked.txt text eol=lf\n")
    _git(repository, "add", ".gitattributes")
    (repository / "tracked.txt").write_bytes(b"before\r\n")
    if cached:
        _git(repository, "add", "tracked.txt")
        _git(repository, "commit", "-qm", "normalized index cache")
    candidate = _capture(repository, base)
    assert candidate.unstaged.paths == ()
    assert b64decode(candidate.unstaged.patch_base64) == b""
    assert candidate.raw_worktree is not None
    assert candidate.raw_worktree.version == 2
    files = {file.path: file for file in candidate.raw_worktree.files}
    assert b64decode(files["tracked.txt"].content_base64) == b"before\r\n"
    (repository / "tracked.txt").write_bytes(b"before\n")
    normalized = _capture(repository, base)
    assert normalized.unstaged == candidate.unstaged
    assert normalized.digest != candidate.digest
    assert normalized.raw_worktree is not None
    assert normalized.raw_worktree.content_digest != candidate.raw_worktree.content_digest


def test_real_boundary_injects_flags_environment_and_timeout_without_mutating_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base = _repository(tmp_path)
    index = repository / ".git" / "index"
    before = index.read_bytes(), index.stat().st_mtime_ns
    calls: list[tuple[list[str], RunOptions]] = []
    native = subprocess.run

    def observed(
        arguments: list[str], **kwargs: Unpack[RunOptions],
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((arguments, kwargs))
        return native(arguments, **kwargs)

    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "diff.external")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "not-a-real-helper")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "not-a-real-helper")
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "nonexistent-index"))
    monkeypatch.setattr(git_candidate, "run", observed)
    _capture(repository, base)
    assert calls
    for arguments, kwargs in calls:
        assert "--no-optional-locks" in arguments
        assert "core.fsmonitor=false" in arguments
        assert 0 < kwargs["timeout"] <= 30
        environment = kwargs["env"]
        assert environment.get("GIT_OPTIONAL_LOCKS") == "0"
        assert "GIT_EXTERNAL_DIFF" not in environment
        assert "GIT_CONFIG_VALUE_0" not in environment
        if "diff" in arguments:
            assert "--no-textconv" in arguments and "--no-ext-diff" in arguments
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before
    assert not (repository / ".git" / "index.lock").exists()


def test_git_timeout_is_a_closed_capture_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("git", 30)

    monkeypatch.setattr(git_candidate, "run", timeout)
    with pytest.raises(GitCommandFailure, match="timed out"):
        SubprocessGitCommandRunner().run(("rev-parse", "--is-inside-work-tree"), tmp_path)


def test_capture_revalidates_task_before_any_git_access(tmp_path: Path) -> None:
    class NoGitAccess:
        def run(self, arguments: tuple[str, ...], repository: Path) -> bytes:
            pytest.fail("invalid TaskContext reached the Git boundary")

    forged = _context().model_copy(update={"body": "forged request"})
    with pytest.raises(ValueError):
        GitCandidateCapturer(runner=NoGitAccess()).capture(
            tmp_path, base_ref="HEAD", task_context=forged,
        )


@pytest.mark.parametrize("drift", ["head", "index", "content", "same-stat-content", "untracked"])
def test_default_real_capture_rejects_mid_capture_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    repository, base = _repository(tmp_path)
    content = repository / "tracked.txt"
    content.write_bytes(b"after\n")
    (repository / "untracked.txt").write_bytes(b"original\n")
    native = subprocess.run
    changed = False

    def mutate(
        arguments: list[str], **kwargs: Unpack[RunOptions],
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal changed
        result = native(arguments, **kwargs)
        if not changed and "diff" in arguments and "--cached" in arguments:
            changed = True
            if drift == "head":
                _git(repository, "commit", "--allow-empty", "-qm", "drift")
            elif drift == "index":
                _git(repository, "add", "tracked.txt")
            elif drift == "untracked":
                (repository / "untracked.txt").write_bytes(b"different\n")
            else:
                previous = content.stat()
                content.write_bytes(b"other\n")
                if drift == "same-stat-content":
                    os.utime(content, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        return result

    monkeypatch.setattr(git_candidate, "run", mutate)
    with pytest.raises(GitCommandFailure, match="changed|drift|stable"):
        _capture(repository, base)
    assert changed, "the race injector must exercise the actual subprocess boundary"


@pytest.mark.parametrize("mode", ["120000", "160000"])
@pytest.mark.parametrize("location", ["index", "head", "base"])
def test_symlink_and_submodule_modes_are_explicitly_unsupported(
    tmp_path: Path, mode: str, location: str,
) -> None:
    repository, base = _repository(tmp_path)
    oid = base if mode == "160000" else _git(
        repository, "hash-object", "-w", "--stdin", data=b"tracked.txt",
    ).decode().strip()
    _git(repository, "update-index", "--add", "--cacheinfo", f"{mode},{oid},unsupported")
    if location != "index":
        _git(repository, "commit", "-qm", "unsupported mode")
    if location == "base":
        base = _git(repository, "rev-parse", "HEAD").decode().strip()
        _git(repository, "update-index", "--force-remove", "unsupported")
        _git(repository, "commit", "-qm", "remove unsupported mode")
    with pytest.raises(GitCommandFailure, match="unsupported|Unsupported"):
        _capture(repository, base)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_hidden_tracked_content_fails_closed(tmp_path: Path, flag: str) -> None:
    repository, base = _repository(tmp_path)
    _git(repository, "update-index", flag, "tracked.txt")
    (repository / "tracked.txt").write_bytes(b"hidden change\n")
    with pytest.raises(GitCommandFailure, match="unsupported|Unsupported"):
        _capture(repository, base)


def test_same_capturer_can_observe_a_new_stable_candidate(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    capturer = GitCandidateCapturer()
    first = capturer.capture(repository, base_ref=base, task_context=_context())
    (repository / "tracked.txt").write_bytes(b"new observation\n")
    second = capturer.capture(repository, base_ref=base, task_context=_context())
    assert first.digest != second.digest
    assert b"+new observation" in b64decode(second.unstaged.patch_base64)


@pytest.mark.parametrize("tracked", [False, True])
def test_filesystem_link_modes_fail_closed_without_following_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tracked: bool,
) -> None:
    repository, base = _repository(tmp_path)
    path = (repository / ("tracked.txt" if tracked else "untracked.txt")).resolve()
    path.write_bytes(b"must not follow\n")
    native = Path.lstat

    def link_stat(current: Path) -> os.stat_result:
        result = native(current)
        if current == path:
            values = list(result)
            values[0] = stat.S_IFLNK | 0o777
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "lstat", link_stat)
    with pytest.raises(GitCommandFailure, match="Unsupported symlink"):
        _capture(repository, base)


def test_git_rename_and_binary_patches_preserve_raw_candidate_bytes(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    (repository / "tracked.txt").rename(repository / "renamed.txt")
    _git(repository, "add", "--all")
    (repository / "renamed.txt").write_bytes(b"\x00new binary\xff")
    (repository / "loose.bin").write_bytes(b"\x00untracked\xff")
    candidate = _capture(repository, base)
    assert candidate.staged.paths == ("renamed.txt", "tracked.txt")
    assert candidate.unstaged.paths == ("renamed.txt",)
    assert b"GIT binary patch" in b64decode(candidate.unstaged.patch_base64)
    assert b64decode(candidate.untracked_files[0].content_base64) == b"\x00untracked\xff"


def test_injected_real_runner_cannot_bypass_drift_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base = _repository(tmp_path)
    native = subprocess.run
    changed = False

    def drift(
        arguments: list[str], **kwargs: Unpack[RunOptions],
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal changed
        result = native(arguments, **kwargs)
        if not changed and "diff" in arguments:
            changed = True
            (repository / "tracked.txt").write_bytes(b"drift\n")
        return result

    monkeypatch.setattr(git_candidate, "run", drift)
    with pytest.raises(GitCommandFailure, match="changed"):
        GitCandidateCapturer(runner=SubprocessGitCommandRunner()).capture(
            repository, base_ref=base, task_context=_context(),
        )
    assert changed