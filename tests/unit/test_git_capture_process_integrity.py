"""The subprocess seam caps both output streams, including error/timeout paths."""

from io import BytesIO
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess, TimeoutExpired

import pytest

import nokinc_factory.adapters.git_capture_process as process
from nokinc_factory.adapters.git_capture_io import git_environment


def _run(tmp_path: Path, arguments: list[str]) -> CompletedProcess[bytes]:
    return process.bounded_run(
        arguments, cwd=tmp_path, capture_output=True, check=True,
        env=git_environment(), timeout=30,
    )


@pytest.mark.parametrize("arguments", [["git", "--version"], ["git", "--invalid-option"]])
def test_both_output_streams_are_capped_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    monkeypatch.setattr(process, "MAX_COMMAND_BYTES", 8)
    with pytest.raises(ValueError, match="output byte limit"):
        _run(tmp_path, arguments)


def test_process_failure_retains_its_bounded_stderr(tmp_path: Path) -> None:
    with pytest.raises(CalledProcessError) as failure:
        _run(tmp_path, ["git", "--invalid-option"])
    assert failure.value.returncode != 0
    assert b"invalid-option" in failure.value.stderr


def test_timeout_kills_the_process_and_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed = False

    class HangingProcess:
        stdout = BytesIO()
        stderr = BytesIO()

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "HangingProcess":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def wait(self, *, timeout: float) -> None:
            raise TimeoutExpired("git", timeout)

        def kill(self) -> None:
            nonlocal killed
            killed = True

    monkeypatch.setattr(process, "Popen", HangingProcess)
    with pytest.raises(TimeoutExpired):
        _run(tmp_path, ["git", "--version"])
    assert killed