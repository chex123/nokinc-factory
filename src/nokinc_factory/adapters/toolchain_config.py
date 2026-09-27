"""Bounded loader for target `.factory/toolchain.yaml` declarations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from nokinc_factory.ports.toolchain import ToolchainSpec

MAX_TOOLCHAIN_BYTES = 1024 * 1024


def load_toolchain(path: Path) -> ToolchainSpec:
    if path.is_symlink() or not path.is_file():
        raise ValueError("toolchain contract is unavailable")
    data = path.read_bytes()
    if len(data) > MAX_TOOLCHAIN_BYTES:
        raise ValueError("toolchain contract exceeds byte limit")
    try:
        parsed: Any = yaml.safe_load(data)
    except yaml.YAMLError as exc:
        raise ValueError("toolchain contract is invalid YAML") from exc
    if not isinstance(parsed, dict):
        raise ValueError("toolchain contract must be a mapping")
    try:
        return ToolchainSpec.model_validate(parsed)
    except (TypeError, ValueError) as exc:
        raise ValueError("toolchain contract is invalid") from exc
