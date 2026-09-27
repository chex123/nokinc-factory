"""File boundaries reject links before content reads, including swapped parents."""

import os
import re
import subprocess
from pathlib import Path
from typing import IO, cast

import pytest

from nokinc_factory.adapters.git_capture_io import read_candidate_file


def test_swapped_ancestor_cannot_supply_external_leaf_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = (tmp_path / "repository").resolve()
    parent = repository / "parent"
    parent.mkdir(parents=True)
    (parent / "leaf").write_bytes(b"inside")
    outside = (tmp_path / "external").resolve()
    outside.mkdir()
    (outside / "leaf").write_bytes(b"external must not be opened")
    native, native_fdopen, native_fstat = Path.lstat, os.fdopen, os.fstat
    inside_info, outside_info = (parent / "leaf").stat(), (outside / "leaf").stat()
    inside_identity = (inside_info.st_dev, inside_info.st_ino)
    outside_identity = (outside_info.st_dev, outside_info.st_ino)
    assert inside_identity != outside_identity
    attempts, inside_reads, external_reads = 0, 0, 0
    denied, installed = False, False

    def swap_after_check(path: Path) -> os.stat_result:
        nonlocal attempts, denied, installed
        result = native(path)
        if path == parent and attempts == 0:
            attempts += 1
            try:
                parent.rename(repository / "parked")
            except OSError as exc:
                # Native directory sharing can prevent the attack itself. That
                # is only success if capture subsequently reads the inside leaf.
                if os.name != "nt" or exc.winerror != 32:
                    raise
                denied = True
                return result
            if os.name == "nt":
                subprocess.run(
                    ["cmd", "/d", "/c", "mklink", "/J", str(parent), str(outside)],
                    capture_output=True, check=True, timeout=10,
                )
            else:
                parent.symlink_to(outside, target_is_directory=True)
            installed = True
        return result

    def observed_fdopen(fd: int, mode: str, *, closefd: bool) -> IO[bytes]:
        nonlocal inside_reads, external_reads
        info = native_fstat(fd)
        file_identity = (info.st_dev, info.st_ino)
        inside_reads += int(file_identity == inside_identity)
        external_reads += int(file_identity == outside_identity)
        return cast(IO[bytes], native_fdopen(fd, mode, closefd=closefd))

    monkeypatch.setattr(Path, "lstat", swap_after_check)
    monkeypatch.setattr(os, "fdopen", observed_fdopen)
    try:
        try:
            content = read_candidate_file(repository, "parent/leaf")
        except (ValueError, OSError) as exc:
            assert installed and not denied, "fixture failure is not capture rejection"
            assert re.search("unsupported|changed|link|directory", str(exc), re.IGNORECASE)
        else:
            assert os.name == "nt" and denied and not installed
            assert content is not None and content[0] == b"inside"
            assert inside_reads == 1, "the original leaf must actually be read"
    finally:
        if installed:
            if os.name == "nt":
                os.rmdir(parent)
            else:
                parent.unlink()
        assert attempts == 1, "the actual ancestor check must be exercised"
        assert external_reads == 0, "external content reached a read handle"


@pytest.mark.parametrize("relative_path", ["directory", "regular/child"])
def test_tracked_paths_replaced_by_another_filesystem_kind_are_missing(
    tmp_path: Path, relative_path: str,
) -> None:
    (tmp_path / "directory").mkdir()
    (tmp_path / "regular").write_bytes(b"replacement")
    assert read_candidate_file(tmp_path, relative_path, missing_ok=True) is None


def test_regular_file_reads_are_bounded(tmp_path: Path) -> None:
    (tmp_path / "large").write_bytes(b"123456789")
    with pytest.raises(ValueError, match="(?i)limit|budget"):
        read_candidate_file(tmp_path, "large", max_bytes=8)