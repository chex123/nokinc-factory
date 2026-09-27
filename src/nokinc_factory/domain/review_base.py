"""Owned, content-addressed values for ARP-1; hashes are integrity, not authentication.

Every consuming policy operation revalidates instances, including nested
``model_copy`` values. JSON round trips retain hashes; constructors compute them
when absent and reject incorrect supplied hashes. Tuples and frozen children
prevent caller-owned containers from changing the recorded facts afterwards.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from json import dumps
from typing import Annotated, Self

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Review facts must not be blank")
    return value


def utc(value: datetime) -> datetime:
    """Injected aware time only: guessing a zone could open an approval window."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware injected time is required")
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError("Timestamp is outside the supported UTC range") from exc


Identifier = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")]
Text = Annotated[str, Field(strict=True, min_length=1), AfterValidator(nonblank)]
Digest = Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
Limit = Annotated[int, Field(strict=True, gt=0)]
Moment = Annotated[AwareDatetime, AfterValidator(utc)]


def content_digest(value: object) -> str:
    encoded = dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":")).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def distinct(values: Iterable[str]) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ValueError("Review identities must be distinct")


class ReviewModel(BaseModel):
    """Recursively revalidated value, deliberately not a signed identity claim."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always",
                              validate_default=True)
    content_digest: str = Field(default="", strict=True)

    @model_validator(mode="after")
    def _content_address(self) -> Self:
        expected = content_digest(self.model_dump(mode="json", exclude={"content_digest"}))
        if self.content_digest and self.content_digest != expected:
            raise ValueError("Review content_digest mismatch")
        object.__setattr__(self, "content_digest", expected)
        return self


def renewed[R: ReviewModel](value: R, **changes: object) -> R:
    """A validated replacement, never Pydantic's nonvalidating copy/update path."""
    return type(value).model_validate(value.model_dump(exclude={"content_digest"}) | changes)