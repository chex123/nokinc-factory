"""Advisory Maestro adapter for preprovisioned isolated mobile workers.

Spec Parts 8/10: IDE-independent tests are target-owned; local results never
authorize merge/deploy. This adapter neither builds nor installs an app, boots
devices, uploads screenshots, provisions credentials, repairs flows nor retries.
The caller must provide an isolated approved runner and installed synthetic app.
Maestro flow scripts are executable/untrusted: shell=False is NOT a sandbox.
"""

import hashlib
import os
import platform
import tempfile
from collections.abc import Mapping
from pathlib import Path
from subprocess import DEVNULL, TimeoutExpired, run
from time import monotonic
from typing import Protocol

from nokinc_factory.adapters.maestro_inputs import flow_digest
from nokinc_factory.adapters.maestro_reports import (
    MAX_REPORT_BYTES,
    parse_report,
)
from nokinc_factory.domain.mobile import MaestroSpec, MobileGateResult
from nokinc_factory.ports.toolchain import GateName, GateStatus, ToolchainSpec


class MaestroCommandRunner(Protocol):
    def run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int) -> int: ...


class SubprocessMaestroRunner:
    """Invoke a provisioned tool without shell interpolation or credential env inheritance.

    OS/tool-location variables are copied for local use. Broader filesystem,
    process-tree and network containment belongs to the worker sandbox. Windows
    launcher wrappers are not qualified by this initial adapter profile.
    """

    def __init__(self, *, environment: Mapping[str, str] | None = None) -> None:
        allowed = {
            "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
            "JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT",
        }
        self._environment = (
            dict(environment) if environment is not None
            else {key: value for key, value in os.environ.items() if key.upper() in allowed}
        )

    def run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int) -> int:
        result = run(
            argv, cwd=cwd, env=self._environment, shell=False, check=False,
            timeout=timeout_seconds, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL,
        )
        return result.returncode


class MaestroToolchain:
    """Run one explicit device/profile and report exact expected flow outcomes."""

    def __init__(
        self, config: MaestroSpec, *, runner: MaestroCommandRunner | None = None,
        executable: str = "maestro", host_system: str | None = None,
        artifacts_root: Path | None = None,
    ) -> None:
        self._config = config
        self._runner = runner or SubprocessMaestroRunner()
        self._executable = executable
        self._host = host_system or platform.system()
        self._artifacts_root = artifacts_root

    def spec(self) -> ToolchainSpec:
        return ToolchainSpec(language="mobile", commands={GateName.MOBILE_E2E: "maestro test"})

    def run(self, gate: GateName, workdir: str) -> MobileGateResult:
        result = MobileGateResult(gate=gate, status=GateStatus.NOT_AVAILABLE)
        if gate is not GateName.MOBILE_E2E:
            result.output = "This adapter only provides the mobile_e2e gate"
            return result
        if self._host not in {"Darwin", "Linux"} or (
            self._config.platform == "ios" and self._host != "Darwin"
        ):
            result.output = "Requires a qualified Linux/Android or macOS mobile worker"
            return result
        start = monotonic()
        try:
            root = Path(workdir).resolve(strict=True)
            source = root / self._config.flow_directory
            if not source.resolve(strict=True).is_relative_to(root):
                raise ValueError("flow input escapes workdir")
            current = source
            while current != root:
                if current.is_symlink() or current.is_junction():
                    raise ValueError("redirected flow directories are forbidden")
                current = current.parent
            result.flow_digest = flow_digest(source)
            artifacts = self._artifact_directory(root)
            artifact_identity = self._directory_identity(artifacts)
            result.artifact_directory = str(artifacts)
            report = artifacts / "report.xml"
            argv = (
                self._executable, "--device", self._config.device_id,
                "--platform", self._config.platform, "test", "--format", "junit",
                "--output", str(report), "--test-output-dir", str(artifacts),
                "--debug-output", str(artifacts), str(source),
            )
            exit_code = self._runner.run(
                argv, cwd=root, timeout_seconds=self._config.timeout_seconds,
            )
            if flow_digest(source) != result.flow_digest:
                raise ValueError("flow inputs changed during execution")
            if exit_code != 0:
                result.status = GateStatus.FAIL
                result.output = "Maestro returned a nonzero exit status"
                return result
            if self._directory_identity(artifacts) != artifact_identity:
                raise ValueError("evidence directory identity changed")
            if report.is_symlink() or not report.is_file():
                raise ValueError("new report unavailable or redirected")
            with report.open("rb") as stream:
                content = stream.read(MAX_REPORT_BYTES + 1)
            summary = parse_report(content, expected_tests=self._config.expected_tests)
            result.report_digest = "sha256:" + hashlib.sha256(content).hexdigest()
            result.tests_run = summary.tests
            result.status = GateStatus.PASS if summary.passed else GateStatus.FAIL
            result.output = (
                "Expected flow results verified" if summary.passed else "Flows failed/skipped"
            )
        except FileNotFoundError:
            result.status = (
                GateStatus.NOT_AVAILABLE if result.artifact_directory else GateStatus.FAIL
            )
            result.output = "Required runner or input is unavailable"
        except TimeoutExpired:
            result.status = GateStatus.FAIL
            result.output = "Mobile deadline exceeded; worker must tear down device/processes"
        except (OSError, ValueError):
            result.status = GateStatus.FAIL
            result.output = "Mobile inputs/report failed validation; inspect restricted artifacts"
        finally:
            result.duration_seconds = monotonic() - start
        return result

    def _artifact_directory(self, workdir: Path) -> Path:
        parent = self._artifacts_root or Path(tempfile.gettempdir())
        if parent.resolve().is_relative_to(workdir):
            raise ValueError("evidence must be outside the source worktree")
        parent.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="nokinc-maestro-", dir=parent)).resolve(strict=True)

    @staticmethod
    def _directory_identity(directory: Path) -> tuple[tuple[int, int], ...]:
        identities = []
        for path in (directory, *directory.parents):
            if path.is_symlink() or path.is_junction():
                raise ValueError("redirected evidence directory is forbidden")
            state = path.stat()
            identities.append((state.st_dev, state.st_ino))
        return tuple(identities)