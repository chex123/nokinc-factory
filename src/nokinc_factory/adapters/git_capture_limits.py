"""Small, explicit resource bounds for one local optimistic capture."""

from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
from time import monotonic


@dataclass(frozen=True)
class CaptureLimits:
    max_files: int = 10_000
    max_file_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 32 * 1024 * 1024
    timeout_seconds: float = 60
    max_path_length: int = 4096
    max_path_depth: int = 64
    max_metadata_entries: int = 50_000

    def __post_init__(self) -> None:
        if any(type(value) is not int or value <= 0 for value in (
            self.max_files, self.max_file_bytes, self.max_total_bytes,
            self.max_path_length, self.max_path_depth, self.max_metadata_entries,
        )):
            raise ValueError("Capture count/byte/path limits must be positive integers")
        if (isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))):
            raise ValueError("Capture limits must be positive and finite")
        try:
            if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
                raise ValueError("Capture limits must be positive and finite")
        except OverflowError as exc:
            raise ValueError("Capture limits must be positive and finite") from exc


@dataclass
class CaptureBudget:
    limits: CaptureLimits
    clock: Callable[[], float] = monotonic
    started: float = field(init=False)
    raw_scans: int = 0
    file_reads: int = 0
    bytes_read: int = 0
    commands: int = 0

    def __post_init__(self) -> None:
        self.started = self.clock()

    def remaining(self) -> float:
        remaining = self.limits.timeout_seconds - (self.clock() - self.started)
        if remaining <= 0:
            raise ValueError("Capture deadline budget exceeded")
        return min(30.0, remaining)

    def inventory(self, count: int) -> None:
        self.remaining()
        if count > self.limits.max_files:
            raise ValueError("Capture file inventory limit exceeded")

    def path(self, value: str) -> str:
        """Check length before scanning/splitting, and depth before expanding parents."""
        self.remaining()
        if len(value) > self.limits.max_path_length:
            raise ValueError("Capture path length limit exceeded")
        if value.count("/") + value.count("\\") + 1 > self.limits.max_path_depth:
            raise ValueError("Capture path depth limit exceeded")
        return value

    def metadata(self, count: int) -> None:
        """Cap each expanded inventory/pass, including nonexistent attribute probes."""
        self.remaining()
        if count > self.limits.max_metadata_entries:
            raise ValueError("Capture metadata inventory limit exceeded")