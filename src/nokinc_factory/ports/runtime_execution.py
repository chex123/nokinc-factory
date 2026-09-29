"""Isolated runtime execution port. See Spec Parts 8 and 14."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from nokinc_factory.domain.runtime_evidence import RuntimeReceipt, RuntimeRequest


@runtime_checkable
class RuntimeExecutionPort(Protocol):
    """Execute one content-bound request in an isolated per-task worker.

    Implementations must not expose control-plane credentials, must enforce
    resource/time/egress limits outside the candidate process, and must return
    a worker-signed receipt. A local shell adapter is not a sandbox and does not
    satisfy this protocol.
    """

    def execute(self, request: RuntimeRequest) -> RuntimeReceipt:
        """Run the declared gate and return signed, candidate-bound evidence."""
        ...
