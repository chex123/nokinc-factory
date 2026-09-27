"""Required ARP-1 persistence extension; deliberately no in-memory implementation.

A04 owns durable atomicity. Implementations must enforce authenticated tenant
scope, immutable seed authority, CAS/freshness, execution replay dedupe and
aggregate child/parent reservations in ONE transaction before calls. Contract
versions, new sessions, model switches and reparenting must not refill budgets.
Returning True without those guarantees is not an implementation of this port.
"""

from typing import Protocol

from nokinc_factory.domain.review import ReviewScope
from nokinc_factory.domain.review_base import Digest, Identifier
from nokinc_factory.domain.review_session import ReviewSession


class ReviewSessionStore(Protocol):
    """Trusted store, not an authority claim available for an LLM to populate."""

    def load_current(self, scope: ReviewScope) -> ReviewSession | None:
        """Load actual durable latest state; deny cross-tenant access and rollback."""
        ...

    def compare_and_reserve(self, *, scope: ReviewScope, expected_digest: Digest | None,
                            updated: ReviewSession, parent_budget_id: Identifier) -> bool:
        """Atomically CAS the ledger AND charge/reserve aggregate parent resources.

        Verify the proposed transition by the same reducers, not only its hash.
        None is create-if-absent, not permission to reset an existing unit. False
        means no writes/reservations/execution; callers reload, never spin/retry
        beyond the already authorized bounds. Persist completion actual usage
        even for overruns, failed calls and lost acknowledgements.
        """
        ...