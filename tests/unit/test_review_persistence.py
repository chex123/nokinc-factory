"""Storage must accept exactly one replayable local transition, not forged snapshots."""

import pytest
from test_review_fixtures import at, begin, clean, finish, report, session

from nokinc_factory.domain.review_base import renewed
from nokinc_factory.policy.review_persistence import validate_extension


def test_creation_requires_an_empty_valid_seed() -> None:
    initial = session()
    assert validate_extension(None, initial) == "CREATE"
    with pytest.raises(ValueError, match="empty"):
        validate_extension(None, clean(initial, 1))


def test_exact_reservation_and_completion_are_valid_extensions() -> None:
    initial = session()
    reserved = begin(initial, 1)
    completed = finish(reserved, report(reserved))
    assert validate_extension(initial, reserved) == "RESERVE"
    assert validate_extension(reserved, completed) == "COMPLETE"
    assert validate_extension(completed, completed) == "UNCHANGED"


def test_cannot_skip_a_reservation_or_rewrite_history() -> None:
    initial = session()
    first = clean(initial, 1)
    with pytest.raises(ValueError, match="transition"):
        validate_extension(initial, first)
    second = clean(first, 2)
    with pytest.raises(ValueError, match="transition"):
        validate_extension(first, second)
    with pytest.raises(ValueError):
        validate_extension(second, initial)


def test_replayed_state_counters_cannot_be_forged_even_with_correct_hash() -> None:
    initial = session()
    forged = renewed(initial, state=renewed(initial.state, last_at=at(1)))
    with pytest.raises(ValueError, match="ledger"):
        validate_extension(None, forged)


def test_seeds_are_immutable_after_registration() -> None:
    first = session()
    altered = renewed(first, seed=renewed(first.seed, started_at=at(1)),
                      state=renewed(first.state, last_at=at(1)))
    with pytest.raises(ValueError, match="seed"):
        validate_extension(first, altered)


def test_forged_nonvalidating_model_copy_cannot_bypass_consumer_checks() -> None:
    initial = session()
    forged = initial.model_copy(update={"attempts": ("unvalidated",)})
    with pytest.raises(ValueError):
        validate_extension(initial, forged)