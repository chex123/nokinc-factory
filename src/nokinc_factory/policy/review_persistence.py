"""Verify a single durable extension before spending or storing caller-supplied state.

This is independent of database/provider choice. A digest alone does not prove
that a session follows its previously committed state. Spec Part 1 and ARP-1.
"""

from typing import Literal

from nokinc_factory.domain.review_session import ReviewSession
from nokinc_factory.policy.review import (
    complete_invocation,
    reserve_invocation,
    validate_session,
)

TransitionKind = Literal["CREATE", "RESERVE", "COMPLETE", "UNCHANGED"]


def validate_extension(current: ReviewSession | None, proposed: ReviewSession) -> TransitionKind:
    """Only creation, one reservation, or settlement of the committed pending call."""
    proposed = validate_session(proposed)
    if current is None:
        if proposed.attempts:
            raise ValueError("New sessions must be empty and independently registered")
        return "CREATE"
    current = validate_session(current)
    if current.seed != proposed.seed:
        raise ValueError("Registered seed cannot change")
    if current == proposed:
        return "UNCHANGED"
    expected: ReviewSession | None = None
    kind: TransitionKind
    if (current.state.pending is None
            and len(proposed.attempts) == len(current.attempts) + 1
            and proposed.attempts[:-1] == current.attempts):
        attempt = proposed.attempts[-1]
        if attempt.completion is not None:
            raise ValueError("A reservation and completion need separate committed transitions")
        invocation = attempt.invocation
        expected = reserve_invocation(
            current, invocation_id=invocation.invocation_id, kind=invocation.kind,
            actor=invocation.actor, reservation=invocation.reservation, now=invocation.started_at,
        )
        kind = "RESERVE"
    elif (current.state.pending is not None and len(proposed.attempts) == len(current.attempts)
          and proposed.attempts[:-1] == current.attempts[:-1]
          and proposed.attempts[-1].invocation == current.attempts[-1].invocation):
        completion = proposed.attempts[-1].completion
        if completion is None:
            raise ValueError("A pending transition has no completion")
        expected = complete_invocation(
            current, invocation_id=completion.invocation_id, receipt=completion.receipt,
            output=completion.output_json, now=completion.finished_at,
            failure_reason=completion.failure_reason,
        )
        kind = "COMPLETE"
    else:
        raise ValueError("Session is not the next committed transition")
    if expected != proposed:
        raise ValueError("Proposed state disagrees with the next transition")
    return kind