"""Provider-neutral model invocation contract."""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field

from nokinc_factory.domain.review_base import Count, Digest, Identifier, ReviewModel, Text


class ModelStatus(StrEnum):
    COMPLETED = "COMPLETED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class ModelRequest(ReviewModel):
    role: Identifier
    prompt: Text
    context_digest: Digest


class ModelUsage(ReviewModel):
    """Provider-reported token usage split into billable rate categories.

    ``input_tokens`` excludes cache reads and writes; those are recorded in
    their own fields so a rate card can price each category without guessing.
    """

    input_tokens: Count
    cached_input_tokens: Count = 0
    cache_write_input_tokens: Count = 0
    output_tokens: Count


class ModelResponse(ReviewModel):
    status: ModelStatus
    model: Identifier
    family: Identifier
    output: str = Field(default="", max_length=128_000)
    provider_execution_id: Identifier | None = None
    usage: ModelUsage | None = None


@runtime_checkable
class ModelPort(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
