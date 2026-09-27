"""Two bounded raw scans, cheap command-boundary metadata, one copied worktree.

This is optimistic local evidence, not atomic isolation: transient ABA changes
and a hostile OS/object store require trusted isolated CI. Git never diffs the
live worktree. Unsupported flags, links, gitlinks and conflicts fail closed.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from nokinc_factory.adapters.git_capture_io import metadata, read_candidate_file
from nokinc_factory.adapters.git_capture_limits import CaptureBudget
from nokinc_factory.adapters.git_capture_view import (
    GitCaptureSource,
    GitCaptureView,
    GitExecute,
    GitRead,
)
from nokinc_factory.domain.preflight import RawWorktree, candidate_file, validate_candidate_path

_UNTRACKED = ("ls-files", "--others", "--exclude-standard", "-z")
_REGULAR_MODES = {b"100644", b"100755"}


def _paths(raw: bytes, budget: CaptureBudget) -> tuple[str, ...]:
    paths = []
    for path in raw.split(b"\0"):
        budget.remaining()
        if path:
            paths.append(validate_candidate_path(budget.path(path.decode("utf-8"))))
            budget.inventory(len(paths))
    if (raw and not raw.endswith(b"\0")) or len(set(paths)) != len(paths):
        raise ValueError("Git returned noncanonical candidate paths")
    return tuple(sorted(paths))


def _index_paths(read: GitRead, budget: CaptureBudget) -> tuple[str, ...]:
    paths = []
    for entry in read(("ls-files", "--stage", "-z")).split(b"\0"):
        budget.remaining()
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, _oid, stage = metadata.split()
        if mode not in _REGULAR_MODES or stage != b"0":
            raise ValueError("Unsupported index mode (symlink/submodule/conflict/sparse entry)")
        paths.append(validate_candidate_path(budget.path(path.decode("utf-8"))))
        budget.inventory(len(paths))
    for count, entry in enumerate(read(("ls-files", "-v", "-z")).split(b"\0")):
        budget.inventory(count)
        if entry and entry[:2] != b"H ":
            raise ValueError("Unsupported index flags (assume-unchanged/skip-worktree)")
    return tuple(sorted(paths))


def _require_tree_modes(read: GitRead, commit: str, budget: CaptureBudget) -> None:
    count, total = 0, 0
    for entry in read(("ls-tree", "-r", "-z", "--long", "--full-tree", commit)).split(b"\0"):
        budget.remaining()
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, _oid, size = metadata.split()
        if mode not in _REGULAR_MODES or kind != b"blob":
            raise ValueError("Unsupported tree mode (symlink/submodule)")
        validate_candidate_path(budget.path(path.decode("utf-8")))
        count += 1
        budget.inventory(count)
        total += int(size)
        if int(size) > budget.limits.max_file_bytes or total > budget.limits.max_total_bytes:
            raise ValueError("Git tree byte limit exceeded")


def _with_attributes(
    repository: Path, paths: tuple[str, ...], budget: CaptureBudget,
) -> tuple[str, ...]:
    budget.inventory(len(paths))
    controls = {".gitattributes"}
    for path in paths:
        parent = ""
        for part in budget.path(path).split("/")[:-1]:
            budget.remaining()
            parent = f"{parent}/{part}" if parent else part
            controls.add(budget.path(f"{parent}/.gitattributes"))
            budget.metadata(len(controls))
    present = set(paths)
    budget.metadata(len(controls))
    for path in sorted(controls):
        budget.path(path)
        if metadata((repository / path,), budget=budget)[0] is not None:
            present.add(path)
            budget.inventory(len(present))
    return tuple(sorted(present))


def _anchors(repository: Path, paths: tuple[str, ...], budget: CaptureBudget) -> tuple[Path, ...]:
    anchors = {repository}
    budget.metadata(len(anchors))
    for path in paths:
        current = repository
        for part in budget.path(path).split("/"):
            budget.remaining()
            current /= part
            budget.path(str(current))
            anchors.add(current)
            budget.metadata(len(anchors))
    return tuple(sorted(anchors))


def _raw_snapshot(
    source: GitCaptureSource, paths: tuple[str, ...], budget: CaptureBudget,
) -> RawWorktree:
    budget.inventory(len(paths))
    budget.raw_scans += 1
    if budget.raw_scans > 2:
        raise ValueError("Capture raw scan budget exceeded")
    files, missing, executable = [], [], []
    total = 0
    for path in paths:
        budget.remaining()
        file = read_candidate_file(
            source.repository, path, missing_ok=True,
            max_bytes=min(budget.limits.max_file_bytes, budget.limits.max_total_bytes - total),
            budget=budget,
        )
        budget.file_reads += 1
        if file is None:
            missing.append(path)
        else:
            raw, mode = file
            total += len(raw)
            budget.bytes_read += len(raw)
            files.append(candidate_file(path, raw))
            if mode & stat.S_IXUSR:
                executable.append(path)
    budget.remaining()
    return RawWorktree.create(files=tuple(files), missing_paths=tuple(missing),
                              executable_paths=tuple(executable),
                              git_inputs_digest=source.git_inputs_digest)


@dataclass(frozen=True)
class GitCaptureObservation:
    source: GitCaptureSource
    view: GitCaptureView
    budget: CaptureBudget
    tracked: tuple[str, ...]
    paths: tuple[str, ...]
    untracked: tuple[str, ...]
    raw_worktree: RawWorktree
    anchors: tuple[Path, ...]
    anchor_metadata: tuple[tuple[int, ...] | None, ...]
    validated_trees: set[str]

    @classmethod
    def capture(
        cls, source: GitCaptureSource, view: GitCaptureView, budget: CaptureBudget,
    ) -> GitCaptureObservation:
        tracked = _index_paths(view.read, budget)
        _require_tree_modes(view.read, source.head, budget)
        untracked = _paths(view.live_inventory(), budget)
        budget.inventory(len(tracked) + len(untracked))
        paths = _with_attributes(
            source.repository, tuple(sorted(set(tracked) | set(untracked))), budget,
        )
        budget.inventory(len(paths))
        ordered = _anchors(source.repository, paths, budget)
        before = metadata(ordered, budget=budget)
        raw = _raw_snapshot(source, paths, budget)
        if set(raw.missing_paths).intersection(untracked):
            raise ValueError("Untracked file disappeared during capture")
        tree = view.populate(raw)
        _require_tree_modes(view.read, tree, budget)
        result = cls(source, view, budget, tracked, paths, untracked, raw, ordered, before,
                     {source.head, tree})
        result.verify_metadata()
        return result

    def verify_metadata(self) -> None:
        self.budget.remaining()
        self.source.verify_metadata()
        if metadata(self.anchors, budget=self.budget) != self.anchor_metadata:
            raise ValueError("Git worktree metadata changed during capture")

    def finish(self, execute: GitExecute) -> None:
        self.verify_metadata()
        untracked = _paths(self.view.live_inventory(), self.budget)
        self.budget.inventory(len(self.tracked) + len(untracked))
        paths = _with_attributes(self.source.repository,
                     tuple(sorted(set(self.tracked) | set(untracked))), self.budget)
        if (untracked != self.untracked or paths != self.paths
                or _raw_snapshot(self.source, paths, self.budget) != self.raw_worktree):
            raise ValueError("Git worktree content/inventory changed during capture")
        self.source.verify(execute)
        self.verify_metadata()

    def run(self, arguments: tuple[str, ...], execute: GitExecute) -> bytes:
        self.verify_metadata()
        if arguments[:2] == ("rev-parse", "--verify"):
            result = execute(arguments, self.source.repository)
        elif arguments and arguments[0] == "diff":
            if arguments[-1] == self.source.head and arguments[-2] not in self.validated_trees:
                _require_tree_modes(self.view.read, arguments[-2], self.budget)
                self.validated_trees.add(arguments[-2])
            result = self.view.read(arguments)
        elif arguments == _UNTRACKED:
            result = b"".join(path.encode("utf-8") + b"\0" for path in self.untracked)
        else:
            raise ValueError("Unsupported Git capture command")
        self.verify_metadata()
        return result