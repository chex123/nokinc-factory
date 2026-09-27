import json
from pathlib import Path

from nokinc_factory.cli import main


def test_run_gate_executes_declared_command_without_shell(tmp_path: Path, capsys) -> None:
    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "toolchain.yaml").write_text(
        "language: test\ncommands:\n  build: python -c \"print('gate-ok')\"\n",
        encoding="utf-8",
    )

    result = main(["run-gate", "build", "--repo", str(tmp_path)])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert "gate-ok" in payload["output"]


def test_run_gate_reports_unsupported_gate_as_unavailable(tmp_path: Path, capsys) -> None:
    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "toolchain.yaml").write_text(
        "language: hcl\ncommands:\n  build: terraform validate\n",
        encoding="utf-8",
    )

    result = main(["run-gate", "unit", "--repo", str(tmp_path)])

    assert result == 2
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_AVAILABLE"
