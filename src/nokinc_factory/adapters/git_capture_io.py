"""Bounded local reads and a helper-free Git subprocess environment."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from nokinc_factory.adapters.git_capture_handles import identity, regular_handle, reject_link
from nokinc_factory.adapters.git_capture_limits import CaptureBudget, CaptureLimits
from nokinc_factory.domain.preflight import validate_candidate_path

MAX_FILE_BYTES = 8 * 1024 * 1024


def git_environment(*, read_config: bool = False) -> dict[str, str]:
    """Do not let ambient Git configuration redirect the repository or run helpers."""
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "", "LC_ALL": "C",
    })
    if read_config:
        # Only allowlisted scalar `git config --get` calls use the user's config.
        # Reading these values cannot execute a filter, textconv or fsmonitor.
        for key in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL"):
            environment.pop(key, None)
            if key in os.environ:
                environment[key] = os.environ[key]
    return environment


def git_arguments(executable: str, arguments: tuple[str, ...]) -> list[str]:
    """Harden only the real subprocess argv, preserving the injected runner protocol."""
    options = ["--no-optional-locks", "--no-pager"]
    for setting in (
        "core.fsmonitor=false", f"core.hooksPath={os.devnull}", "core.untrackedCache=false",
        "core.splitIndex=false", "protocol.allow=never", "submodule.recurse=false",
        "fetch.recurseSubmodules=false", "maintenance.auto=false", "gc.auto=0",
    ):
        options.extend(("-c", setting))
    args = list(arguments)
    if "diff" in args:
        position = args.index("diff") + 1
        args[position:position] = [
            "--no-textconv", "--no-ext-diff", "--no-renames", "--ignore-submodules=none",
            "--no-color", "--src-prefix=a/", "--dst-prefix=b/",
        ]
    if args[:2] == ["rev-parse", "--verify"]:
        args.insert(2, "--end-of-options")
    return [executable, *options, *args]


def metadata(
    paths: tuple[Path, ...], *, budget: CaptureBudget | None = None,
) -> tuple[tuple[int, ...] | None, ...]:
    """Cheap drift observations, not an alternative to safe content handles."""
    budget = budget or CaptureBudget(CaptureLimits())
    budget.metadata(len(paths))
    result: list[tuple[int, ...] | None] = []
    for path in paths:
        budget.path(str(path))
        try:
            info = path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            result.append(None)
        else:
            reject_link(path, info)
            result.append((*identity(info), info.st_ctime_ns))
    budget.remaining()
    return tuple(result)


def read_regular(
    path: Path, *, max_bytes: int = MAX_FILE_BYTES, budget: CaptureBudget | None = None,
) -> tuple[bytes, int]:
    """Open safely first, enforce a size cap, then compare handle/path identities."""
    budget = budget or CaptureBudget(CaptureLimits())
    with regular_handle(path, budget=budget) as (fd, before):
        opened = os.fstat(fd)
        reject_link(path, opened)
        if not stat.S_ISREG(opened.st_mode) or identity(before) != identity(opened):
            raise ValueError("Candidate file changed while opening")
        if max_bytes < 0 or opened.st_size > max_bytes:
            raise ValueError("Candidate file byte limit exceeded")
        budget.remaining()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            content = stream.read(max_bytes + 1)
        budget.remaining()
        if len(content) > max_bytes:
            raise ValueError("Candidate file byte limit exceeded")
        after, current = os.fstat(fd), path.lstat()
        reject_link(path, current)
        # Windows creation times differ between path-stat and handle-stat APIs.
        if (len({identity(info) for info in (before, opened, after, current)}) != 1
                or before.st_ctime_ns != current.st_ctime_ns
                or opened.st_ctime_ns != after.st_ctime_ns):
            raise ValueError("Candidate file changed while reading")
    return content, stat.S_IMODE(current.st_mode)


def read_candidate_file(
    repository: Path, relative_path: str, *, missing_ok: bool = False,
    max_bytes: int = MAX_FILE_BYTES, budget: CaptureBudget | None = None,
) -> tuple[bytes, int] | None:
    """A regular tracked path may be absent due to a legitimate file/dir replacement."""
    budget = budget or CaptureBudget(CaptureLimits())
    budget.path(relative_path)
    validate_candidate_path(relative_path)
    try:
        return read_regular(repository / relative_path, max_bytes=max_bytes, budget=budget)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        if missing_ok:
            return None
        raise