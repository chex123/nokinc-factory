"""Behavior of the advisory Maestro adapter; no emulator or real model used."""

from collections.abc import Callable
from pathlib import Path
from subprocess import TimeoutExpired

import pytest
from pydantic import ValidationError

from nokinc_factory.adapters.maestro import MaestroToolchain
from nokinc_factory.domain.mobile import MaestroSpec
from nokinc_factory.ports.toolchain import GateName, GateStatus, ToolchainPort

REPORT = b'<testsuite tests="1"><testcase name="login" status="SUCCESS"/></testsuite>'


class Runner:
    def __init__(
        self, *, report: bytes | None = REPORT, code: int = 0,
        error: Exception | None = None, mutate: Callable[[], None] | None = None,
    ) -> None:
        self.report = report
        self.code = code
        self.error = error
        self.mutate = mutate
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int) -> int:
        self.calls.append(argv)
        assert cwd.is_dir()
        assert timeout_seconds == 60
        if self.error:
            raise self.error
        if self.mutate:
            self.mutate()
        if self.report is not None:
            Path(argv[argv.index("--output") + 1]).write_bytes(self.report)
        return self.code


def setup(tmp_path: Path, runner: Runner, *, platform: str = "android", host: str = "Linux"):
    repo = tmp_path / "repo"
    flow_dir = repo / ".maestro"
    flow_dir.mkdir(parents=True)
    (flow_dir / "login.yaml").write_text(
        "appId: com.example.synthetic\nname: login\n---\n- launchApp\n"
        "- assertVisible: Synthetic\n", encoding="utf-8",
    )
    spec = MaestroSpec(
        platform=platform, device_id="emulator-5554", expected_tests=("login",),
        timeout_seconds=60,
    )
    adapter = MaestroToolchain(spec, runner=runner, host_system=host,
                               artifacts_root=tmp_path / "artifacts")
    return adapter, repo


def test_maestro_extends_universal_toolchain_without_claiming_other_gates(tmp_path: Path) -> None:
    adapter, repo = setup(tmp_path, Runner())
    assert isinstance(adapter, ToolchainPort)
    assert adapter.spec().supports(GateName.MOBILE_E2E)
    assert not adapter.spec().supports(GateName.VISUAL)
    assert adapter.run(GateName.VISUAL, str(repo)).status is GateStatus.NOT_AVAILABLE


def test_run_captures_junit_and_binds_flow_input_as_advisory(tmp_path: Path) -> None:
    runner = Runner()
    adapter, repo = setup(tmp_path, runner)
    result = adapter.run(GateName.MOBILE_E2E, str(repo))
    assert result.status is GateStatus.PASS
    assert result.evidence_scope == "local_advisory"
    assert result.tests_run == 1
    assert result.flow_digest.startswith("sha256:")
    assert result.report_digest is not None
    argv = runner.calls[0]
    assert argv[:8] == (
        "maestro", "--device", "emulator-5554", "--platform", "android", "test",
        "--format", "junit",
    )
    assert "--test-output-dir" in argv and "--debug-output" in argv
    assert argv[-1] == str(repo / ".maestro")
    assert "--analyze" not in argv


@pytest.mark.parametrize(
    ("runner", "status"),
    [
        (Runner(code=1), GateStatus.FAIL), (Runner(report=None), GateStatus.FAIL),
        (Runner(report=b"invalid"), GateStatus.FAIL),
        (Runner(error=FileNotFoundError("private path")), GateStatus.NOT_AVAILABLE),
        (Runner(error=TimeoutExpired("cmd", 60)), GateStatus.FAIL),
        (Runner(error=OSError("private path")), GateStatus.FAIL),
    ],
)
def test_unavailable_failed_timeout_and_missing_evidence_never_pass(
    tmp_path: Path, runner: Runner, status: GateStatus,
) -> None:
    adapter, repo = setup(tmp_path, runner)
    result = adapter.run(GateName.MOBILE_E2E, str(repo))
    assert result.status is status
    assert "private path" not in result.output


def test_ios_requires_macos_and_does_not_execute_on_linux(tmp_path: Path) -> None:
    runner = Runner()
    adapter, repo = setup(tmp_path, runner, platform="ios")
    assert adapter.run(GateName.MOBILE_E2E, str(repo)).status is GateStatus.NOT_AVAILABLE
    assert runner.calls == []


def test_ios_can_use_provisioned_macos_runner(tmp_path: Path) -> None:
    adapter, repo = setup(tmp_path, Runner(), platform="ios", host="Darwin")
    assert adapter.run(GateName.MOBILE_E2E, str(repo)).status is GateStatus.PASS


def test_no_shared_or_preexisting_report_can_satisfy_a_new_run(tmp_path: Path) -> None:
    runner = Runner()
    adapter, repo = setup(tmp_path, runner)
    first = adapter.run(GateName.MOBILE_E2E, str(repo))
    runner.report = None
    second = adapter.run(GateName.MOBILE_E2E, str(repo))
    assert first.status is GateStatus.PASS
    assert second.status is GateStatus.FAIL
    assert first.artifact_directory != second.artifact_directory


def test_flow_changes_during_execution_invalidate_result(tmp_path: Path) -> None:
    runner = Runner()
    adapter, repo = setup(tmp_path, runner)
    runner.mutate = lambda: (repo / ".maestro" / "login.yaml").write_text("changed")
    assert adapter.run(GateName.MOBILE_E2E, str(repo)).status is GateStatus.FAIL


@pytest.mark.parametrize("flow_directory", ["../other", "/absolute", "C:/other", "a/../b", "."])
def test_flow_directory_is_repository_relative(flow_directory: str) -> None:
    with pytest.raises(ValidationError):
        MaestroSpec(platform="android", device_id="device", expected_tests=("login",),
                    flow_directory=flow_directory)


@pytest.mark.parametrize("tests", [(), ("",), ("login", "login")])
def test_nonempty_distinct_inventory_required(tests: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        MaestroSpec(platform="android", device_id="device", expected_tests=tests)


@pytest.mark.parametrize("seconds", [0, -1, 3601])
def test_timeout_is_bounded(seconds: int) -> None:
    with pytest.raises(ValidationError):
        MaestroSpec(platform="android", device_id="device", expected_tests=("login",),
                    timeout_seconds=seconds)


def test_missing_flow_tree_fails_before_execution(tmp_path: Path) -> None:
    runner = Runner()
    adapter, repo = setup(tmp_path, runner)
    (repo / ".maestro" / "login.yaml").unlink()
    assert adapter.run(GateName.MOBILE_E2E, str(repo)).status is GateStatus.FAIL
    assert runner.calls == []