"""Bounded local execution of a target repository's declared toolchain."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from nokinc_factory.ports.toolchain import (
    GateName,
    GateResult,
    GateStatus,
    ToolchainPort,
    ToolchainSpec,
)

_SENSITIVE_ENV_PARTS = ("SECRET", "TOKEN", "PASSWORD", "PRIVATE_KEY", "API_KEY")


def _safe_environment(root: Path) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items()
        if not any(part in key.upper() for part in _SENSITIVE_ENV_PARTS)
    }
    candidates = (
        root / ".venv" / "Scripts" if os.name == "nt" else root / ".venv" / "bin",
    )
    for candidate in candidates:
        if candidate.is_dir():
            environment["PATH"] = os.pathsep.join((str(candidate), environment.get("PATH", "")))
            break
    return environment


def _split_commands(command: str) -> tuple[str, ...]:
    """Split the only supported shell-like operator without invoking a shell."""
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        character = command[index]
        if escaped:
            current.append(character)
            escaped = False
            index += 1
            continue
        if character == "\\" and quote != "'":
            current.append(character)
            escaped = True
            index += 1
            continue
        if character in ("'", '"'):
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            current.append(character)
            index += 1
            continue
        if (quote is None and character == "&" and index + 1 < len(command)
            and command[index + 1] == "&"):
            parts.append("".join(current).strip())
            current = []
            index += 2
            continue
        if quote is None and character in ";|><`":
            raise ValueError("Unsupported shell syntax")
        if (quote is None and character == "$" and index + 1 < len(command)
            and command[index + 1] == "("):
            raise ValueError("Unsupported shell substitution")
        current.append(character)
        index += 1
    if quote is not None or escaped:
        raise ValueError("Unterminated command quoting")
    parts.append("".join(current).strip())
    if not parts or any(not part for part in parts):
        raise ValueError("Empty command segment")
    return tuple(parts)


def _argv(command: str, *, search_path: str | None = None) -> tuple[tuple[str, ...], ...]:
    result: list[tuple[str, ...]] = []
    for part in _split_commands(command):
        values = shlex.split(part, posix=os.name != "nt")
        if os.name == "nt":
            values = [
                value[1:-1] if len(value) >= 2 and value[0] == value[-1]
                and value[0] in "'\"" else value
                for value in values
            ]
        if not values:
            raise ValueError("Empty command segment")
        executable = shutil.which(values[0], path=search_path)
        if executable is None and os.name == "nt":
            executable = shutil.which(values[0] + ".cmd", path=search_path)
        if executable is not None:
            values[0] = executable
        result.append(tuple(values))
    return tuple(result)


def _bounded(data: bytes, limit: int) -> str:
    return data[:limit].decode("utf-8", errors="replace")


class SubprocessToolchain(ToolchainPort):
    """Run trusted, pinned toolchain declarations without shell interpolation.

    This is an execution adapter, not a sandbox. The caller must provide an
    isolated worker and trusted toolchain identity before invoking it.
    """

    def __init__(self, spec: ToolchainSpec, *, timeout_seconds: float = 600.0,
                 output_bytes: int = 64 * 1024) -> None:
        if timeout_seconds <= 0 or output_bytes <= 0:
            raise ValueError("toolchain limits must be positive")
        self._spec = ToolchainSpec.model_validate(spec)
        self._timeout_seconds = timeout_seconds
        self._output_bytes = output_bytes

    def spec(self) -> ToolchainSpec:
        return self._spec

    def run(self, gate: GateName, workdir: str) -> GateResult:
        command = self._spec.commands.get(gate)
        if command is None:
            return GateResult(
                gate=gate, status=GateStatus.NOT_AVAILABLE,
                output=f"No {gate.value} command is declared for {self._spec.language}.",
            )
        root = Path(workdir).resolve()
        if not root.is_dir():
            return GateResult(
                gate=gate, status=GateStatus.FAIL,
                failures=[{"rule": "workdir", "message": "workdir is not a directory"}],
            )
        try:
            environment = _safe_environment(root)
            commands = _argv(command, search_path=environment.get("PATH"))
        except (ValueError, IndexError) as exc:
            return GateResult(
                gate=gate, status=GateStatus.FAIL,
                failures=[{"rule": "shell_syntax", "message": str(exc)}],
            )
        started = time.monotonic()
        captured = bytearray()
        for argv in commands:
            remaining = self._timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                return self._failure(gate, started, captured, "timeout", "gate deadline exceeded")
            try:
                process = subprocess.Popen(
                    argv, cwd=root, env=environment,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                )
                try:
                    output, _ = process.communicate(timeout=remaining)
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    output, _ = process.communicate()
                    if isinstance(exc.output, bytes):
                        output = exc.output + output
                    captured.extend(output)
                    return self._failure(gate, started, captured, "timeout", "gate timed out")
            except FileNotFoundError:
                return GateResult(
                    gate=gate, status=GateStatus.NOT_AVAILABLE,
                    duration_seconds=time.monotonic() - started,
                    failures=[{
                        "rule": "tool_missing",
                        "message": "declared executable is unavailable",
                    }],
                )
            except OSError as exc:
                return self._failure(gate, started, captured, "process", str(exc))
            captured.extend(output)
            if process.returncode != 0:
                return self._failure(
                    gate, started, captured, "exit_code",
                    f"command exited with {process.returncode}",
                )
        return GateResult(
            gate=gate, status=GateStatus.PASS,
            duration_seconds=time.monotonic() - started,
            output=_bounded(bytes(captured), self._output_bytes),
        )

    def _failure(self, gate: GateName, started: float, captured: bytearray,
                 rule: str, message: str) -> GateResult:
        return GateResult(
            gate=gate, status=GateStatus.FAIL,
            duration_seconds=time.monotonic() - started,
            output=_bounded(bytes(captured), self._output_bytes),
            failures=[{"rule": rule, "message": message}],
        )
