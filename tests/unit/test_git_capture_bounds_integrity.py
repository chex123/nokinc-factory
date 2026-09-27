"""Bound path expansion and local metadata/config loops before expensive traversal."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import SupportsIndex, Unpack, cast

import pytest
from git_capture_integrity_helpers import ReadOptions, capture, git, make_repository

import nokinc_factory.adapters.git_capture_io as capture_io
import nokinc_factory.adapters.git_capture_snapshot as snapshot
import nokinc_factory.adapters.git_capture_view as view
from nokinc_factory.adapters.git_candidate import (
    GitCaptureIntegrityError,
    SubprocessGitCommandRunner,
)
from nokinc_factory.adapters.git_capture_limits import CaptureBudget, CaptureLimits


@pytest.mark.parametrize("path", ["/".join(["d"] * 1000), "x" * 4097], ids=["depth", "length"])
def test_inventory_path_is_bounded_before_attribute_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str,
) -> None:
    repository, base = make_repository(tmp_path)

    class InventoryRunner(SubprocessGitCommandRunner):
        def _execute(self, arguments: tuple[str, ...], root: Path) -> bytes:
            if "ls-files" in arguments and "--stage" in arguments:
                return f"100644 {'a' * 40} 0\t{path}\0".encode()
            return super()._execute(arguments, root)

    def forbidden_metadata(*args: object, **kwargs: object) -> None:
        pytest.fail("unbounded inventory reached attribute metadata")

    monkeypatch.setattr(snapshot, "metadata", forbidden_metadata)
    with pytest.raises(GitCaptureIntegrityError, match="(?i)path.*(depth|length|limit)"):
        capture(repository, base, runner=InventoryRunner())


@pytest.mark.parametrize("name", ["max_path_length", "max_path_depth", "max_metadata_entries"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, float("inf"), float("nan"), "2"])
def test_traversal_limits_are_positive_integers(name: str, value: object) -> None:
    construct = cast(Callable[..., CaptureLimits], CaptureLimits)
    with pytest.raises(ValueError, match="(?i)limit|integer"):
        construct(**{name: value})


@pytest.mark.parametrize("name", ["max_path_length", "max_path_depth", "max_metadata_entries"])
def test_traversal_limits_are_explicit_and_configurable(name: str) -> None:
    limits = CaptureLimits(**{name: 3})
    assert getattr(limits, name) == 3


def test_path_limits_include_the_exact_boundary() -> None:
    budget = CaptureBudget(CaptureLimits(max_path_length=3, max_path_depth=2))
    assert budget.path("a/b") == "a/b"
    with pytest.raises(ValueError, match="length"):
        budget.path("abcd")
    budget = CaptureBudget(CaptureLimits(max_path_length=20, max_path_depth=2))
    with pytest.raises(ValueError, match="depth"):
        budget.path("a/b/c")


@pytest.mark.parametrize("stage", ["attributes", "anchors"])
def test_expanded_metadata_inventory_is_bounded_before_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    budget = CaptureBudget(CaptureLimits(max_metadata_entries=3))

    def forbidden_stat(*args: object, **kwargs: object) -> None:
        pytest.fail("over-budget expansion reached the filesystem")

    monkeypatch.setattr(Path, "lstat", forbidden_stat)
    with pytest.raises(ValueError, match="metadata.*limit"):
        if stage == "attributes":
            snapshot._with_attributes(tmp_path, ("a/b/c/leaf",), budget)
        else:
            snapshot._anchors(tmp_path, ("a/b/c/leaf",), budget)


def test_parent_expansion_splits_each_input_only_once(tmp_path: Path) -> None:
    splits = 0

    class ObservedPath(str):
        def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
            nonlocal splits
            splits += 1
            return super().split(sep, maxsplit)

    paths = (ObservedPath("a/b/c/leaf"),)
    budget = CaptureBudget(CaptureLimits())
    snapshot._with_attributes(tmp_path, paths, budget)
    snapshot._anchors(tmp_path, paths, budget)
    assert splits == 2


@pytest.mark.parametrize("stage", ["attributes", "anchors"])
def test_parent_expansion_checks_deadline_inside_the_loop(tmp_path: Path, stage: str) -> None:
    now = [0.0]

    class ExpiringPath(str):
        def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
            parts = super().split(sep, maxsplit)
            now[0] = 61.0
            return parts

    budget = CaptureBudget(CaptureLimits(), clock=lambda: now[0])
    with pytest.raises(ValueError, match="deadline"):
        if stage == "attributes":
            snapshot._with_attributes(tmp_path, (ExpiringPath("a/b/c"),), budget)
        else:
            snapshot._anchors(tmp_path, (ExpiringPath("a/b/c"),), budget)


def test_metadata_checks_deadline_between_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    calls: list[Path] = []
    budget = CaptureBudget(CaptureLimits(), clock=lambda: now[0])
    native = Path.lstat

    def observed(path: Path) -> os.stat_result:
        calls.append(path)
        now[0] = 61.0
        return native(path)

    monkeypatch.setattr(Path, "lstat", observed)
    with pytest.raises(ValueError, match="deadline"):
        capture_io.metadata((tmp_path, tmp_path), budget=budget)
    assert calls == [tmp_path]


def test_configuration_file_reads_share_the_capture_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base = make_repository(tmp_path)
    first_rule = repository / ".git/info/attributes"
    first_rule.write_bytes(b"item text\n")
    now = [0.0]
    native = capture_io.read_regular

    def observed(path: Path, **kwargs: Unpack[ReadOptions]) -> tuple[bytes, int]:
        assert now[0] == 0.0, "expired config loop reached another file read"
        result = native(path, **kwargs)
        if path == first_rule:
            now[0] = 61.0
        return result

    monkeypatch.setattr(view, "read_regular", observed)
    with pytest.raises(GitCaptureIntegrityError, match="deadline"):
        capture(repository, base, runner=SubprocessGitCommandRunner(clock=lambda: now[0]))
    assert now[0] == 61.0


def test_configured_paths_are_bounded_before_local_open(tmp_path: Path) -> None:
    repository, base = make_repository(tmp_path)
    git(repository, "config", "core.attributesfile", "x" * 4097)
    with pytest.raises(GitCaptureIntegrityError, match="(?i)path.*length"):
        capture(repository, base)