import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from nokinc_factory.adapters.postgres_workflow_schema import create_schema, grant_worker
from nokinc_factory.adapters.postgres_workflow_store import (
    PostgresWorkflowStore,
    WorkflowStoreDenied,
)

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


@pytest.fixture
def database():
    owner_url = os.environ.get("FACTORY_TEST_DATABASE_URL")
    worker_url = os.environ.get("FACTORY_TEST_WORKER_URL")
    if not owner_url or not worker_url:
        pytest.skip("Disposable PostgreSQL owner/worker URLs are required")
    owner, worker = create_engine(owner_url), create_engine(worker_url)
    create_schema(owner)
    with worker.connect() as connection:
        role = connection.scalar(text("SELECT current_user"))
    grant_worker(owner, str(role))
    yield owner, worker
    worker.dispose()
    owner.dispose()


def test_inbox_deduplicates_provider_event_and_survives_restart(database) -> None:
    _, worker = database
    store = PostgresWorkflowStore(worker)
    tenant = "tenant-" + uuid4().hex
    event_id = "provider-event-" + uuid4().hex
    digest = "sha256:" + "1" * 64

    first = store.record_inbox(
        tenant_id=tenant, event_id=event_id, source="github",
        payload_digest=digest, received_at=NOW,
    )
    replay = PostgresWorkflowStore(worker).record_inbox(
        tenant_id=tenant, event_id=event_id, source="github",
        payload_digest=digest, received_at=NOW,
    )

    assert first == replay
    assert store.mark_inbox_processed(tenant_id=tenant, event_id=event_id, now=NOW)
    assert not store.mark_inbox_processed(tenant_id=tenant, event_id=event_id, now=NOW)


def test_inbox_event_id_collision_with_changed_payload_is_denied(database) -> None:
    _, worker = database
    store = PostgresWorkflowStore(worker)
    tenant = "tenant-" + uuid4().hex
    event_id = "provider-event-" + uuid4().hex
    store.record_inbox(
        tenant_id=tenant, event_id=event_id, source="github",
        payload_digest="sha256:" + "1" * 64, received_at=NOW,
    )

    with pytest.raises(WorkflowStoreDenied, match="collision"):
        store.record_inbox(
            tenant_id=tenant, event_id=event_id, source="github",
            payload_digest="sha256:" + "2" * 64, received_at=NOW,
        )
