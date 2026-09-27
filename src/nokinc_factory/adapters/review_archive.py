"""Bounded local review archive reader; importing data never authorizes a live run."""

import json
from pathlib import Path

from nokinc_factory.adapters.git_capture_io import read_regular
from nokinc_factory.domain.review_session import ReviewSession
from nokinc_factory.policy.review import validate_session

MAX_ARCHIVE_BYTES = 4 * 1024 * 1024


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Ambiguous archive fields")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Invalid archive JSON number")


def read_review_archive(path: Path) -> ReviewSession:
    """Regular file, bounded bytes/attempts, strict JSON and replayed content digests."""
    content, _ = read_regular(path.absolute(), max_bytes=MAX_ARCHIVE_BYTES)
    data: object = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_fields,
                              parse_constant=_reject_constant)
    if not isinstance(data, dict) or not isinstance(data.get("attempts"), list):
        raise ValueError("Invalid archive shape")
    if len(data["attempts"]) > 64:
        raise ValueError("Archive exceeds inspection attempt budget")
    try:
        return validate_session(ReviewSession.model_validate(data))
    except OverflowError as exc:
        raise ValueError("Archive numbers exceed supported replay arithmetic") from exc