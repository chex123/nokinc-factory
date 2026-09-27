"""Deterministic model-port doubles and qualification boundaries."""

from __future__ import annotations

from uuid import uuid4

from nokinc_factory.ports.model import ModelPort, ModelRequest, ModelResponse, ModelStatus


class ModelUnavailable(RuntimeError):
    """A required model/provider is unavailable; no quality pass is implied."""


class FakeModelPort(ModelPort):
    """Offline deterministic provider double for workflow plumbing tests only."""

    def __init__(self, *, model: str, family: str, available: bool = True) -> None:
        self.model = model
        self.family = family
        self.available = available

    def complete(self, request: ModelRequest) -> ModelResponse:
        if not self.available:
            raise ModelUnavailable(f"model {self.model} is unavailable")
        return ModelResponse(
            status=ModelStatus.COMPLETED, model=self.model, family=self.family,
            output=f"FAKE_{request.role}_COMPLETED",
            provider_execution_id=f"fake-{uuid4().hex}",
        )
