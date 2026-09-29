"""Durable tenant-scoped workflow intake and append-only event storage."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import IntegrityError

from nokinc_factory.adapters.postgres_workflow_schema import (
    require_postgres,
    workflow_events,
    workflow_inbox,
    workflow_items,
    workflow_outbox,
)
from nokinc_factory.application.service import (
    ChatModelTurnLimitExceeded,
    ChatTurnAudit,
    ChatTurnReservation,
    GateDecision,
    GroundedAnalysisAudit,
    InboxEntry,
    OutboxEntry,
    WorkflowEvent,
    WorkflowItem,
)
from nokinc_factory.domain.review_base import content_digest, utc


class WorkflowStoreDenied(ValueError):
    """The durable workflow operation was denied without an authority change."""


class PostgresWorkflowStore:
    def __init__(self, engine: Engine) -> None:
        require_postgres(engine)
        self._engine = engine

    @contextmanager
    def _transaction(self, tenant_id: str) -> Iterator[Connection]:
        with self._engine.begin() as connection:
            unsafe = connection.scalar(text(
                "SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb "
                "FROM pg_roles WHERE rolname=current_user"
            ))
            if unsafe:
                raise WorkflowStoreDenied("Worker must not bypass row-level security")
            connection.execute(text("SELECT set_config('factory.tenant_id', :tenant, true)"),
                               {"tenant": tenant_id})
            yield connection

    @staticmethod
    def _item(row: RowMapping) -> WorkflowItem:
        if row["format_version"] != 1:
            raise WorkflowStoreDenied("Unsupported stored workflow item version")
        item = WorkflowItem.model_validate_json(row["document"])
        if item.content_digest != row["digest"] or item.tenant_id != row["tenant_id"]:
            raise WorkflowStoreDenied("Stored workflow item digest or tenant disagrees")
        return item

    @staticmethod
    def _event(row: RowMapping, expected_sequence: int) -> WorkflowEvent:
        event = WorkflowEvent.model_validate_json(row["document"])
        if (row["sequence"] != expected_sequence or event.content_digest != row["digest"]
                or event.tenant_id != row["tenant_id"]
                or event.work_item_id != row["work_item_id"]
                or event.kind != row["kind"] or event.actor_id != row["actor_id"]):
            raise WorkflowStoreDenied("Workflow event chain disagrees with stored authority")
        return event

    def create_intake(self, *, tenant_id: str, actor_id: str, now: datetime,
                      message: str, repository: str | None = None,
                      repositories: tuple[str, ...] = (),
                      analysis_profile: str | None = None) -> WorkflowItem:
        current = utc(now)
        item = WorkflowItem(
            work_item_id=f"wi-{uuid4().hex}", tenant_id=tenant_id, created_by=actor_id,
            title="Conversation intake", created_at=current,
        )
        event = WorkflowEvent(
            event_id=f"evt-{uuid4().hex}", tenant_id=tenant_id,
            work_item_id=item.work_item_id, actor_id=actor_id, kind="INTAKE",
            occurred_at=current,
            payload_digest=content_digest(
                {
                    "message": message,
                    "repositories": repositories,
                    "analysis_profile": analysis_profile,
                }
                if len(repositories) > 1
                else {"message": message}
                if repository is None
                else {
                    "message": message,
                    "repository": repository,
                    "analysis_profile": analysis_profile,
                }
            ),
        )
        try:
            with self._transaction(tenant_id) as connection:
                connection.execute(workflow_items.insert().values(
                    tenant_id=tenant_id, work_item_id=item.work_item_id,
                    document=item.model_dump_json(), digest=item.content_digest, format_version=1,
                ))
                connection.execute(workflow_events.insert().values(
                    tenant_id=tenant_id, work_item_id=item.work_item_id, sequence=1,
                    event_id=event.event_id, document=event.model_dump_json(),
                    digest=event.content_digest, kind=event.kind, actor_id=event.actor_id,
                ))
        except IntegrityError as exc:
            raise WorkflowStoreDenied("Workflow intake could not be committed") from exc
        return item

    def record_grounded_analysis(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        actor_id: str,
        analysis: GroundedAnalysisAudit,
        now: datetime,
    ) -> WorkflowEvent:
        event = WorkflowEvent(
            event_id=f"evt-{uuid4().hex}",
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            actor_id=actor_id,
            kind="GROUNDED_ANALYSIS_RECORDED",
            occurred_at=utc(now),
            payload_digest=analysis.content_digest,
            grounded_analysis=analysis,
        )
        with self._transaction(tenant_id) as connection:
            item = connection.execute(select(workflow_items).where(
                workflow_items.c.tenant_id == tenant_id,
                workflow_items.c.work_item_id == work_item_id,
            )).mappings().one_or_none()
            if item is None:
                raise WorkflowStoreDenied("Work item not found")
            self._item(item)
            connection.execute(text(
                "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"
            ), {"key": f"{tenant_id}:{work_item_id}"})
            sequence = connection.scalar(select(func.max(workflow_events.c.sequence)).where(
                workflow_events.c.tenant_id == tenant_id,
                workflow_events.c.work_item_id == work_item_id,
            ))
            next_sequence = int(sequence or 0) + 1
            connection.execute(workflow_events.insert().values(
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                sequence=next_sequence,
                event_id=event.event_id,
                document=event.model_dump_json(),
                digest=event.content_digest,
                kind=event.kind,
                actor_id=event.actor_id,
            ))
        return event

    def record_chat_turn(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        actor_id: str,
        audit: ChatTurnAudit,
        now: datetime,
    ) -> WorkflowEvent:
        event = WorkflowEvent(
            event_id=f"evt-{uuid4().hex}",
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            actor_id=actor_id,
            kind="CHAT_TURN_RECORDED",
            occurred_at=utc(now),
            payload_digest=audit.content_digest,
            chat_turn_audit=audit,
        )
        with self._transaction(tenant_id) as connection:
            item_row = connection.execute(select(workflow_items).where(
                workflow_items.c.tenant_id == tenant_id,
                workflow_items.c.work_item_id == work_item_id,
            )).mappings().one_or_none()
            if item_row is None:
                raise WorkflowStoreDenied("Work item not found")
            item = self._item(item_row)
            if item.created_by != actor_id:
                raise WorkflowStoreDenied("Work item not found")
            connection.execute(text(
                "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"
            ), {"key": f"{tenant_id}:{work_item_id}"})
            sequence = connection.scalar(select(func.max(workflow_events.c.sequence)).where(
                workflow_events.c.tenant_id == tenant_id,
                workflow_events.c.work_item_id == work_item_id,
            ))
            next_sequence = int(sequence or 0) + 1
            connection.execute(workflow_events.insert().values(
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                sequence=next_sequence,
                event_id=event.event_id,
                document=event.model_dump_json(),
                digest=event.content_digest,
                kind=event.kind,
                actor_id=event.actor_id,
            ))
        return event

    def reserve_chat_turn(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        actor_id: str,
        reservation: ChatTurnReservation,
        limit: int | None,
        now: datetime,
    ) -> WorkflowEvent:
        if limit is not None and limit <= 0:
            raise ChatModelTurnLimitExceeded("Model-backed chat is disabled by turn-limit policy")
        event = WorkflowEvent(
            event_id=f"evt-{uuid4().hex}",
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            actor_id=actor_id,
            kind="CHAT_TURN_RESERVED",
            occurred_at=utc(now),
            payload_digest=reservation.content_digest,
            chat_turn_reservation=reservation,
        )
        with self._transaction(tenant_id) as connection:
            if limit is not None:
                connection.execute(text(
                    "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"
                ), {"key": f"{tenant_id}:chat-model-budget"})
            item_row = connection.execute(select(workflow_items).where(
                workflow_items.c.tenant_id == tenant_id,
                workflow_items.c.work_item_id == work_item_id,
            )).mappings().one_or_none()
            if item_row is None:
                raise WorkflowStoreDenied("Work item not found")
            item = self._item(item_row)
            if item.created_by != actor_id:
                raise WorkflowStoreDenied("Work item not found")
            if limit is not None:
                used = connection.scalar(select(func.count()).select_from(workflow_events).where(
                    workflow_events.c.tenant_id == tenant_id,
                    workflow_events.c.kind == "CHAT_TURN_RESERVED",
                ))
                if int(used or 0) >= limit:
                    raise ChatModelTurnLimitExceeded("Tenant chat-turn limit exhausted")
            connection.execute(text(
                "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"
            ), {"key": f"{tenant_id}:{work_item_id}"})
            sequence = connection.scalar(select(func.max(workflow_events.c.sequence)).where(
                workflow_events.c.tenant_id == tenant_id,
                workflow_events.c.work_item_id == work_item_id,
            ))
            next_sequence = int(sequence or 0) + 1
            connection.execute(workflow_events.insert().values(
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                sequence=next_sequence,
                event_id=event.event_id,
                document=event.model_dump_json(),
                digest=event.content_digest,
                kind=event.kind,
                actor_id=event.actor_id,
            ))
        return event

    def get(self, *, tenant_id: str, work_item_id: str) -> WorkflowItem | None:
        with self._transaction(tenant_id) as connection:
            row = connection.execute(select(workflow_items).where(
                workflow_items.c.tenant_id == tenant_id,
                workflow_items.c.work_item_id == work_item_id,
            )).mappings().one_or_none()
            return self._item(row) if row is not None else None

    def list(self, *, tenant_id: str) -> tuple[WorkflowItem, ...]:
        with self._transaction(tenant_id) as connection:
            rows = connection.execute(select(workflow_items).where(
                workflow_items.c.tenant_id == tenant_id,
            )).mappings()
            items = [self._item(row) for row in rows]
            return tuple(sorted(items, key=lambda item: item.created_at))

    def add_gate_request(self, *, tenant_id: str, work_item_id: str, actor_id: str,
                         gate: str, decision: GateDecision,
                         decision_digest: str, now: datetime) -> WorkflowEvent:
        current = utc(now)
        event = WorkflowEvent(
            event_id=f"evt-{uuid4().hex}", tenant_id=tenant_id,
            work_item_id=work_item_id, actor_id=actor_id, kind="GATE_REQUESTED",
            occurred_at=current,
            payload_digest=content_digest({
                "gate": gate,
                "decision": decision,
                "decision_digest": decision_digest,
            }),
        )
        with self._transaction(tenant_id) as connection:
            item = connection.execute(select(workflow_items).where(
                workflow_items.c.tenant_id == tenant_id,
                workflow_items.c.work_item_id == work_item_id,
            )).mappings().one_or_none()
            if item is None:
                raise WorkflowStoreDenied("Work item not found")
            self._item(item)
            connection.execute(text(
                "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"
            ), {"key": f"{tenant_id}:{work_item_id}"})
            sequence = connection.scalar(select(func.max(workflow_events.c.sequence)).where(
                workflow_events.c.tenant_id == tenant_id,
                workflow_events.c.work_item_id == work_item_id,
            ))
            next_sequence = int(sequence or 0) + 1
            connection.execute(workflow_events.insert().values(
                tenant_id=tenant_id, work_item_id=work_item_id, sequence=next_sequence,
                event_id=event.event_id, document=event.model_dump_json(),
                digest=event.content_digest, kind=event.kind, actor_id=event.actor_id,
            ))
        return event

    def trace(self, *, tenant_id: str, work_item_id: str) -> tuple[WorkflowEvent, ...]:
        with self._transaction(tenant_id) as connection:
            rows = connection.execute(select(workflow_events).where(
                workflow_events.c.tenant_id == tenant_id,
                workflow_events.c.work_item_id == work_item_id,
            ).order_by(workflow_events.c.sequence)).mappings()
            return tuple(self._event(row, index) for index, row in enumerate(rows, start=1))

    @staticmethod
    def _outbox_identity(entry: OutboxEntry) -> str:
        return content_digest({
            "event_id": entry.event_id,
            "tenant_id": entry.tenant_id,
            "work_item_id": entry.work_item_id,
            "topic": entry.topic,
            "payload_digest": entry.payload_digest,
        })

    @staticmethod
    def _outbox(row: dict[str, Any] | RowMapping) -> OutboxEntry:
        entry = OutboxEntry(
            event_id=row["event_id"], tenant_id=row["tenant_id"],
            work_item_id=row["work_item_id"], topic=row["topic"],
            payload_digest=row["payload_digest"], status=row["status"],
            attempts=row["attempts"], worker_id=row["worker_id"],
            lease_expires_at=row["lease_expires_at"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )
        if PostgresWorkflowStore._outbox_identity(entry) != row["content_digest"]:
            raise WorkflowStoreDenied("Outbox digest disagrees with stored authority")
        return entry

    def enqueue_outbox(self, *, tenant_id: str, work_item_id: str, event_id: str,
                       topic: str, payload_digest: str, now: datetime) -> OutboxEntry:
        current = utc(now)
        entry = OutboxEntry(
            event_id=event_id, tenant_id=tenant_id, work_item_id=work_item_id,
            topic=topic, payload_digest=payload_digest, created_at=current,
            updated_at=current,
        )
        with self._transaction(tenant_id) as connection:
            item = connection.execute(select(workflow_items).where(
                workflow_items.c.tenant_id == tenant_id,
                workflow_items.c.work_item_id == work_item_id,
            )).mappings().one_or_none()
            if item is None:
                raise WorkflowStoreDenied("Work item not found")
            statement = postgres_insert(workflow_outbox).values(
                tenant_id=tenant_id, event_id=event_id, work_item_id=work_item_id,
                topic=topic, payload_digest=payload_digest, status="PENDING", attempts=0,
                worker_id=None, lease_expires_at=None, created_at=current,
                updated_at=current, content_digest=self._outbox_identity(entry),
            ).on_conflict_do_nothing(index_elements=[
                workflow_outbox.c.tenant_id, workflow_outbox.c.event_id,
            ])
            connection.execute(statement)
            row = connection.execute(select(workflow_outbox).where(
                workflow_outbox.c.tenant_id == tenant_id,
                workflow_outbox.c.event_id == event_id,
            )).mappings().one()
            existing = self._outbox(row)
            if (existing.work_item_id != work_item_id or existing.topic != topic
                    or existing.payload_digest != payload_digest):
                raise WorkflowStoreDenied("Outbox event identity collision")
            return existing

    def claim_outbox(self, *, tenant_id: str, worker_id: str, now: datetime,
                     lease_seconds: int) -> OutboxEntry | None:
        current = utc(now)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        expiry = current + timedelta(seconds=lease_seconds)
        with self._transaction(tenant_id) as connection:
            row = connection.execute(select(workflow_outbox).where(
                workflow_outbox.c.tenant_id == tenant_id,
                or_(workflow_outbox.c.status == "PENDING", and_(
                    workflow_outbox.c.status == "CLAIMED",
                    workflow_outbox.c.lease_expires_at <= current,
                )),
            ).order_by(workflow_outbox.c.created_at).with_for_update(skip_locked=True)
            ).mappings().first()
            if row is None:
                return None
            connection.execute(workflow_outbox.update().where(
                workflow_outbox.c.tenant_id == tenant_id,
                workflow_outbox.c.event_id == row["event_id"],
            ).values(
                status="CLAIMED", attempts=int(row["attempts"]) + 1,
                worker_id=worker_id, lease_expires_at=expiry, updated_at=current,
            ))
            updated = dict(row)
            updated.update({
                "status": "CLAIMED", "attempts": int(row["attempts"]) + 1,
                "worker_id": worker_id, "lease_expires_at": expiry,
                "updated_at": current,
            })
            return self._outbox(updated)

    def ack_outbox(self, *, tenant_id: str, event_id: str, worker_id: str) -> bool:
        with self._transaction(tenant_id) as connection:
            result = connection.execute(workflow_outbox.update().where(
                workflow_outbox.c.tenant_id == tenant_id,
                workflow_outbox.c.event_id == event_id,
                workflow_outbox.c.status == "CLAIMED",
                workflow_outbox.c.worker_id == worker_id,
            ).values(status="ACKED", lease_expires_at=None))
            return result.rowcount == 1

    @staticmethod
    def _inbox_identity(entry: InboxEntry) -> str:
        return content_digest({
            "event_id": entry.event_id, "tenant_id": entry.tenant_id,
            "source": entry.source, "payload_digest": entry.payload_digest,
        })

    @staticmethod
    def _inbox(row: RowMapping) -> InboxEntry:
        entry = InboxEntry(
            event_id=row["event_id"], tenant_id=row["tenant_id"], source=row["source"],
            payload_digest=row["payload_digest"], status=row["status"],
            received_at=row["received_at"], processed_at=row["processed_at"],
        )
        if PostgresWorkflowStore._inbox_identity(entry) != row["content_digest"]:
            raise WorkflowStoreDenied("Inbox digest disagrees with stored authority")
        return entry

    def record_inbox(self, *, tenant_id: str, event_id: str, source: str,
                     payload_digest: str, received_at: datetime) -> InboxEntry:
        current = utc(received_at)
        entry = InboxEntry(
            event_id=event_id, tenant_id=tenant_id, source=source,
            payload_digest=payload_digest, received_at=current,
        )
        with self._transaction(tenant_id) as connection:
            statement = postgres_insert(workflow_inbox).values(
                tenant_id=tenant_id, event_id=event_id, source=source,
                payload_digest=payload_digest, status="RECEIVED", received_at=current,
                processed_at=None, content_digest=self._inbox_identity(entry),
            ).on_conflict_do_nothing(index_elements=[
                workflow_inbox.c.tenant_id, workflow_inbox.c.event_id,
            ])
            connection.execute(statement)
            row = connection.execute(select(workflow_inbox).where(
                workflow_inbox.c.tenant_id == tenant_id,
                workflow_inbox.c.event_id == event_id,
            )).mappings().one()
            existing = self._inbox(row)
            if existing.source != source or existing.payload_digest != payload_digest:
                raise WorkflowStoreDenied("Inbox event identity collision")
            return existing

    def mark_inbox_processed(self, *, tenant_id: str, event_id: str,
                             now: datetime) -> bool:
        current = utc(now)
        with self._transaction(tenant_id) as connection:
            result = connection.execute(workflow_inbox.update().where(
                workflow_inbox.c.tenant_id == tenant_id,
                workflow_inbox.c.event_id == event_id,
                workflow_inbox.c.status == "RECEIVED",
            ).values(status="PROCESSED", processed_at=current))
            return result.rowcount == 1
