"""Provider-neutral model invocation contract."""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field

from nokinc_factory.domain.review_base import Digest, Identifier, ReviewModel, Text


class ModelStatus(StrEnum):
    COMPLETED = "COMPLETED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class ModelRequest(ReviewModel):
    role: Identifier
    prompt: Text
    context_digest: Digest


class ModelResponse(ReviewModel):
    status: ModelStatus
    model: Identifier
    family: Identifier
    output: str = Field(default="", max_length=128_000)
    provider_execution_id: Identifier | None = None


@runtime_checkable
class ModelPort(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
