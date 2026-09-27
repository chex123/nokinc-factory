"""Bound Git output while it is produced, rather than after unbounded communicate()."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from subprocess import DEVNULL, PIPE, CalledProcessError, CompletedProcess, Popen, TimeoutExpired
from threading import Lock
from typing import IO

MAX_COMMAND_BYTES = 32 * 1024 * 1024


def bounded_run(
    arguments: list[str], *, cwd: Path, capture_output: bool, check: bool,
    env: dict[str, str], timeout: float,
) -> CompletedProcess[bytes]:
    """The usual subprocess.run test seam, with a shared stdout/stderr byte cap.

    The executable/OS are trusted; repository helpers cannot be invoked. At most
    the cap plus two 64KiB pipe reads are retained before terminating oversized Git.
    """
    if not capture_output or not check:
        raise ValueError("Capture requires checked binary output")
    size, exceeded = 0, False
    lock = Lock()
    with Popen(arguments, cwd=cwd, env=env, stdin=DEVNULL, stdout=PIPE, stderr=PIPE) as process:
        assert process.stdout is not None and process.stderr is not None

        def drain(stream: IO[bytes]) -> bytes:
            nonlocal size, exceeded
            content = bytearray()
            while chunk := stream.read(64 * 1024):
                with lock:
                    size += len(chunk)
                    exceeded |= size > MAX_COMMAND_BYTES
                    stop = exceeded
                if stop:
                    with suppress(ProcessLookupError):
                        process.kill()
                    break
                content.extend(chunk)
            return bytes(content)

        with ThreadPoolExecutor(max_workers=2) as pool:
            stdout = pool.submit(drain, process.stdout)
            stderr = pool.submit(drain, process.stderr)
            try:
                process.wait(timeout=timeout)
            except TimeoutExpired:
                process.kill()
                raise
            output, errors = stdout.result(), stderr.result()
        if exceeded:
            raise ValueError("Git command output byte limit exceeded")
        if process.returncode:
            raise CalledProcessError(process.returncode, arguments, output=output, stderr=errors)
        return CompletedProcess(arguments, 0, output, errors)