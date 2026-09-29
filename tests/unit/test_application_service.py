import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from nokinc_factory.application.business_analyst import BusinessAnalystResult
from nokinc_factory.application.chat_roles import ConversationTurn
from nokinc_factory.application.grounded_repository_discussion import (
    GroundedClaim,
    GroundedDiscussionResult,
    ModelRunTelemetry,
    SourceCitation,
)
from nokinc_factory.application.model_pricing import ModelCostEstimate
from nokinc_factory.application.service import (
    HmacTokenIssuer,
    InMemoryWorkflowStore,
    WorkflowEvent,
    create_app,
)
from nokinc_factory.domain.review_base import content_digest
from nokinc_factory.ports.model import ModelUsage

SECRET = b"unit-test-secret-with-enough-entropy"
NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


def test_workflow_event_reads_legacy_document_without_analysis_audit() -> None:
    event = WorkflowEvent(
        event_id="evt-legacy",
        tenant_id="tenant-a",
        work_item_id="wi-legacy",
        actor_id="user-a",
        kind="INTAKE",
        occurred_at=NOW,
        payload_digest=content_digest({"message": "build refunds"}),
    )
    legacy_document = event.model_dump(mode="json", exclude={"grounded_analysis"})

    restored = WorkflowEvent.model_validate(legacy_document)

    assert restored.content_digest == event.content_digest
    assert restored.grounded_analysis is None


def test_grounded_analysis_event_requires_audit_payload_and_matching_digest() -> None:
    with pytest.raises(ValidationError, match="requires grounded analysis audit"):
        WorkflowEvent(
            event_id="evt-analysis-missing",
            tenant_id="tenant-a",
            work_item_id="wi-analysis",
            actor_id="user-a",
            kind="GROUNDED_ANALYSIS_RECORDED",
            occurred_at=NOW,
            payload_digest=content_digest({"analysis": "missing"}),
        )


def client() -> tuple[TestClient, HmacTokenIssuer]:
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    app = create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        store=InMemoryWorkflowStore(),
        clock=lambda: NOW,
    )
    return TestClient(app), issuer


def headers(issuer: HmacTokenIssuer, *, tenant: str = "tenant-a",
            subject: str = "user-a", roles: tuple[str, ...] = ("viewer",),
            expires_in: int = 300) -> dict[str, str]:
    return {"Authorization": "Bearer " + issuer.issue(
        tenant_id=tenant, subject_id=subject, roles=roles,
        now=NOW, expires_in_seconds=expires_in,
    )}


def test_health_is_public_but_workflow_requires_authentication() -> None:
    api, issuer = client()

    assert api.get("/healthz").status_code == 200
    assert api.post("/v1/chat", json={"message": "build refunds"}).status_code == 401
    assert api.post("/v1/chat", json={"message": "build refunds"},
                    headers=headers(issuer)).status_code == 202


def test_signed_in_chat_shell_and_assets_are_served() -> None:
    api, _ = client()

    page = api.get("/chat")
    stylesheet = api.get("/static/chat.css")
    script = api.get("/static/chat.js")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert "id=\"chat-form\"" in page.text
    assert 'id="repositories" name="repositories" multiple' in page.text
    assert stylesheet.status_code == 200
    assert "text/css" in stylesheet.headers["content-type"]
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "selectedOptions" in script.text
    assert "repositories: selectedRepositories" in script.text
    assert "Array.isArray(item.repositories)" in script.text
    assert "citation.repository || repository" in script.text


def test_chat_reports_recorded_intake_and_persisted_state() -> None:
    api, issuer = client()

    response = api.post(
        "/v1/chat",
        json={"message": "inspect Flur repo health"},
        headers=headers(issuer),
    )

    assert response.status_code == 202
    assert response.json()["status"] == "INTAKE_RECORDED"
    assert response.json()["state"] == "REFINING"
    assert response.json()["tenant_id"] == "tenant-a"


def test_chat_rejects_history_that_does_not_end_with_an_assistant_turn() -> None:
    api, issuer = client()

    response = api.post(
        "/v1/chat",
        json={
            "message": "Another detail",
            "work_item_id": "wi-123",
            "history": [{"role": "user", "content": "First message"}],
        },
        headers=headers(issuer),
    )

    assert response.status_code == 422
    assert "must end with an assistant turn" in response.text


def test_expired_and_forged_tokens_fail_closed() -> None:
    api, issuer = client()
    expired_token = issuer.issue(
        tenant_id="tenant-a", subject_id="user-a", roles=("viewer",),
        now=NOW - timedelta(seconds=600), expires_in_seconds=300,
    )
    expired = {"Authorization": "Bearer " + expired_token}
    forged = {"Authorization": "Bearer " + expired_token[:-1] + "x"}

    assert api.get("/v1/status", headers=expired).status_code == 401
    assert api.get("/v1/status", headers=forged).status_code == 401


def test_work_items_and_trace_are_tenant_scoped() -> None:
    api, issuer = client()
    created = api.post("/v1/chat", json={"message": "build refunds"},
                       headers=headers(issuer)).json()
    work_item_id = created["work_item_id"]

    other = headers(issuer, tenant="tenant-b", subject="user-b")
    assert api.get("/v1/status", headers=other).json()["items"] == []
    assert api.get(f"/v1/work-items/{work_item_id}", headers=other).status_code == 404

    trace = api.get(f"/v1/work-items/{work_item_id}/trace",
                    headers=headers(issuer)).json()
    assert trace["work_item_id"] == work_item_id
    assert trace["events"][0]["tenant_id"] == "tenant-a"
    assert trace["events"][0]["actor_id"] == "user-a"


def test_gate_requires_operator_and_never_fabricates_provider_approval() -> None:
    api, issuer = client()
    created = api.post("/v1/chat", json={"message": "build refunds"},
                       headers=headers(issuer)).json()
    work_item_id = created["work_item_id"]
    decision_digest = "sha256:" + "a" * 64
    request = {
        "gate": "gate-1",
        "decision": "approve",
        "decision_digest": decision_digest,
    }

    assert api.post(
        f"/v1/work-items/{work_item_id}/gate",
        json={"gate": "gate-1", "decision": "approve"},
        headers=headers(issuer, roles=("operator",)),
    ).status_code == 422
    assert api.post(
        f"/v1/work-items/{work_item_id}/gate",
        json={**request, "decision_digest": "not-a-digest"},
        headers=headers(issuer, roles=("operator",)),
    ).status_code == 422

    assert api.post(f"/v1/work-items/{work_item_id}/gate", json=request,
                    headers=headers(issuer)).status_code == 403
    response = api.post(
        f"/v1/work-items/{work_item_id}/gate", json=request,
        headers=headers(issuer, roles=("operator",)),
    )
    assert response.status_code == 202
    assert response.json()["status"] == "NOT_AVAILABLE"
    assert response.json()["reason"] == "APPROVAL_PROVIDER_NOT_CONFIGURED"
    trace = api.get(f"/v1/work-items/{work_item_id}/trace",
                    headers=headers(issuer, roles=("operator",))).json()
    assert trace["events"][-1]["payload_digest"] == content_digest({
        "gate": "gate-1",
        "decision": "approve",
        "decision_digest": decision_digest,
    })


def test_token_issuer_rejects_clock_before_issue_time() -> None:
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    token = issuer.issue(
        tenant_id="tenant-a", subject_id="user-a", roles=("viewer",),
        now=NOW + timedelta(seconds=10), expires_in_seconds=300,
    )
    try:
        issuer.verify(token, now=NOW)
    except ValueError as error:
        assert "issued" in str(error)
    else:
        raise AssertionError("future-issued token was accepted")


def test_github_access_probe_is_operator_only_and_uses_fixed_repo_allowlist() -> None:
    class FakeGitHubAppBroker:
        def __init__(self) -> None:
            self.repositories: list[str] = []

        def token_for(self, repository: str) -> object:
            self.repositories.append(repository)
            return object()

    broker = FakeGitHubAppBroker()
    repositories = ("NOK-Apps/flur-frontend", "NOK-Apps/flur-sdk")
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    app = create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_app_broker=broker,
        github_repositories=repositories,
        clock=lambda: NOW,
    )
    api = TestClient(app)

    viewer = headers(issuer)
    denied = api.get("/v1/github/access", headers=viewer)
    operator_headers = headers(issuer, roles=("operator",))
    allowed = api.get("/v1/github/access", headers=operator_headers)

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json() == {
        "repositories": [
            {"repository": repository, "status": "TOKEN_ISSUED"}
            for repository in repositories
        ]
    }
    assert broker.repositories == list(repositories)


def test_github_repository_read_probe_is_operator_only_and_allowlisted() -> None:
    class FakeRepositoryReader:
        def __init__(self) -> None:
            self.repositories: list[str] = []

        def read(self, repository: str) -> SimpleNamespace:
            self.repositories.append(repository)
            return SimpleNamespace(
                full_name=repository,
                default_branch="main",
                visibility="internal",
                archived=False,
            )

    reader = FakeRepositoryReader()
    repositories = ("NOK-Apps/flur-sdk", "NOK-Apps/flur-frontend")
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    app = create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_repository_reader=reader,
        github_repositories=repositories,
        clock=lambda: NOW,
    )
    api = TestClient(app)

    denied = api.get("/v1/github/repositories", headers=headers(issuer))
    allowed = api.get(
        "/v1/github/repositories",
        headers=headers(issuer, roles=("operator",)),
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json() == {
        "repositories": [
            {
                "repository": repository,
                "full_name": repository,
                "default_branch": "main",
                "visibility": "internal",
                "archived": False,
            }
            for repository in repositories
        ]
    }
    assert reader.repositories == list(repositories)


def test_chat_runs_grounded_repository_discussion_only_for_operator() -> None:
    class FakeGroundedDiscussion:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def answer(
            self,
            *,
            repository: str,
            question: str,
            profile: str,
            history: tuple[ConversationTurn, ...] = (),
        ) -> GroundedDiscussionResult:
            self.calls.append((repository, question, profile))
            return GroundedDiscussionResult(
                analysis_id="11111111-1111-4111-8111-111111111111",
                status="ANSWERED",
                repository=repository,
                default_branch="main",
                context_digest="sha256:" + "a" * 64,
                summary="Login stores a token in localStorage.",
                claims=(GroundedClaim(
                    statement="Login stores a token in localStorage.",
                    citations=(SourceCitation(
                        path="apps/web/src/auth.ts",
                        line_start=2,
                        line_end=2,
                        quote="localStorage.setItem('auth_token', token);",
                    ),),
                ),),
                open_questions=(),
                model_runs=(
                    ModelRunTelemetry(
                        stage="architect", provider="openai", model="gpt-6-astra",
                        family="openai-astra", provider_execution_id="doer-run",
                        request_digest="sha256:" + "b" * 64,
                        response_digest="sha256:" + "c" * 64, latency_ms=120,
                        usage=ModelUsage(
                            input_tokens=100,
                            cached_input_tokens=20,
                            output_tokens=10,
                        ),
                        list_price_cost=ModelCostEstimate(
                            cost_nanodollars=1_520_000,
                            price_card_id="openai-2026-09",
                            source_url="https://developers.openai.com/api/docs/models/gpt-6-astra",
                            priced_on=NOW.date(),
                        ),
                    ),
                    ModelRunTelemetry(
                        stage="independent-review", provider="aws-bedrock",
                        model="amazon.nova-pro-v1:0", family="amazon-nova-pro",
                        provider_execution_id="review-run",
                        request_digest="sha256:" + "d" * 64,
                        response_digest="sha256:" + "e" * 64, latency_ms=80,
                    ),
                ),
                elapsed_ms=210,
            )

    discussion = FakeGroundedDiscussion()
    repositories = ("NOK-Apps/flur-frontend", "NOK-Apps/flur-sdk")
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    app = create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_repositories=repositories,
        grounded_discussion=discussion,
        chat_model_turn_limit=10,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    )
    api = TestClient(app)

    viewer = api.post(
        "/v1/chat",
        json={"message": "explain login", "repository": "NOK-Apps/flur-frontend"},
        headers=headers(issuer),
    )
    operator = api.post(
        "/v1/chat",
        json={"message": "explain login", "repository": "NOK-Apps/flur-frontend"},
        headers=headers(issuer, roles=("operator",)),
    )

    assert viewer.status_code == 403
    assert operator.status_code == 202
    assert operator.json()["status"] == "AGENT_REPLIED"
    assert operator.json()["agent_role"] == "architect"
    assert operator.json()["analysis"]["status"] == "ANSWERED"
    assert operator.json()["analysis"]["claims"][0]["citations"][0]["path"] == (
        "apps/web/src/auth.ts"
    )
    assert discussion.calls == [("NOK-Apps/flur-frontend", "explain login", "architecture")]
    work_item_id = operator.json()["work_item_id"]
    trace = api.get(
        f"/v1/work-items/{work_item_id}/trace",
        headers=headers(issuer, roles=("operator",)),
    ).json()
    assert trace["events"][0]["payload_digest"] == content_digest({
        "message": "explain login",
        "repository": "NOK-Apps/flur-frontend",
        "analysis_profile": "architecture",
    })
    assert [event["kind"] for event in trace["events"]] == [
        "INTAKE",
        "CHAT_TURN_RESERVED",
        "CHAT_TURN_RECORDED",
    ]
    assert trace["events"][1]["chat_turn_reservation"]["model_call_count"] == 6
    telemetry = trace["events"][2]["chat_turn_audit"]
    assert telemetry["analysis_profile"] == "architecture"
    assert telemetry["repository"] == "NOK-Apps/flur-frontend"
    assert telemetry["input_digest"] == content_digest("explain login")
    assert telemetry["context_digest"] == "sha256:" + "a" * 64
    assert telemetry["model_runs"][0]["provider_execution_id"] == "doer-run"
    assert telemetry["model_runs"][1]["provider_execution_id"] == "review-run"
    assert telemetry["model_runs"][0]["latency_ms"] == 120
    assert telemetry["model_runs"][1]["request_digest"] == "sha256:" + "d" * 64
    assert telemetry["model_runs"][0]["usage"]["output_tokens"] == 10
    assert telemetry["model_runs"][0]["list_price_cost"]["cost_nanodollars"] == 1_520_000
    assert "prompt" not in telemetry and "claims" not in telemetry
    tampered_event = dict(trace["events"][2])
    tampered_audit = dict(tampered_event["chat_turn_audit"])
    tampered_audit["model_runs"] = list(tampered_audit["model_runs"])
    tampered_audit["model_runs"][0] = dict(tampered_audit["model_runs"][0])
    tampered_audit["model_runs"][0]["latency_ms"] = 121
    tampered_event["chat_turn_audit"] = tampered_audit
    with pytest.raises(ValidationError):
        WorkflowEvent.model_validate(tampered_event)

def test_chat_can_select_coding_model_profile(monkeypatch) -> None:
    class FakeGroundedDiscussion:
        def __init__(self) -> None:
            self.profile: str | None = None

        def answer(
            self,
            *,
            repository: str,
            question: str,
            profile: str,
            history: tuple[ConversationTurn, ...] = (),
        ) -> GroundedDiscussionResult:
            self.profile = profile
            return GroundedDiscussionResult(
                analysis_id="22222222-2222-4222-8222-222222222222",
                status="NEEDS_CLARIFICATION",
                repository=repository,
                default_branch="main",
                context_digest="sha256:" + "f" * 64,
                summary="The available code context does not settle this question.",
                claims=(),
                open_questions=("Which behavior should the code implement?",),
                model_runs=(
                    ModelRunTelemetry(
                        stage="architect", provider="openai", model="gpt-5.6-luna",
                        family="openai-luna", provider_execution_id=None,
                        request_digest="sha256:" + "1" * 64,
                        response_digest="sha256:" + "2" * 64, latency_ms=10,
                    ),
                    ModelRunTelemetry(
                        stage="independent-review", provider="google",
                        model="gemini-3.8-flash", family="google-gemini-flash",
                        provider_execution_id=None,
                        request_digest="sha256:" + "3" * 64,
                        response_digest="sha256:" + "4" * 64, latency_ms=10,
                    ),
                ),
                elapsed_ms=20,
            )

    discussion = FakeGroundedDiscussion()
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_repositories=("NOK-Apps/flur-sdk",),
        grounded_discussion=discussion,
        chat_model_turn_limit=10,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    ))

    response = api.post(
        "/v1/chat",
        json={
            "message": "review the SDK implementation",
            "repository": "NOK-Apps/flur-sdk",
            "analysis_profile": "coding",
        },
        headers=headers(issuer, roles=("operator",)),
    )

    assert response.status_code == 202
    assert response.json()["analysis"]["status"] == "NEEDS_CLARIFICATION"
    assert discussion.profile == "coding"


def test_business_chat_reuses_work_item_and_passes_bounded_history() -> None:
    class FakeBusinessAnalyst:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, tuple[ConversationTurn, ...]]] = []

        def answer(
            self,
            *,
            work_item_id: str,
            question: str,
            repository: str | None = None,
            history: tuple[ConversationTurn, ...],
        ) -> BusinessAnalystResult:
            self.calls.append((work_item_id, question, history))
            return BusinessAnalystResult(
                analysis_id=f"{len(self.calls):08x}-1111-4111-8111-111111111111",
                status="ELICITING",
                reply="Who is affected by the refund issue?",
                open_questions=("Who is affected?",),
                context_digest=content_digest({"turn": len(self.calls)}),
                model_runs=(
                    ModelRunTelemetry(
                        stage="business-analyst", provider="openai", model="gpt-6-astra",
                        family="openai-astra", provider_execution_id="business-run",
                        request_digest=content_digest({"request": "business"}),
                        response_digest=content_digest({"response": "business"}),
                        latency_ms=100,
                    ),
                    ModelRunTelemetry(
                        stage="business-review", provider="aws-bedrock",
                        model="amazon.nova-pro-v1:0", family="amazon-nova-pro",
                        provider_execution_id="business-review-run",
                        request_digest=content_digest({"request": "review"}),
                        response_digest=content_digest({"response": "review"}),
                        latency_ms=90,
                    ),
                ),
                elapsed_ms=200,
            )

    analyst = FakeBusinessAnalyst()
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        business_analyst=analyst,
        chat_model_turn_limit=10,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    ))
    operator_headers = headers(issuer, roles=("operator",))

    first = api.post(
        "/v1/chat",
        json={"message": "Refund processing is confusing.", "mode": "business"},
        headers=operator_headers,
    )
    work_item_id = first.json()["work_item_id"]
    second = api.post(
        "/v1/chat",
        json={
            "message": "Customers cannot tell when money arrives.",
            "mode": "business",
            "work_item_id": work_item_id,
            "history": [
                {"role": "user", "content": "Refund processing is confusing."},
                {"role": "assistant", "content": "Who is affected by the refund issue?"},
            ],
        },
        headers=operator_headers,
    )

    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["agent_role"] == "business_analyst"
    assert second.json()["work_item_id"] == work_item_id
    assert second.json()["reply"] == "Who is affected by the refund issue?"
    assert analyst.calls[1] == (
        work_item_id,
        "Customers cannot tell when money arrives.",
        (
            ConversationTurn(role="user", content="Refund processing is confusing."),
            ConversationTurn(
                role="assistant",
                content="Who is affected by the refund issue?",
            ),
        ),
    )
    trace = api.get(
        f"/v1/work-items/{work_item_id}/trace",
        headers=operator_headers,
    ).json()
    assert [event["kind"] for event in trace["events"]] == [
        "INTAKE",
        "CHAT_TURN_RESERVED",
        "CHAT_TURN_RECORDED",
        "CHAT_TURN_RESERVED",
        "CHAT_TURN_RECORDED",
    ]
    audit_json = str(trace["events"][4]["chat_turn_audit"])
    assert "Customers cannot tell when money arrives." not in audit_json
    assert "Who is affected by the refund issue?" not in audit_json


def test_business_provider_failure_returns_conversation_id_for_retry() -> None:
    class FailingBusinessAnalyst:
        def answer(
            self,
            *,
            work_item_id: str,
            question: str,
            repository: str | None = None,
            history: tuple[ConversationTurn, ...] = (),
        ) -> BusinessAnalystResult:
            raise RuntimeError("provider failure")

    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        business_analyst=FailingBusinessAnalyst(),
        chat_model_turn_limit=10,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    ))

    response = api.post(
        "/v1/chat",
        json={"message": "Refund processing is confusing.", "mode": "business"},
        headers=headers(issuer, roles=("operator",)),
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "AGENT_RESPONSE_UNAVAILABLE"
    work_item_id = response.json()["detail"]["work_item_id"]
    assert api.get("/v1/work-items/" + work_item_id,
                   headers=headers(issuer, roles=("operator",))).status_code == 200


def test_chat_failure_logs_stage_and_type_without_exception_message(caplog) -> None:
    failure_message = "api_key=never-log-this prompt contains private data"

    class FailingBusinessAnalyst:
        def answer(
            self,
            *,
            work_item_id: str,
            question: str,
            repository: str | None = None,
            history: tuple[ConversationTurn, ...] = (),
        ) -> BusinessAnalystResult:
            raise RuntimeError(failure_message)

    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        business_analyst=FailingBusinessAnalyst(),
        chat_model_turn_limit=1,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    ))

    with caplog.at_level(logging.ERROR, logger="nokinc_factory.application.service"):
        response = api.post(
            "/v1/chat",
            json={"message": "Analyze the payment flow", "mode": "business"},
            headers=headers(issuer, roles=("operator",)),
        )

    assert response.status_code == 502
    assert "stage=provider" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert failure_message not in caplog.text


def test_chat_provider_failure_logs_only_allowlisted_diagnostic_code(caplog) -> None:
    from nokinc_factory.adapters.model_providers import ModelProviderError

    provider_message = "account data and provider response must not appear"

    class FailingBusinessAnalyst:
        def answer(
            self,
            *,
            work_item_id: str,
            question: str,
            repository: str | None = None,
            history: tuple[ConversationTurn, ...] = (),
        ) -> BusinessAnalystResult:
            raise ModelProviderError(
                provider_message,
                diagnostic_code="HTTP_AUTHORIZATION",
            )

    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        business_analyst=FailingBusinessAnalyst(),
        chat_model_turn_limit=1,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    ))

    with caplog.at_level(logging.ERROR, logger="nokinc_factory.application.service"):
        response = api.post(
            "/v1/chat",
            json={"message": "Analyze the payment flow", "mode": "business"},
            headers=headers(issuer, roles=("operator",)),
        )

    assert response.status_code == 502
    assert "diagnostic_code=HTTP_AUTHORIZATION" in caplog.text
    assert provider_message not in caplog.text


@pytest.mark.parametrize(
    ("turn_limit", "second_status", "expected_calls"),
    [
        pytest.param(1, 429, 1, id="bounded"),
        pytest.param(None, 202, 2, id="unlimited"),
    ],
)
def test_chat_model_turn_limit_is_reserved_before_provider_call(
    turn_limit: int | None,
    second_status: int,
    expected_calls: int,
) -> None:
    class FakeBusinessAnalyst:
        def __init__(self) -> None:
            self.calls = 0

        def answer(
            self,
            *,
            work_item_id: str,
            question: str,
            repository: str | None = None,
            history: tuple[ConversationTurn, ...] = (),
        ) -> BusinessAnalystResult:
            self.calls += 1
            return BusinessAnalystResult(
                analysis_id=f"{self.calls:08x}-1111-4111-8111-111111111111",
                status="ELICITING",
                reply="Who is affected?",
                open_questions=("Who is affected?",),
                context_digest=content_digest({"turn": self.calls}),
                model_runs=(
                    ModelRunTelemetry(
                        stage="business-analyst", provider="openai", model="gpt-6-astra",
                        family="openai-astra", request_digest=content_digest("doer"),
                        response_digest=content_digest("draft"), latency_ms=1,
                    ),
                    ModelRunTelemetry(
                        stage="business-review", provider="aws-bedrock",
                        model="amazon.nova-pro-v1:0", family="amazon-nova-pro",
                        request_digest=content_digest("review"),
                        response_digest=content_digest("reviewed"), latency_ms=1,
                    ),
                ),
                elapsed_ms=2,
            )

    analyst = FakeBusinessAnalyst()
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        business_analyst=analyst,
        chat_model_turn_limit=turn_limit,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    ))
    operator_headers = headers(issuer, roles=("operator",))

    first = api.post(
        "/v1/chat",
        json={"message": "Refund processing is confusing.", "mode": "business"},
        headers=operator_headers,
    )
    work_item_id = first.json()["work_item_id"]
    second = api.post(
        "/v1/chat",
        json={
            "message": "Another detail",
            "mode": "business",
            "work_item_id": work_item_id,
            "history": [
                {"role": "user", "content": "Refund processing is confusing."},
                {"role": "assistant", "content": "Who is affected?"},
            ],
        },
        headers=operator_headers,
    )

    assert first.status_code == 202
    assert second.status_code == second_status
    if turn_limit is not None:
        assert second.json()["detail"]["code"] == "CHAT_MODEL_TURN_LIMIT_REACHED"
    assert analyst.calls == expected_calls
    trace = api.get(
        f"/v1/work-items/{work_item_id}/trace",
        headers=operator_headers,
    ).json()
    expected_event_kinds = [
        "INTAKE",
        "CHAT_TURN_RESERVED",
        "CHAT_TURN_RECORDED",
    ]
    if turn_limit is None:
        expected_event_kinds.extend(("CHAT_TURN_RESERVED", "CHAT_TURN_RECORDED"))
    assert [event["kind"] for event in trace["events"]] == expected_event_kinds


def test_model_chat_is_restricted_to_the_authorized_pilot_tenant() -> None:
    class FakeBusinessAnalyst:
        def __init__(self) -> None:
            self.calls = 0

        def answer(self, *, work_item_id: str, question: str, repository=None, history=()):
            self.calls += 1
            raise AssertionError("unauthorized tenant must not invoke a model")

    analyst = FakeBusinessAnalyst()
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        business_analyst=analyst,
        chat_model_turn_limit=2,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    ))

    response = api.post(
        "/v1/chat",
        json={"message": "Question from another tenant", "mode": "business"},
        headers=headers(issuer, tenant="tenant-b", subject="user-b", roles=("operator",)),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "CHAT_MODEL_TENANT_NOT_AUTHORIZED"
    assert analyst.calls == 0


def test_auto_chat_routes_code_questions_and_requires_operator_role() -> None:
    class FakeGroundedDiscussion:
        def __init__(self) -> None:
            self.profile: str | None = None

        def answer(
            self,
            *,
            repository: str,
            question: str,
            profile: str,
            history: tuple[ConversationTurn, ...] = (),
        ) -> GroundedDiscussionResult:
            self.profile = profile
            return GroundedDiscussionResult(
                analysis_id="33333333-3333-4333-8333-333333333333",
                status="NEEDS_CLARIFICATION",
                repository=repository,
                default_branch="main",
                context_digest=content_digest({"source": repository}),
                summary="The selected source files do not answer this question yet.",
                claims=(),
                open_questions=("Which login flow do you mean?",),
                model_runs=(
                    ModelRunTelemetry(
                        stage="architect", provider="openai", model="gpt-5.6-luna",
                        family="openai-luna", request_digest=content_digest("doer"),
                        response_digest=content_digest("draft"), latency_ms=10,
                    ),
                    ModelRunTelemetry(
                        stage="independent-review", provider="google",
                        model="gemini-3.8-flash", family="google-gemini-flash",
                        request_digest=content_digest("review"),
                        response_digest=content_digest("reviewed"), latency_ms=10,
                    ),
                ),
                elapsed_ms=20,
            )

    discussion = FakeGroundedDiscussion()
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_repositories=("NOK-Apps/flur-frontend",),
        grounded_discussion=discussion,
        chat_model_turn_limit=10,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    ))

    denied = api.post(
        "/v1/chat",
        json={
            "message": "Where is the login token saved in the code?",
            "repository": "NOK-Apps/flur-frontend",
            "mode": "auto",
        },
        headers=headers(issuer),
    )
    answered = api.post(
        "/v1/chat",
        json={
            "message": "Where is the login token saved in the code?",
            "repository": "NOK-Apps/flur-frontend",
            "mode": "auto",
        },
        headers=headers(issuer, roles=("operator",)),
    )

    assert denied.status_code == 403
    assert answered.status_code == 202
    assert answered.json()["agent_role"] == "code_analyst"
    assert discussion.profile == "coding"


def test_chat_rejects_repository_outside_pilot_allowlist_before_intake() -> None:
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    app = create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_repositories=("NOK-Apps/flur-sdk",),
        grounded_discussion=object(),
        clock=lambda: NOW,
    )
    api = TestClient(app)

    response = api.post(
        "/v1/chat",
        json={"message": "explain login", "repository": "other/private-repo"},
        headers=headers(issuer, roles=("operator",)),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "repository is outside the pilot allowlist"
    assert api.get("/v1/status", headers=headers(issuer, roles=("operator",))).json()["items"] == []


def test_multi_repository_chat_preserves_selection_and_citation_identity() -> None:
    repositories = ("NOK-Apps/flur-frontend", "NOK-Apps/flur-sdk")

    class FakeGroundedDiscussion:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], str, str]] = []

        def answer(
            self,
            *,
            repositories: tuple[str, ...],
            question: str,
            profile: str,
            history: tuple[ConversationTurn, ...] = (),
        ) -> GroundedDiscussionResult:
            self.calls.append((repositories, question, profile))
            return GroundedDiscussionResult(
                analysis_id="44444444-4444-4444-8444-444444444444",
                status="ANSWERED",
                repository=repositories[0],
                repositories=repositories,
                default_branch="main",
                context_digest=content_digest({"repositories": repositories}),
                summary="The repositories persist tokens differently.",
                claims=(
                    GroundedClaim(
                        statement="The frontend persists a token locally.",
                        citations=(SourceCitation(
                            repository=repositories[0],
                            default_branch="main",
                            path="README.md",
                            line_start=1,
                            line_end=1,
                            quote="Frontend token storage",
                        ),),
                    ),
                    GroundedClaim(
                        statement="The SDK persists a token in a cookie.",
                        citations=(SourceCitation(
                            repository=repositories[1],
                            default_branch="main",
                            path="README.md",
                            line_start=1,
                            line_end=1,
                            quote="SDK token storage",
                        ),),
                    ),
                ),
                open_questions=(),
                model_runs=(
                    ModelRunTelemetry(
                        stage="architect", provider="openai", model="gpt-5.6-luna",
                        family="openai-luna", request_digest=content_digest("doer"),
                        response_digest=content_digest("draft"), latency_ms=10,
                    ),
                    ModelRunTelemetry(
                        stage="independent-review", provider="google",
                        model="gemini-3.8-flash", family="google-gemini-flash",
                        request_digest=content_digest("review"),
                        response_digest=content_digest("reviewed"), latency_ms=10,
                    ),
                ),
                elapsed_ms=20,
            )

    discussion = FakeGroundedDiscussion()
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_repositories=repositories,
        grounded_discussion=discussion,
        chat_model_turn_limit=10,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    ))

    response = api.post(
        "/v1/chat",
        json={
            "message": "Compare token storage across these repositories.",
            "repositories": list(repositories),
            "mode": "coding",
        },
        headers=headers(issuer, roles=("operator",)),
    )

    assert response.status_code == 202
    analysis = response.json()["analysis"]
    assert analysis["repositories"] == list(repositories)
    assert [claim["citations"][0]["repository"] for claim in analysis["claims"]] == list(
        repositories
    )
    assert discussion.calls == [(
        repositories,
        "Compare token storage across these repositories.",
        "coding",
    )]
    work_item_id = response.json()["work_item_id"]
    trace = api.get(
        f"/v1/work-items/{work_item_id}/trace",
        headers=headers(issuer, roles=("operator",)),
    ).json()
    assert trace["events"][0]["payload_digest"] == content_digest({
        "message": "Compare token storage across these repositories.",
        "repositories": repositories,
        "analysis_profile": "coding",
    })
    assert trace["events"][2]["chat_turn_audit"]["repositories"] == list(repositories)


def test_multi_repository_chat_rejects_any_unallowlisted_repo_before_intake() -> None:
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_repositories=("NOK-Apps/flur-sdk",),
        grounded_discussion=object(),
        clock=lambda: NOW,
    ))

    response = api.post(
        "/v1/chat",
        json={
            "message": "Compare behavior across repos",
            "repositories": ["NOK-Apps/flur-sdk", "NOK-Apps/flur-backend"],
            "mode": "coding",
        },
        headers=headers(issuer, roles=("operator",)),
    )

    assert response.status_code == 403
    assert api.get(
        "/v1/status",
        headers=headers(issuer, roles=("operator",)),
    ).json()["items"] == []


def test_multi_repository_chat_rejects_duplicate_selection_before_intake() -> None:
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_repositories=("NOK-Apps/flur-sdk",),
        grounded_discussion=object(),
        clock=lambda: NOW,
    ))

    response = api.post(
        "/v1/chat",
        json={
            "message": "Compare SDK behavior",
            "repositories": ["NOK-Apps/flur-sdk", "NOK-Apps/flur-sdk"],
            "mode": "coding",
        },
        headers=headers(issuer, roles=("operator",)),
    )

    assert response.status_code == 422
    assert api.get(
        "/v1/status",
        headers=headers(issuer, roles=("operator",)),
    ).json()["items"] == []


def test_legacy_repository_field_rejects_pipe_delimited_repo_names() -> None:
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_repositories=("NOK-Apps/flur-sdk", "NOK-Apps/flur-frontend"),
        grounded_discussion=object(),
        clock=lambda: NOW,
    ))

    response = api.post(
        "/v1/chat",
        json={
            "message": "Compare the SDK and frontend",
            "repository": "NOK-Apps/flur-sdk|NOK-Apps/flur-frontend",
            "mode": "coding",
        },
        headers=headers(issuer, roles=("operator",)),
    )

    assert response.status_code == 422
    assert api.get(
        "/v1/status",
        headers=headers(issuer, roles=("operator",)),
    ).json()["items"] == []


def test_chat_rejects_more_than_four_repositories_before_intake() -> None:
    repositories = tuple(f"NOK-Apps/flur-repo-{index}" for index in range(5))
    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_repositories=repositories,
        grounded_discussion=object(),
        chat_model_turn_limit=10,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    ))

    response = api.post(
        "/v1/chat",
        json={
            "message": "Compare these repositories",
            "repositories": list(repositories),
            "mode": "coding",
        },
        headers=headers(issuer, roles=("operator",)),
    )

    assert response.status_code == 422
    assert api.get(
        "/v1/status",
        headers=headers(issuer, roles=("operator",)),
    ).json()["items"] == []


def test_runtime_validation_request_fails_closed_before_intake_or_models() -> None:
    class NeverRunGroundedDiscussion:
        def answer(self, **kwargs) -> GroundedDiscussionResult:
            raise AssertionError("runtime questions must not be answered from source alone")

    issuer = HmacTokenIssuer(secret=SECRET, issuer="factory.test", audience="factory-api")
    api = TestClient(create_app(
        auth_secret=SECRET,
        issuer="factory.test",
        audience="factory-api",
        github_repositories=("NOK-Apps/flur-frontend",),
        grounded_discussion=NeverRunGroundedDiscussion(),
        chat_model_turn_limit=10,
        chat_model_tenant_id="tenant-a",
        clock=lambda: NOW,
    ))

    response = api.post(
        "/v1/chat",
        json={
            "message": "Run this end-to-end and tell me whether it works in practice.",
            "repository": "NOK-Apps/flur-frontend",
            "mode": "coding",
        },
        headers=headers(issuer, roles=("operator",)),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "RUNTIME_EVIDENCE_NOT_CONFIGURED"
    assert api.get(
        "/v1/status",
        headers=headers(issuer, roles=("operator",)),
    ).json()["items"] == []
