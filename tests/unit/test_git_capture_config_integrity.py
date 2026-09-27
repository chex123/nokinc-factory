"""Compare allowlisted configuration data with native Git, not Python's HOME rules."""

import os
from base64 import b64decode
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from git_capture_integrity_helpers import capture, git, make_repository

import nokinc_factory.adapters.git_capture_view as view


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = (tmp_path / "git-home-雪").resolve()
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "test-global-config"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def _rules(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "attributes").write_bytes(b"item text eol=lf\n")
    (directory / "ignore").write_bytes(b"hidden.*\n")


def _assert_native(repository: Path, base: str) -> None:
    (repository / "item").write_bytes(b"before\r\n")
    (repository / "hidden.loose").write_bytes(b"ignored by global rules\n")
    expected_patch = git(repository, "diff", "--binary", "--full-index", "--no-ext-diff")
    expected_paths = tuple(
        item.decode() for item in git(
            repository, "ls-files", "--others", "--exclude-standard", "-z",
        ).split(b"\0") if item
    )
    candidate = capture(repository, base)
    assert b64decode(candidate.unstaged.patch_base64) == expected_patch
    assert tuple(file.path for file in candidate.untracked_files) == expected_paths
    assert candidate.raw_worktree is not None
    raw = {file.path: b64decode(file.content_base64) for file in candidate.raw_worktree.files}
    assert raw["item"] == b"before\r\n"


@pytest.mark.parametrize("xdg", [None, "", "absolute", "relative", "literal-tilde"])
def test_default_rules_follow_git_home_and_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, xdg: str | None,
) -> None:
    home = _home(tmp_path, monkeypatch)
    repository, base = make_repository(tmp_path)
    if xdg in (None, ""):
        directory = home / ".config/git"
        if xdg == "":
            monkeypatch.setenv("XDG_CONFIG_HOME", "")
    else:
        value = str(tmp_path / "xdg-雪") if xdg == "absolute" else (
            "relative-config" if xdg == "relative" else "~/literal-config"
        )
        monkeypatch.setenv("XDG_CONFIG_HOME", value)
        directory = repository / value / "git"
    _rules(directory)
    _assert_native(repository, base)


@pytest.mark.skipif(os.name != "nt", reason="requires Git for Windows HOME fallback")
def test_unset_home_uses_git_for_windows_home_not_python_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _home(tmp_path, monkeypatch)
    python_home = tmp_path / "different-python-home"
    python_home.mkdir()
    monkeypatch.delenv("HOME")
    monkeypatch.setenv("USERPROFILE", str(python_home))
    monkeypatch.setenv("HOMEDRIVE", home.drive)
    monkeypatch.setenv("HOMEPATH", str(home)[len(home.drive):])
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    assert Path.home() == python_home
    repository, base = make_repository(tmp_path)
    _rules(home / ".config/git")
    _assert_native(repository, base)


@pytest.mark.parametrize("kind", ["absolute", "relative", "tilde", "spaces", "empty"])
@pytest.mark.parametrize("origin", ["local", "global"])
def test_configured_rule_paths_use_git_path_expansion_without_stripping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, origin: str,
) -> None:
    home = _home(tmp_path, monkeypatch)
    repository, base = make_repository(tmp_path)
    _rules(home / ".config/git")
    if kind == "empty":
        prefix = ""
    elif kind == "tilde":
        prefix = "~/rules-雪"
        _rules(home / "rules-雪")
    elif kind == "absolute":
        directory = tmp_path / "absolute-雪"
        _rules(directory)
        prefix = directory.as_posix()
    else:
        prefix = " rules-雪" if kind == "spaces" else "rules-雪"
        _rules(repository / prefix)
    for key, name in (("core.attributesfile", "attributes"), ("core.excludesfile", "ignore")):
        options = ("--global",) if origin == "global" else ()
        git(repository, "config", *options, key, f"{prefix}/{name}" if prefix else "")
    _assert_native(repository, base)


def test_private_view_config_preserves_non_ascii_temporary_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _home(tmp_path, monkeypatch)
    non_ascii = tmp_path / "private-é-雪"
    non_ascii.mkdir()
    repository, base = make_repository(non_ascii)

    def temporary(*, prefix: str) -> TemporaryDirectory[str]:
        return TemporaryDirectory(prefix=prefix, dir=non_ascii)

    monkeypatch.setattr(view, "TemporaryDirectory", temporary)
    (repository / "item").write_bytes(b"after\n")
    candidate = capture(repository, base)
    assert candidate.unstaged.paths == ("item",)
    assert b"+after" in b64decode(candidate.unstaged.patch_base64)


@pytest.mark.parametrize("value", ['snow-雪/quoted"name', "é/back\tspace\bnewline\n"])
def test_generated_config_path_quoting_roundtrips_through_git(tmp_path: Path, value: str) -> None:
    config = tmp_path / "quoted-config"
    path = Path(value)
    config.write_text(f"[core]\nattributesfile={view._quote_path(path)}\n", encoding="utf-8")
    assert git(tmp_path, "config", "--file", str(config), "--null", "--get",
               "core.attributesfile") == path.as_posix().encode() + b"\0"