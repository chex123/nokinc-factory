"""Mobile gate CLI is IDE-independent and cannot declare authoritative evidence."""

import json
from pathlib import Path

import pytest

from nokinc_factory.adapters.maestro import MaestroToolchain
from nokinc_factory.cli import main
from nokinc_factory.domain.mobile import MobileGateResult
from nokinc_factory.ports.toolchain import GateName, GateStatus


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [(GateStatus.PASS, 0), (GateStatus.FAIL, 1), (GateStatus.NOT_AVAILABLE, 2)],
)
def test_cli_propagates_gate_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    status: GateStatus, expected_exit: int,
) -> None:
    def result(self: MaestroToolchain, gate: GateName, workdir: str) -> MobileGateResult:
        assert gate is GateName.MOBILE_E2E
        assert workdir == str(tmp_path)
        return MobileGateResult(gate=gate, status=status)

    monkeypatch.setattr(MaestroToolchain, "run", result)
    code = main([
        "mobile-test", "--repo", str(tmp_path), "--platform", "android",
        "--device", "emulator-5554", "--expected-test", "login",
    ])
    output = json.loads(capsys.readouterr().out)
    assert code == expected_exit
    assert output["status"] == status.value
    assert output["evidence_scope"] == "local_advisory"


def test_bad_mobile_config_is_controlled_error(capsys: pytest.CaptureFixture[str]) -> None:
    code = main([
        "mobile-test", "--platform", "android", "--device", "emulator-5554",
        "--expected-test", "login", "--flow-directory", "../outside",
    ])
    assert code == 2
    assert "Invalid mobile test configuration" in capsys.readouterr().err