"""Local regular-file handles, not an isolation boundary against a hostile OS.

POSIX descends using dir_fd/O_NOFOLLOW. Windows requests directory data access
and pins every ancestor without write/delete sharing, then opens the leaf with
OPEN_REPARSE_POINT. Metadata-only access would not enforce the sharing lock.
Unsupported platforms/network paths fail closed; no check-then-follow fallback.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from nokinc_factory.adapters.git_capture_limits import CaptureBudget, CaptureLimits


def reject_link(path: Path, info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Unsupported symlink/reparse path: {path.name}")


def identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns


@contextmanager
def regular_handle(
    path: Path, *, budget: CaptureBudget | None = None,
) -> Iterator[tuple[int, os.stat_result]]:
    budget = budget or CaptureBudget(CaptureLimits())
    budget.path(str(path))
    path = path.absolute()
    budget.path(str(path))
    with ExitStack() as stack:
        if os.name == "nt":
            fd, before = _windows_handle(path, stack, budget)
        else:
            fd, before = _posix_handle(path, stack, budget)
        if not stat.S_ISREG(before.st_mode):
            if stat.S_ISDIR(before.st_mode):
                raise IsADirectoryError(f"Candidate is a directory: {path.name}")
            raise ValueError(f"Unsupported filesystem mode: {path.name}")
        yield fd, before


def _posix_handle(
    path: Path, stack: ExitStack, budget: CaptureBudget,
) -> tuple[int, os.stat_result]:
    nofollow: int = getattr(os, "O_NOFOLLOW", 0)
    nonblock: int = getattr(os, "O_NONBLOCK", 0)
    directory: int = getattr(os, "O_DIRECTORY", 0)
    if os.open not in os.supports_dir_fd or not all((nofollow, nonblock, directory)):
        raise ValueError("Unsupported platform: handle-relative nofollow is required")
    flags = os.O_RDONLY | nofollow | nonblock
    parent = os.open(path.anchor, flags | directory)
    stack.callback(os.close, parent)
    current = Path(path.anchor)
    for part in path.parts[1:-1]:
        budget.remaining()
        current /= part
        reject_link(current, current.lstat())
        parent = os.open(part, flags | directory, dir_fd=parent)
        stack.callback(os.close, parent)
    budget.remaining()
    reject_link(path, path.lstat())
    before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    reject_link(path, before)
    fd = os.open(path.name, flags, dir_fd=parent)
    stack.callback(os.close, fd)
    return fd, before


def _windows_handle(
    path: Path, stack: ExitStack, budget: CaptureBudget,
) -> tuple[int, os.stat_result]:
    # Imported only on Windows: this code has no emulated/insecure POSIX fallback.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    if path.drive.startswith("\\\\"):
        raise ValueError("Unsupported network path for local capture")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    attributes = kernel.GetFileInformationByHandleEx
    attributes.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    attributes.restype = wintypes.BOOL

    def opened(current: Path, *, directory: bool) -> tuple[int, os.stat_result]:
        budget.remaining()
        before = current.lstat()
        reject_link(current, before)
        if directory and not stat.S_ISDIR(before.st_mode):
            raise NotADirectoryError(f"Candidate ancestor is not a directory: {current.name}")
        # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES activates share checking.
        # Ancestors deny write/delete handles (including reparse substitution);
        # only regular leaves allow concurrent content writes for drift detection.
        handle = create(str(current), 0x81 if directory else 0x80000000,
                        1 if directory else 3,
                        None, 3, 0x00200000 | (0x02000000 if directory else 0), None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        tag = (wintypes.DWORD * 2)()
        try:
            if not attributes(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)):
                raise ctypes.WinError(ctypes.get_last_error())
            if tag[0] & 0x400:
                raise ValueError(f"Unsupported symlink/reparse path: {current.name}")
            if identity(before) != identity(current.lstat()):
                raise ValueError("Candidate ancestor/leaf changed while opening")
        except BaseException:
            close(handle)
            raise
        return int(handle), before

    for parent in reversed(path.parents):
        handle, _ = opened(parent, directory=True)
        stack.callback(close, handle)
    # A directory in place of a tracked file is a deletion, not an unsafe mode.
    budget.remaining()
    before = path.lstat()
    reject_link(path, before)
    if stat.S_ISDIR(before.st_mode):
        raise IsADirectoryError(f"Candidate is a directory: {path.name}")
    handle, before = opened(path, directory=False)
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        close(handle)
        raise
    stack.callback(os.close, fd)
    return fd, before