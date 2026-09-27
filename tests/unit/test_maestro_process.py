"""Exercise real subprocess plumbing with a synthetic reporter, NOT Maestro."""

import os
import sys
from pathlib import Path

import pytest

from nokinc_factory.adapters.maestro import SubprocessMaestroRunner


def test_subprocess_uses_explicit_environment_and_literal_arguments(tmp_path: Path) -> None:
    destination = tmp_path / "result with spaces.txt"
    script = (
        "import os,sys; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(os.environ['SYNTHETIC_VALUE'] + sys.argv[2])"
    )
    result = SubprocessMaestroRunner(environment={"SYNTHETIC_VALUE": "expected"}).run(
        (sys.executable, "-c", script, str(destination), ";not-a-shell-command"),
        cwd=tmp_path, timeout_seconds=10,
    )
    assert result == 0
    assert destination.read_text() == "expected;not-a-shell-command"


def test_subprocess_does_not_inherit_provider_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNTHETIC_SECRET", "not-real")
    assert os.environ["SYNTHETIC_SECRET"]
    result = SubprocessMaestroRunner().run(
        (sys.executable, "-c", "import os,sys; sys.exit('SYNTHETIC_SECRET' in os.environ)"),
        cwd=tmp_path, timeout_seconds=10,
    )
    assert result == 0