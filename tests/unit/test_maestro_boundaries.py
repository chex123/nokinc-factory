"""Boundary regressions for untrusted flow trees and independently stored reports."""

import sys
from pathlib import Path

import pytest
from test_maestro_toolchain import REPORT, Runner, setup

from nokinc_factory.adapters import maestro_inputs
from nokinc_factory.adapters.maestro import MaestroToolchain, SubprocessMaestroRunner
from nokinc_factory.adapters.maestro_inputs import flow_digest
from nokinc_factory.domain.mobile import MaestroSpec
from nokinc_factory.ports.toolchain import GateName, GateStatus


def test_windows_profile_fails_closed_without_spawning_native_wrapper(tmp_path: Path) -> None:
    runner = Runner()
    adapter, repo = setup(tmp_path, runner, host="Windows")
    assert adapter.run(GateName.MOBILE_E2E, str(repo)).status is GateStatus.NOT_AVAILABLE
    assert runner.calls == []


def test_nested_flow_inputs_are_hashed(tmp_path: Path) -> None:
    runner = Runner()
    adapter, repo = setup(tmp_path, runner)
    support = repo / ".maestro" / "support"
    support.mkdir()
    fixture = support / "fixture.json"
    fixture.write_text('{"synthetic": 1}')
    first = adapter.run(GateName.MOBILE_E2E, str(repo))
    fixture.write_text('{"synthetic": 2}')
    second = adapter.run(GateName.MOBILE_E2E, str(repo))
    assert first.flow_digest != second.flow_digest


def test_artifacts_cannot_be_written_into_source_worktree(tmp_path: Path) -> None:
    runner = Runner()
    _, repo = setup(tmp_path, runner)
    adapter = MaestroToolchain(
        MaestroSpec(platform="android", device_id="device", expected_tests=("login",)),
        runner=runner, host_system="Linux", artifacts_root=repo / "results",
    )
    assert adapter.run(GateName.MOBILE_E2E, str(repo)).status is GateStatus.FAIL
    assert runner.calls == []


@pytest.mark.parametrize("redirect", ["is_symlink", "is_junction"])
def test_redirected_flow_paths_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redirect: str,
) -> None:
    runner = Runner()
    adapter, repo = setup(tmp_path, runner)
    monkeypatch.setattr(Path, redirect, lambda path: path.name == "login.yaml")
    assert adapter.run(GateName.MOBILE_E2E, str(repo)).status is GateStatus.FAIL
    assert runner.calls == []


def test_inputs_have_file_and_byte_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "flow.yaml").write_text("synthetic")
    monkeypatch.setattr(maestro_inputs, "MAX_FLOW_BYTES", 2)
    with pytest.raises(ValueError, match="size"):
        flow_digest(tmp_path)
    monkeypatch.setattr(maestro_inputs, "MAX_FLOW_BYTES", 50_000_000)
    monkeypatch.setattr(maestro_inputs, "MAX_FLOW_FILES", 0)
    with pytest.raises(ValueError, match="size"):
        flow_digest(tmp_path)


def test_failed_and_skipped_flows_propagate_fail(tmp_path: Path) -> None:
    report = b'<testsuite><testcase name="login"><skipped/></testcase></testsuite>'
    adapter, repo = setup(tmp_path, Runner(report=report))
    result = adapter.run(GateName.MOBILE_E2E, str(repo))
    assert result.status is GateStatus.FAIL
    assert result.tests_run == 1


def test_real_subprocess_report_lifecycle_is_not_an_emulator_test(tmp_path: Path) -> None:
    class SyntheticReporter:
        def run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int) -> int:
            code = (
                "import sys; from pathlib import Path; "
                f"Path(sys.argv[1]).write_bytes({REPORT!r})"
            )
            return SubprocessMaestroRunner(environment={}).run(
                (sys.executable, "-c", code, argv[argv.index("--output") + 1]),
                cwd=cwd, timeout_seconds=timeout_seconds,
            )

    _, repo = setup(tmp_path, Runner())
    adapter = MaestroToolchain(
        MaestroSpec(platform="android", device_id="device", expected_tests=("login",)),
        runner=SyntheticReporter(), host_system="Linux", artifacts_root=tmp_path / "evidence",
    )
    result = adapter.run(GateName.MOBILE_E2E, str(repo))
    assert result.status is GateStatus.PASS
    assert result.evidence_scope == "local_advisory"
    assert Path(result.artifact_directory or "").joinpath("report.xml").is_file()


def test_replaced_artifact_directory_invalidates_report(tmp_path: Path) -> None:
    class ReplacingRunner(Runner):
        def run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int) -> int:
            destination = Path(argv[argv.index("--output") + 1]).parent
            destination.rename(destination.with_name(destination.name + "-old"))
            destination.mkdir()
            return super().run(argv, cwd=cwd, timeout_seconds=timeout_seconds)

    adapter, repo = setup(tmp_path, ReplacingRunner())
    assert adapter.run(GateName.MOBILE_E2E, str(repo)).status is GateStatus.FAIL


def test_directory_only_tree_consumes_discovery_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "flow.yaml").write_text("synthetic")
    for n in range(10):
        (tmp_path / f"empty-{n}").mkdir()
    monkeypatch.setattr(maestro_inputs, "MAX_FLOW_ENTRIES", 5, raising=False)
    with pytest.raises(ValueError, match="discovery"):
        flow_digest(tmp_path)


def test_flow_depth_is_bounded(tmp_path: Path) -> None:
    nested = tmp_path
    for _ in range(20):
        nested = nested / "d"
        nested.mkdir()
    (nested / "flow.yaml").write_text("synthetic")
    with pytest.raises(ValueError, match="depth"):
        flow_digest(tmp_path)