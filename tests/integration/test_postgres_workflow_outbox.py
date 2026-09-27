"""Durable outbox and lease contracts for workflow side-effect intents."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from nokinc_factory.adapters.postgres_workflow_schema import create_schema, grant_worker
from nokinc_factory.adapters.postgres_workflow_store import PostgresWorkflowStore

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


def test_outbox_claim_is_idempotent_holder_bound_and_reclaimable(database) -> None:
    _, worker = database
    store = PostgresWorkflowStore(worker)
    tenant = "tenant-" + uuid4().hex
    item = store.create_intake(
        tenant_id=tenant, actor_id="user-a", now=NOW, message="build refunds",
    )
    event_id = "outbox-" + uuid4().hex
    first = store.enqueue_outbox(
        tenant_id=tenant, work_item_id=item.work_item_id, event_id=event_id,
        topic="workflow.intake", payload_digest="sha256:" + "1" * 64, now=NOW,
    )
    replay = store.enqueue_outbox(
        tenant_id=tenant, work_item_id=item.work_item_id, event_id=event_id,
        topic="workflow.intake", payload_digest="sha256:" + "1" * 64, now=NOW,
    )
    assert replay == first

    claimed = store.claim_outbox(tenant_id=tenant, worker_id="worker-a", now=NOW,
                                 lease_seconds=30)
    assert claimed is not None and claimed.event_id == event_id
    assert store.claim_outbox(tenant_id=tenant, worker_id="worker-b", now=NOW,
                              lease_seconds=30) is None
    assert not store.ack_outbox(tenant_id=tenant, event_id=event_id, worker_id="worker-b")
    assert store.ack_outbox(tenant_id=tenant, event_id=event_id, worker_id="worker-a")
    assert store.claim_outbox(tenant_id=tenant, worker_id="worker-b", now=NOW,
                              lease_seconds=30) is None


def test_expired_outbox_claim_can_be_reclaimed_but_tenant_cannot_see_it(database) -> None:
    _, worker = database
    store = PostgresWorkflowStore(worker)
    tenant = "tenant-" + uuid4().hex
    item = store.create_intake(
        tenant_id=tenant, actor_id="user-a", now=NOW, message="build refunds",
    )
    store.enqueue_outbox(
        tenant_id=tenant, work_item_id=item.work_item_id, event_id="outbox-" + uuid4().hex,
        topic="workflow.intake", payload_digest="sha256:" + "2" * 64, now=NOW,
    )
    assert store.claim_outbox(tenant_id=tenant, worker_id="worker-a", now=NOW,
                              lease_seconds=1) is not None
    reclaimed = store.claim_outbox(
        tenant_id=tenant, worker_id="worker-b", now=NOW + timedelta(seconds=2),
        lease_seconds=30,
    )
    assert reclaimed is not None and reclaimed.worker_id == "worker-b"
    assert store.claim_outbox(tenant_id="other-tenant", worker_id="worker-b", now=NOW,
                              lease_seconds=30) is None
