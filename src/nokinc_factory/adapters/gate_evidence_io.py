"""Bounded structured evidence loading; file contents are data, never authority."""

import json
from pathlib import Path

from nokinc_factory.adapters.git_capture_io import read_regular
from nokinc_factory.domain.review_base import ReviewModel


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("Duplicate evidence field")
        value[name] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError("Unsupported JSON numeric constant")


def read_evidence[Document: ReviewModel](
    path: Path, model: type[Document], *, max_bytes: int = 4 * 1024 * 1024,
) -> Document:
    raw, _ = read_regular(path.absolute(), max_bytes=max_bytes)
    data: object = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                              parse_constant=_reject_constant)
    return model.model_validate(data)