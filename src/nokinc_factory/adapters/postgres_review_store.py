"""Durable PostgreSQL ARP-1 CAS and aggregate reservations, not a public authorization API.

The trusted service injects a tenant grant and a nonowner, NOBYPASSRLS database
role. It must authenticate that grant, approve seed registration and verify real
provider receipts. Never expose this object/connection to model-controlled code.
Lock parent before session; commit counters, snapshot and immutable event together.
DB connection/commit errors propagate: an unknown commit outcome is never False.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import Table, and_, func, select, text
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement

from nokinc_factory.adapters.postgres_review_admin import PostgresReviewAdmin as PostgresReviewAdmin
from nokinc_factory.adapters.postgres_review_admin import ReviewStoreDenied as ReviewStoreDenied
from nokinc_factory.adapters.postgres_review_schema import (
    COUNTERS,
    budgets,
    events,
    registrations,
    require_postgres,
    sessions,
)
from nokinc_factory.domain.review import ReviewScope
from nokinc_factory.domain.review_budget import ParentBudget, ParentUsage, ReviewStoreGrant
from nokinc_factory.domain.review_session import BudgetState, ReviewSession
from nokinc_factory.policy.review import validate_session
from nokinc_factory.policy.review_persistence import TransitionKind, validate_extension

MAX_SESSION_BYTES = 4 * 1024 * 1024


def _scope(table: Table, scope: ReviewScope) -> ColumnElement[bool]:
    return and_(table.c.tenant_id == scope.tenant_id, table.c.work_item_id == scope.work_item_id,
                table.c.session_id == scope.session_id)


def _decode(row: RowMapping) -> ReviewSession:
    if row["format_version"] != 1 or len(row["document"].encode("utf-8")) > MAX_SESSION_BYTES:
        raise ReviewStoreDenied("Unsupported or oversized stored session")
    result = validate_session(ReviewSession.model_validate_json(row["document"]))
    if result.content_digest != row["digest"]:
        raise ReviewStoreDenied("Stored session digest disagrees with payload")
    return result


class PostgresReviewSessionStore:
    def __init__(self, engine: Engine, *, grant: ReviewStoreGrant) -> None:
        require_postgres(engine)
        self._engine = engine
        self.grant = ReviewStoreGrant.model_validate(grant)

    @contextmanager
    def _transaction(self, scope: ReviewScope | None = None) -> Iterator[Connection]:
        if scope is not None and scope.tenant_id != self.grant.tenant_id:
            raise ReviewStoreDenied("Cross-tenant review access denied")
        with self._engine.begin() as connection:
            unsafe = connection.scalar(text(
                "SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb "
                "FROM pg_roles WHERE rolname=current_user"
            ))
            if unsafe:
                raise ReviewStoreDenied("Worker must not bypass row-level security")
            unsafe_tables = connection.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_class WHERE relname IN "
                "('factory_review_budget', 'factory_review_registration', "
                "'factory_review_session', 'factory_review_event') "
                "AND pg_has_role(current_user, relowner, 'USAGE')) "
                "OR has_column_privilege(current_user, 'factory_review_budget', "
                "'limits_json', 'UPDATE') "
                "OR has_table_privilege(current_user, 'factory_review_registration', 'INSERT') "
                "OR has_any_column_privilege(current_user, 'factory_review_event', 'UPDATE') "
                "OR has_table_privilege(current_user, 'factory_review_event', 'DELETE') "
                "OR has_table_privilege(current_user, 'factory_review_event', 'TRUNCATE')"
            ))
            if unsafe_tables:
                raise ReviewStoreDenied("Worker has unsafe inherited/administrative privileges")
            connection.execute(text("SELECT set_config('factory.tenant_id', :tenant, true)"),
                               {"tenant": self.grant.tenant_id})
            yield connection

    def _registration(self, connection: Connection, scope: ReviewScope) -> RowMapping:
        row = connection.execute(select(registrations).where(
            _scope(registrations, scope))).mappings().one_or_none()
        if row is None:
            raise ReviewStoreDenied("Unit has no independently approved registration")
        return row

    def load_current(self, scope: ReviewScope) -> ReviewSession | None:
        scope = ReviewScope.model_validate(scope)
        with self._transaction(scope) as connection:
            registration = self._registration(connection, scope)
            connection.execute(select(budgets.c.budget_id).where(
                budgets.c.tenant_id == scope.tenant_id,
                budgets.c.budget_id == registration["budget_id"],
            ).with_for_update(read=True)).one()
            row = connection.execute(select(sessions).where(
                _scope(sessions, scope))).mappings().one_or_none()
            self._check_tip(connection, scope, row)
            if row is None:
                return None
            result = _decode(row)
            if (result.seed.scope != scope
                    or result.seed.content_digest != registration["seed_digest"]):
                raise ReviewStoreDenied("Stored seed disagrees with registered authority")
            return result

    @staticmethod
    def _check_tip(connection: Connection, scope: ReviewScope, row: RowMapping | None) -> None:
        tip = connection.execute(select(events).where(_scope(events, scope))
                                 .order_by(events.c.sequence.desc()).limit(1)).mappings().one_or_none()
        if row is None and tip is None:
            return
        if (row is None or tip is None or row["sequence"] != tip["sequence"]
                or row["digest"] != tip["after_digest"] or row["document"] != tip["document"]):
            raise ReviewStoreDenied("Stored snapshot disagrees with immutable audit tip")

    def usage(self, parent_budget_id: str) -> ParentUsage:
        with self._transaction() as connection:
            row = connection.execute(select(budgets).where(
                budgets.c.tenant_id == self.grant.tenant_id,
                budgets.c.budget_id == parent_budget_id,
            )).mappings().one_or_none()
            if row is None:
                raise ReviewStoreDenied("Parent budget unavailable")
            return ParentUsage.model_validate({key: row[key] for key in COUNTERS})

    def history(self, scope: ReviewScope) -> tuple[ReviewSession, ...]:
        with self._transaction(scope) as connection:
            self._registration(connection, scope)
            rows = connection.execute(select(events).where(_scope(events, scope))
                                      .order_by(events.c.sequence)).mappings()
            result: list[ReviewSession] = []
            previous: ReviewSession | None = None
            for row in rows:
                item = validate_session(ReviewSession.model_validate_json(row["document"]))
                if (row["sequence"] != len(result) + 1 or row["after_digest"] != item.content_digest
                        or row["before_digest"] != (previous.content_digest if previous else None)
                        or row["kind"] != validate_extension(previous, item)):
                    raise ReviewStoreDenied("Audit event chain is inconsistent")
                result.append(item)
                previous = item
            return tuple(result)

    def compare_and_reserve(self, *, scope: ReviewScope, expected_digest: str | None,
                            updated: ReviewSession, parent_budget_id: str) -> bool:
        scope = ReviewScope.model_validate(scope)
        updated = validate_session(updated)
        if updated.seed.scope != scope:
            raise ReviewStoreDenied("Session scope mismatch")
        size = len(updated.model_dump_json().encode("utf-8"))
        if size > MAX_SESSION_BYTES:
            raise ReviewStoreDenied("Session exceeds persistence byte limit")
        try:
            with self._transaction(scope) as connection:
                registration = self._registration(connection, scope)
                if (registration["budget_id"] != parent_budget_id
                        or registration["seed_digest"] != updated.seed.content_digest):
                    raise ReviewStoreDenied("Seed or parent not approved for this unit")
                parent = connection.execute(select(budgets).where(
                    budgets.c.tenant_id == scope.tenant_id,
                    budgets.c.work_item_id == scope.work_item_id,
                    budgets.c.budget_id == parent_budget_id,
                ).with_for_update()).mappings().one()
                row = connection.execute(select(sessions).where(
                    _scope(sessions, scope)).with_for_update()).mappings().one_or_none()
                self._check_tip(connection, scope, row)
                current = _decode(row) if row is not None else None
                if current is not None and current == updated:
                    return False  # Replay never grants another dispatch right.
                if expected_digest != (current.content_digest if current is not None else None):
                    return False  # Nothing written before the CAS decision.
                kind = validate_extension(current, updated)
                if kind in ("CREATE", "RESERVE") and size > MAX_SESSION_BYTES // 2:
                    raise ReviewStoreDenied("Session byte limit requires settlement headroom")
                counters = self._counters(connection, parent, current, updated, kind)
                connection.execute(budgets.update().where(
                    budgets.c.tenant_id == scope.tenant_id, budgets.c.budget_id == parent_budget_id,
                ).values(**counters))
                sequence = int(row["sequence"]) + 1 if row is not None else 1
                values = {"document": updated.model_dump_json(), "digest": updated.content_digest,
                          "sequence": sequence, "format_version": 1}
                if row is None:
                    connection.execute(sessions.insert().values(
                        tenant_id=scope.tenant_id, work_item_id=scope.work_item_id,
                        session_id=scope.session_id, **values))
                else:
                    connection.execute(sessions.update().where(_scope(sessions, scope))
                                       .values(**values))
                self._append_event(connection, updated, current, kind, sequence)
            return True
        except IntegrityError as exc:
            # Rollback has completed; unique execution/invocation collisions deny
            # this completion without losing the prior held reservation.
            raise ReviewStoreDenied("Conflicting execution; reload and reconcile") from exc

    @staticmethod
    def _counters(connection: Connection, parent: RowMapping, current: ReviewSession | None,
                  updated: ReviewSession, kind: TransitionKind) -> dict[str, int]:
        old, new = current.budget if current is not None else BudgetState(), updated.budget
        values = {key: int(parent[key]) + int(getattr(new, key)) - int(getattr(old, key))
                  for key in COUNTERS}
        if any(value < 0 for value in values.values()):
            raise ReviewStoreDenied("Aggregate budget accounting underflow")
        if kind == "RESERVE":
            limits = ParentBudget.model_validate_json(parent["limits_json"])
            now = connection.scalar(select(func.clock_timestamp()))
            assert isinstance(now, datetime)
            invocation = updated.state.pending
            assert invocation is not None
            deadline = invocation.started_at + timedelta(seconds=invocation.reservation.seconds)
            if not invocation.started_at <= now < deadline or deadline > limits.expires_at:
                raise ReviewStoreDenied("Invocation time window expired or not current")
            if (now >= limits.expires_at or values["invocations"] > limits.max_invocations
                    or values["repairs"] > limits.max_repairs
                    or values["tokens_spent"] + values["tokens_reserved"] > limits.max_tokens
                    or values["cost_microusd_spent"] + values["cost_microusd_reserved"]
                    > limits.max_cost_microusd):
                raise ReviewStoreDenied("Parent budget exhausted or expired")
        # Completion must settle real usage even if limits have been exceeded.
        return values

    def _append_event(self, connection: Connection, updated: ReviewSession,
                      current: ReviewSession | None, kind: TransitionKind, sequence: int) -> None:
        scope = updated.seed.scope
        invocation_id = provider = execution_id = None
        if kind == "RESERVE":
            invocation_id = updated.attempts[-1].invocation.invocation_id
        elif kind == "COMPLETE":
            completion = updated.attempts[-1].completion
            assert completion is not None
            if completion.receipt is not None:
                provider = completion.receipt.actual_model.provider
                execution_id = completion.receipt.execution_id
        connection.execute(events.insert().values(
            tenant_id=scope.tenant_id, work_item_id=scope.work_item_id, session_id=scope.session_id,
            sequence=sequence, kind=kind, subject_id=self.grant.subject_id,
            before_digest=current.content_digest if current is not None else None,
            after_digest=updated.content_digest, document=updated.model_dump_json(),
            invocation_id=invocation_id, provider=provider, execution_id=execution_id,
        ))