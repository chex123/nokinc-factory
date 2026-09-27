"""Deterministic qualification checks around provider-neutral model responses."""

from nokinc_factory.adapters.model_qualification import ModelUnavailable
from nokinc_factory.ports.model import ModelPort, ModelRequest, ModelResponse, ModelStatus


class ModelQualificationError(ValueError):
    """A model response cannot count as qualified evidence for this role."""


def invoke_qualified(
    port: ModelPort, request: ModelRequest, *, producer_family: str,
) -> ModelResponse:
    response = port.complete(request)
    if response.status is not ModelStatus.COMPLETED:
        raise ModelQualificationError("model response did not complete")
    if not response.model.strip():
        raise ModelQualificationError("model identity is missing")
    if not response.family.strip() or response.family == producer_family:
        raise ModelQualificationError("reviewer family is missing or not independent")
    if not response.output.strip():
        raise ModelQualificationError("model output is empty")
    return response


__all__ = ["ModelQualificationError", "ModelUnavailable", "invoke_qualified"]
