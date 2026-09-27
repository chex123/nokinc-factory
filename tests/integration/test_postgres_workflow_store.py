"""Durable factory intake/event contracts against real PostgreSQL."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from nokinc_factory.adapters.postgres_workflow_schema import (
    create_schema,
    grant_worker,
    require_postgres,
)
from nokinc_factory.adapters.postgres_workflow_store import PostgresWorkflowStore
from nokinc_factory.application.grounded_repository_discussion import ModelRunTelemetry
from nokinc_factory.application.service import (
    ChatModelTurnLimitExceeded,
    ChatTurnAudit,
    ChatTurnReservation,
    GroundedAnalysisAudit,
    WorkflowEvent,
)
from nokinc_factory.domain.review_base import content_digest

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


def test_intake_survives_store_restart_and_is_tenant_scoped(database) -> None:
    _, worker = database
    tenant = "tenant-" + uuid4().hex
    store = PostgresWorkflowStore(worker)
    item = store.create_intake(
        tenant_id=tenant, actor_id="user-a", now=NOW, message="build refunds",
    )

    restarted = PostgresWorkflowStore(worker)
    assert restarted.get(tenant_id=tenant, work_item_id=item.work_item_id) == item
    assert restarted.list(tenant_id=tenant) == (item,)
    assert restarted.list(tenant_id="other-tenant") == ()
    assert restarted.trace(tenant_id=tenant, work_item_id=item.work_item_id)[0].kind == "INTAKE"


def test_gate_request_is_an_append_only_event(database) -> None:
    _, worker = database
    store = PostgresWorkflowStore(worker)
    item = store.create_intake(
        tenant_id="tenant-" + uuid4().hex, actor_id="user-a", now=NOW, message="build refunds",
    )
    decision_digest = content_digest({"solution_ready": "approved-candidate"})
    event = store.add_gate_request(
        tenant_id=item.tenant_id, work_item_id=item.work_item_id, actor_id="operator-a",
        gate="gate-1", decision="approve", decision_digest=decision_digest, now=NOW,
    )

    events = store.trace(tenant_id=item.tenant_id, work_item_id=item.work_item_id)
    assert isinstance(event, WorkflowEvent)
    assert [entry.kind for entry in events] == ["INTAKE", "GATE_REQUESTED"]
    assert events[-1] == event


def test_grounded_analysis_telemetry_survives_store_restart(database) -> None:
    _, worker = database
    store = PostgresWorkflowStore(worker)
    item = store.create_intake(
        tenant_id="tenant-" + uuid4().hex,
        actor_id="user-a",
        now=NOW,
        message="explain auth",
        repository="NOK-Apps/flur-frontend",
        analysis_profile="architecture",
    )
    analysis = GroundedAnalysisAudit(
        analysis_id="11111111-1111-4111-8111-111111111111",
        analysis_profile="architecture",
        repository="NOK-Apps/flur-frontend",
        status="ANSWERED",
        default_branch="main",
        context_digest=content_digest({"source": "snapshot"}),
        model_runs=(
            ModelRunTelemetry(
                stage="architect", provider="openai", model="gpt-6-astra",
                family="openai-astra", provider_execution_id="doer-run",
                request_digest=content_digest({"request": "doer"}),
                response_digest=content_digest({"response": "doer"}), latency_ms=120,
            ),
            ModelRunTelemetry(
                stage="independent-review", provider="aws-bedrock",
                model="amazon.nova-pro-v1:0", family="amazon-nova-pro",
                provider_execution_id="review-run",
                request_digest=content_digest({"request": "review"}),
                response_digest=content_digest({"response": "review"}), latency_ms=80,
            ),
        ),
        elapsed_ms=210,
    )

    event = store.record_grounded_analysis(
        tenant_id=item.tenant_id,
        work_item_id=item.work_item_id,
        actor_id="user-a",
        analysis=analysis,
        now=NOW,
    )
    events = PostgresWorkflowStore(worker).trace(
        tenant_id=item.tenant_id,
        work_item_id=item.work_item_id,
    )

    assert isinstance(event, WorkflowEvent)
    assert [entry.kind for entry in events] == ["INTAKE", "GROUNDED_ANALYSIS_RECORDED"]
    assert events[-1].grounded_analysis == analysis
    assert events[-1].payload_digest == analysis.content_digest


def test_chat_turn_audit_survives_restart_without_transcript_text(database) -> None:
    _, worker = database
    store = PostgresWorkflowStore(worker)
    user_text = "Customers cannot identify when the refund arrives."
    assistant_text = "Which customers are affected?"
    item = store.create_intake(
        tenant_id="tenant-" + uuid4().hex,
        actor_id="user-a",
        now=NOW,
        message=user_text,
    )
    audit = ChatTurnAudit(
        agent_role="business_analyst",
        analysis_profile="business",
        repository=None,
        status="ELICITING",
        input_digest=content_digest(user_text),
        history_digest=content_digest([]),
        response_digest=content_digest(assistant_text),
        context_digest=content_digest({"turn": 1}),
        model_runs=(
            ModelRunTelemetry(
                stage="business-analyst", provider="openai", model="gpt-6-astra",
                family="openai-astra", provider_execution_id="doer-run",
                request_digest=content_digest({"request": "doer"}),
                response_digest=content_digest({"response": "doer"}), latency_ms=120,
            ),
            ModelRunTelemetry(
                stage="business-review", provider="aws-bedrock",
                model="amazon.nova-pro-v1:0", family="amazon-nova-pro",
                provider_execution_id="review-run",
                request_digest=content_digest({"request": "review"}),
                response_digest=content_digest({"response": "review"}), latency_ms=80,
            ),
        ),
        elapsed_ms=210,
    )

    store.record_chat_turn(
        tenant_id=item.tenant_id,
        work_item_id=item.work_item_id,
        actor_id="user-a",
        audit=audit,
        now=NOW,
    )
    events = PostgresWorkflowStore(worker).trace(
        tenant_id=item.tenant_id,
        work_item_id=item.work_item_id,
    )
    serialized_audit = events[-1].model_dump_json()

    assert [event.kind for event in events] == ["INTAKE", "CHAT_TURN_RECORDED"]
    assert events[-1].chat_turn_audit == audit
    assert user_text not in serialized_audit
    assert assistant_text not in serialized_audit


def test_model_turn_budget_reservation_is_durable_and_tenant_scoped(database) -> None:
    _, worker = database
    store = PostgresWorkflowStore(worker)
    tenant = "tenant-" + uuid4().hex
    reservation = ChatTurnReservation(
        agent_role="business_analyst",
        analysis_profile="business",
        repository=None,
        input_digest=content_digest("synthetic question"),
        history_digest=content_digest([]),
    )
    first = store.create_intake(
        tenant_id=tenant,
        actor_id="user-a",
        now=NOW,
        message="synthetic question",
    )
    reserved = store.reserve_chat_turn(
        tenant_id=tenant,
        work_item_id=first.work_item_id,
        actor_id="user-a",
        reservation=reservation,
        limit=1,
        now=NOW,
    )

    second = store.create_intake(
        tenant_id=tenant,
        actor_id="user-a",
        now=NOW,
        message="another synthetic question",
    )
    with pytest.raises(ChatModelTurnLimitExceeded):
        PostgresWorkflowStore(worker).reserve_chat_turn(
            tenant_id=tenant,
            work_item_id=second.work_item_id,
            actor_id="user-a",
            reservation=reservation,
            limit=1,
            now=NOW,
        )

    persisted = PostgresWorkflowStore(worker).trace(
        tenant_id=tenant,
        work_item_id=first.work_item_id,
    )
    assert reserved.kind == "CHAT_TURN_RESERVED"
    assert [event.kind for event in persisted] == ["INTAKE", "CHAT_TURN_RESERVED"]
    assert persisted[-1].chat_turn_reservation == reservation


def test_non_postgres_dialect_is_rejected_without_execution() -> None:
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(ValueError, match="PostgreSQL"):
            require_postgres(engine)
    finally:
        engine.dispose()


def test_multi_repository_chat_audit_survives_store_restart(database) -> None:
    _, worker = database
    repositories = ("NOK-Apps/flur-frontend", "NOK-Apps/flur-sdk")
    tenant = "tenant-" + uuid4().hex
    store = PostgresWorkflowStore(worker)
    item = store.create_intake(
        tenant_id=tenant,
        actor_id="user-a",
        now=NOW,
        message="compare token storage",
        repository=repositories[0],
        repositories=repositories,
        analysis_profile="coding",
    )
    reservation = ChatTurnReservation(
        agent_role="code_analyst",
        analysis_profile="coding",
        repository=repositories[0],
        repositories=repositories,
        input_digest=content_digest("compare token storage"),
        history_digest=content_digest([]),
    )
    audit = ChatTurnAudit(
        agent_role="code_analyst",
        analysis_profile="coding",
        repository=repositories[0],
        repositories=repositories,
        status="ANSWERED",
        input_digest=content_digest("compare token storage"),
        history_digest=content_digest([]),
        response_digest=content_digest("source-reviewed comparison"),
        context_digest=content_digest({"repositories": repositories}),
        model_runs=(
            ModelRunTelemetry(
                stage="architect", provider="openai", model="gpt-6-astra",
                family="openai-astra", request_digest=content_digest("doer"),
                response_digest=content_digest("draft"), latency_ms=20,
            ),
            ModelRunTelemetry(
                stage="independent-review", provider="aws-bedrock",
                model="amazon.nova-pro-v1:0", family="amazon-nova-pro",
                request_digest=content_digest("review"),
                response_digest=content_digest("reviewed"), latency_ms=15,
            ),
        ),
        elapsed_ms=40,
    )

    reserved = store.reserve_chat_turn(
        tenant_id=tenant,
        work_item_id=item.work_item_id,
        actor_id="user-a",
        reservation=reservation,
        limit=2,
        now=NOW,
    )
    recorded = store.record_chat_turn(
        tenant_id=tenant,
        work_item_id=item.work_item_id,
        actor_id="user-a",
        audit=audit,
        now=NOW,
    )
    events = PostgresWorkflowStore(worker).trace(
        tenant_id=tenant,
        work_item_id=item.work_item_id,
    )

    assert reserved.kind == "CHAT_TURN_RESERVED"
    assert recorded.kind == "CHAT_TURN_RECORDED"
    assert events[0].payload_digest == content_digest({
        "message": "compare token storage",
        "repositories": repositories,
        "analysis_profile": "coding",
    })
    assert events[1].chat_turn_reservation == reservation
    assert events[2].chat_turn_audit == audit
