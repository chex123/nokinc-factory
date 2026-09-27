import sys
from pathlib import Path

import pytest

from nokinc_factory.adapters.subprocess_toolchain import SubprocessToolchain
from nokinc_factory.ports.toolchain import GateName, GateStatus, ToolchainSpec


def adapter(command: str, *, timeout: float = 2.0) -> SubprocessToolchain:
    return SubprocessToolchain(
        ToolchainSpec(language="test", commands={GateName.BUILD: command}),
        timeout_seconds=timeout,
        output_bytes=100,
    )


def test_supported_command_passes_and_returns_bounded_output(tmp_path: Path) -> None:
    result = adapter(f'{sys.executable} -c "print(\'ok\')"').run(
        GateName.BUILD, str(tmp_path),
    )

    assert result.status is GateStatus.PASS
    assert result.output.strip() == "ok"
    assert result.duration_seconds >= 0


def test_nonzero_command_fails_without_exposing_unbounded_output(tmp_path: Path) -> None:
    result = adapter(f'{sys.executable} -c "print(\'x\' * 1000); raise SystemExit(3)"').run(
        GateName.BUILD, str(tmp_path),
    )

    assert result.status is GateStatus.FAIL
    assert len(result.output.encode()) <= 100
    assert result.failures


def test_unsupported_gate_is_not_available(tmp_path: Path) -> None:
    result = adapter("never-run").run(GateName.UNIT, str(tmp_path))

    assert result.status is GateStatus.NOT_AVAILABLE
    assert result.failures == []


def test_missing_executable_is_not_available(tmp_path: Path) -> None:
    result = adapter("factory-executable-that-does-not-exist").run(
        GateName.BUILD, str(tmp_path),
    )

    assert result.status is GateStatus.NOT_AVAILABLE


@pytest.mark.parametrize("command", [
    f'{sys.executable} -c "print(1); print(2)" | more',
    f'{sys.executable} -c "print(1)"; whoami',
    f'{sys.executable} -c "print(1)" || whoami',
])
def test_shell_operators_are_rejected(tmp_path: Path, command: str) -> None:
    result = adapter(command).run(GateName.BUILD, str(tmp_path))

    assert result.status is GateStatus.FAIL
    assert result.failures[0]["rule"] == "shell_syntax"


def test_timeout_is_a_failure(tmp_path: Path) -> None:
    result = adapter(f'{sys.executable} -c "import time; time.sleep(2)"', timeout=0.05).run(
        GateName.BUILD, str(tmp_path),
    )

    assert result.status is GateStatus.FAIL
    assert result.failures[0]["rule"] == "timeout"
