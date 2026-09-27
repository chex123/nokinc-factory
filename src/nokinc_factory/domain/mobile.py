"""Mobile adapter inputs and advisory results. Spec Parts 8 and 10.

Declaring a device, test inventory or expected build is not attestation that the
right binary was installed. Only a trusted pipeline can establish that binding;
the local adapter deliberately cannot produce authoritative release evidence.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nokinc_factory.ports.toolchain import GateResult


class MaestroSpec(BaseModel):
    """Trusted runner configuration, not authority supplied by issue/PR text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: Literal["ios", "android"]
    device_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    expected_tests: tuple[str, ...] = Field(min_length=1)
    flow_directory: str = ".maestro"
    timeout_seconds: int = Field(default=600, ge=1, le=3600, strict=True)

    @field_validator("flow_directory")
    @classmethod
    def _relative_directory(cls, value: str) -> str:
        if "\\" in value or ":" in value or any(
            part in ("", ".", "..") for part in value.split("/")
        ):
            raise ValueError("flow_directory must be a normalized repository-relative path")
        return value

    @field_validator("expected_tests")
    @classmethod
    def _inventory(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not name.strip() or name != name.strip() for name in value):
            raise ValueError("expected test names must be nonblank and canonical")
        if len(set(value)) != len(value):
            raise ValueError("expected test names must be distinct")
        return value


class MobileGateResult(GateResult):
    """A local invocation result is advisory even when its tests pass."""

    evidence_scope: Literal["local_advisory"] = "local_advisory"
    flow_digest: str = ""
    report_digest: str | None = None
    tests_run: int = 0
    artifact_directory: str | None = None