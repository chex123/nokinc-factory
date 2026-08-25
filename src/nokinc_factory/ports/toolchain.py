"""ToolchainPort. Spec Part 8.

Gate NAMES are universal. Gate IMPLEMENTATIONS come from the target repository's
declared toolchain. Without this the factory silently becomes biased toward
whatever language it happens to be written in.

Declared in each target repo as `.factory/toolchain.yaml`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class GateName(StrEnum):
    BUILD = "build"
    UNIT = "unit"
    ACCEPTANCE = "acceptance"
    TYPES = "types"
    COVERAGE = "coverage"
    MUTATION = "mutation"
    CONTRACT = "contract"


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    #: The toolchain has no runner for this gate. NEVER report PASS instead --
    #: a gate that cannot run has not passed.
    NOT_AVAILABLE = "NOT_AVAILABLE"


class GateResult(BaseModel):
    gate: GateName
    status: GateStatus
    duration_seconds: float = 0.0
    output: str = ""
    failures: list[dict[str, str]] = Field(
        default_factory=list,
        description="Machine-readable: file, line, rule, message. Feeds the repair loop.",
    )


class ToolchainSpec(BaseModel):
    """Contents of a target repo's `.factory/toolchain.yaml`.

    A gate with no command declared is NOT_AVAILABLE. Policy decides whether that
    blocks: for T2 a missing mutation runner is a blocker; for T1 it is recorded
    and accepted.
    """

    language: str
    commands: dict[GateName, str] = Field(default_factory=dict)

    def supports(self, gate: GateName) -> bool:
        return gate in self.commands


@runtime_checkable
class ToolchainPort(Protocol):
    def spec(self) -> ToolchainSpec: ...

    def run(self, gate: GateName, workdir: str) -> GateResult:
        """Run one gate. Must return NOT_AVAILABLE rather than PASS when unsupported."""
        ...


#: Reference toolchains for the demo targets.
REFERENCE: dict[str, ToolchainSpec] = {
    "python": ToolchainSpec(
        language="python",
        commands={
            GateName.BUILD: "python -m build --wheel",
            GateName.UNIT: "pytest tests/unit -q",
            GateName.ACCEPTANCE: "pytest tests/acceptance -q",
            GateName.TYPES: "mypy --strict src",
            GateName.COVERAGE: "pytest --cov=src --cov-report=xml -q",
        },
    ),
    "typescript": ToolchainSpec(
        language="typescript",
        commands={
            GateName.BUILD: "npm run build",
            GateName.UNIT: "npx vitest run",
            GateName.ACCEPTANCE: "npx cucumber-js",
            GateName.TYPES: "npx tsc --noEmit",
            GateName.COVERAGE: "npx vitest run --coverage",
        },
    ),
    # No unit or mutation runner for HCL -- both report NOT_AVAILABLE, by design.
    "hcl": ToolchainSpec(
        language="hcl",
        commands={
            GateName.BUILD: "terraform init -backend=false && terraform validate",
            GateName.TYPES: "terraform fmt -check -recursive",
        },
    ),
}
