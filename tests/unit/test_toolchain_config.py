from pathlib import Path

import pytest

from nokinc_factory.adapters.toolchain_config import load_toolchain
from nokinc_factory.ports.toolchain import GateName


def test_load_toolchain_yaml_maps_declared_commands(tmp_path: Path) -> None:
    path = tmp_path / "toolchain.yaml"
    path.write_text(
        "language: python\ncommands:\n  build: python -m build\n  types: mypy --strict src\n",
        encoding="utf-8",
    )

    spec = load_toolchain(path)

    assert spec.language == "python"
    assert spec.commands[GateName.BUILD] == "python -m build"
    assert spec.supports(GateName.TYPES)


def test_invalid_or_oversized_toolchain_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "toolchain.yaml"
    path.write_text("language: python\ncommands: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_toolchain(path)

    path.write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="byte"):
        load_toolchain(path)
