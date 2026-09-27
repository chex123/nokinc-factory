"""Native post-open races: a pinned directory must deny rename before a child read."""

import os
import subprocess
from pathlib import Path
from typing import IO, cast

import pytest

from nokinc_factory.adapters.git_capture_io import read_candidate_file


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows directory sharing")
@pytest.mark.parametrize("ancestor", ["parent", "parent/nested"])
def test_post_open_parent_rename_and_junction_cannot_read_external_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ancestor: str,
) -> None:
    repository = (tmp_path / "repository").resolve()
    leaf = repository / "parent/nested/leaf"
    leaf.parent.mkdir(parents=True)
    leaf.write_bytes(b"inside")
    parent = repository / ancestor
    outside = (tmp_path / "outside").resolve()
    external_leaf = outside / leaf.relative_to(parent)
    external_leaf.parent.mkdir(parents=True)
    external_leaf.write_bytes(b"external test sentinel")
    external_identity = (external_leaf.stat().st_dev, external_leaf.stat().st_ino)

    def junction(link: Path) -> None:
        # Junction creation requires no symlink privilege. Failure on native
        # Windows is a test failure, never a skip masquerading as race coverage.
        subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(outside)],
            check=True, capture_output=True, timeout=10,
        )

    probe = tmp_path / "junction-capability"
    junction(probe)
    os.rmdir(probe)
    native_stat, native_fdopen = Path.lstat, os.fdopen
    checks, rename_attempts, denied, external_reads = 0, 0, 0, 0
    substituted = False

    def after_post_open_check(path: Path) -> os.stat_result:
        nonlocal checks, rename_attempts, denied, substituted
        result = native_stat(path)
        if path == parent:
            checks += 1
            if checks == 2:
                # Return the already obtained post-open stat: a later path stat
                # must not be what saves us from following the substituted parent.
                rename_attempts += 1
                try:
                    parent.rename(parent.with_name(parent.name + "-parked"))
                except OSError as exc:
                    assert exc.winerror in (5, 32), exc
                    denied += 1
                else:
                    junction(parent)
                    substituted = True
        return result

    def observed_fdopen(fd: int, mode: str, *, closefd: bool) -> IO[bytes]:
        nonlocal external_reads
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) == external_identity:
            external_reads += 1
        return cast(IO[bytes], native_fdopen(fd, mode, closefd=closefd))

    monkeypatch.setattr(Path, "lstat", after_post_open_check)
    monkeypatch.setattr(os, "fdopen", observed_fdopen)
    try:
        result = read_candidate_file(repository, "parent/nested/leaf")
    finally:
        if substituted:
            os.rmdir(parent)
        assert rename_attempts == 1, "the native post-open race was not exercised"
        assert external_reads == 0, "an external leaf reached the content-read handle"
    assert denied == 1, "directory sharing did not deny rename"
    assert result is not None and result[0] == b"inside"