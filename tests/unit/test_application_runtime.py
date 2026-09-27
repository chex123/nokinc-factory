import json
from pathlib import Path

import boto3
from fastapi.testclient import TestClient

import nokinc_factory.application.runtime as runtime
from nokinc_factory.adapters.github_repository_reader import (
    RepositoryCodeContext,
    RepositorySourceFile,
)
from nokinc_factory.application.runtime import build_app
from nokinc_factory.domain.review_base import content_digest
from nokinc_factory.ports.model import ModelResponse, ModelStatus

PILOT_CONFIG = Path(__file__).parents[2] / "config" / "pilot.yaml"


def test_development_runtime_can_start_without_external_credentials(monkeypatch) -> None:
    monkeypatch.delenv("FACTORY_DATABASE_URL", raising=False)
    monkeypatch.delenv("FACTORY_AUTH_SECRET", raising=False)
    monkeypatch.setenv("FACTORY_ENV", "development")

    response = TestClient(build_app()).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "authentication": "not_configured"}


def test_production_runtime_requires_database_and_authentication(monkeypatch) -> None:
    monkeypatch.setenv("FACTORY_ENV", "production")
    monkeypatch.setenv("FACTORY_AUTH_MODE", "oidc")
    monkeypatch.setenv("FACTORY_OIDC_ISSUER", "https://identity.example.test/tenant-pool")
    monkeypatch.setenv("FACTORY_OIDC_AUDIENCE", "factory-api")
    monkeypatch.setenv("FACTORY_OIDC_JWKS_URL", "https://identity.example.test/jwks")
    monkeypatch.delenv("FACTORY_DATABASE_URL", raising=False)
    monkeypatch.delenv("FACTORY_AUTH_SECRET", raising=False)

    try:
        build_app()
    except RuntimeError as error:
        assert "FACTORY_DATABASE_URL" in str(error)
    else:
        raise AssertionError("production runtime started without durable configuration")


def test_production_runtime_rejects_shared_hmac_authentication(monkeypatch) -> None:
    monkeypatch.setenv("FACTORY_ENV", "production")
    monkeypatch.setenv(
        "FACTORY_DATABASE_URL",
        "postgresql+psycopg://factory_worker:password@localhost/factory",
    )
    monkeypatch.setenv("FACTORY_AUTH_SECRET", "shared-hmac-pilot-secret-long-enough")
    monkeypatch.delenv("FACTORY_AUTH_MODE", raising=False)
    monkeypatch.delenv("FACTORY_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("FACTORY_OIDC_AUDIENCE", raising=False)
    monkeypatch.delenv("FACTORY_OIDC_JWKS_URL", raising=False)

    try:
        build_app()
    except RuntimeError as error:
        assert "OIDC" in str(error)
    else:
        raise AssertionError("production runtime accepted shared HMAC authentication")


def test_pilot_runtime_requires_database_and_authentication(monkeypatch) -> None:
    monkeypatch.setenv("FACTORY_ENV", "pilot")
    monkeypatch.delenv("FACTORY_DATABASE_URL", raising=False)
    monkeypatch.delenv("FACTORY_DATABASE_SECRET_ARN", raising=False)
    monkeypatch.delenv("FACTORY_AUTH_SECRET", raising=False)

    try:
        build_app()
    except RuntimeError as error:
        assert "FACTORY_DATABASE_URL" in str(error)
    else:
        raise AssertionError("pilot runtime started without durable configuration")


def test_pilot_runtime_resolves_managed_database_secret(monkeypatch) -> None:
    monkeypatch.setenv("FACTORY_ENV", "pilot")
    monkeypatch.delenv("FACTORY_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "FACTORY_DATABASE_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:441186133046:secret:pilot/factory/db-test",
    )
    monkeypatch.setenv("FACTORY_AUTH_SECRET", "runtime-secret-with-enough-entropy")

    class FakeSecretsManager:
        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            assert SecretId.endswith("db-test")
            return {
                "SecretString": (
                    '{"host":"db.internal","port":5432,"username":"factory",'
                    '"password":"test-password","dbname":"factory"}'
                )
            }

    monkeypatch.setattr(
        boto3, "client", lambda service_name, region_name=None: FakeSecretsManager()
    )

    response = TestClient(build_app()).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "authentication": "configured"}


def test_pilot_runtime_resolves_rds_managed_credentials_with_endpoint_metadata(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FACTORY_ENV", "pilot")
    monkeypatch.delenv("FACTORY_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "FACTORY_DATABASE_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:441186133046:secret:rds-test",
    )
    monkeypatch.setenv("FACTORY_DATABASE_HOST", "db.internal")
    monkeypatch.setenv("FACTORY_DATABASE_PORT", "5432")
    monkeypatch.setenv("FACTORY_DATABASE_NAME", "factory")
    monkeypatch.setenv("FACTORY_AUTH_SECRET", "runtime-secret-with-enough-entropy")

    class FakeSecretsManager:
        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            assert SecretId.endswith("rds-test")
            return {"SecretString": '{"username":"factory","password":"test-password"}'}

    monkeypatch.setattr(
        boto3, "client", lambda service_name, region_name=None: FakeSecretsManager()
    )

    response = TestClient(build_app()).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "authentication": "configured"}


def test_configured_runtime_uses_postgres_boundary_without_connecting_at_import(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FACTORY_ENV", "production")
    monkeypatch.setenv("FACTORY_AUTH_MODE", "oidc")
    monkeypatch.setenv("FACTORY_OIDC_ISSUER", "https://identity.example.test/tenant-pool")
    monkeypatch.setenv("FACTORY_OIDC_AUDIENCE", "factory-api")
    monkeypatch.setenv("FACTORY_OIDC_JWKS_URL", "https://identity.example.test/jwks")
    monkeypatch.setenv(
        "FACTORY_DATABASE_URL",
        "postgresql+psycopg://factory_worker:password@localhost/factory",
    )
    monkeypatch.delenv("FACTORY_AUTH_SECRET", raising=False)

    response = TestClient(build_app()).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "authentication": "configured"}


def test_cognito_production_runtime_requires_hosted_login_configuration(monkeypatch) -> None:
    monkeypatch.setenv("FACTORY_ENV", "production")
    monkeypatch.setenv("FACTORY_AUTH_MODE", "oidc")
    monkeypatch.setenv("FACTORY_OIDC_PROVIDER", "cognito")
    monkeypatch.setenv("FACTORY_OIDC_ISSUER", "https://identity.example.test/pool")
    monkeypatch.setenv("FACTORY_OIDC_AUDIENCE", "factory-client")
    monkeypatch.setenv("FACTORY_OIDC_JWKS_URL", "https://identity.example.test/keys")
    monkeypatch.setenv(
        "FACTORY_DATABASE_URL",
        "postgresql+psycopg://factory_worker:password@localhost/factory",
    )
    for name in (
        "FACTORY_COGNITO_DOMAIN",
        "FACTORY_COGNITO_CLIENT_ID",
        "FACTORY_COGNITO_CALLBACK_URL",
        "FACTORY_COGNITO_LOGOUT_URL",
        "FACTORY_PUBLIC_ORIGIN",
    ):
        monkeypatch.delenv(name, raising=False)

    try:
        build_app()
    except RuntimeError as error:
        assert "FACTORY_COGNITO_DOMAIN" in str(error)
    else:
        raise AssertionError("Cognito production runtime started without browser login config")


def test_cognito_production_runtime_exposes_hosted_login_redirect(monkeypatch) -> None:
    monkeypatch.setenv("FACTORY_ENV", "production")
    monkeypatch.setenv("FACTORY_AUTH_MODE", "oidc")
    monkeypatch.setenv("FACTORY_OIDC_PROVIDER", "cognito")
    monkeypatch.setenv(
        "FACTORY_OIDC_ISSUER",
        "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example",
    )
    monkeypatch.setenv("FACTORY_OIDC_AUDIENCE", "factory-client")
    monkeypatch.setenv(
        "FACTORY_OIDC_JWKS_URL",
        "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example/.well-known/jwks.json",
    )
    monkeypatch.setenv(
        "FACTORY_DATABASE_URL",
        "postgresql+psycopg://factory_worker:password@localhost/factory",
    )
    monkeypatch.setenv(
        "FACTORY_COGNITO_DOMAIN",
        "https://nokinc-factory-test.auth.us-east-1.amazoncognito.com",
    )
    monkeypatch.setenv("FACTORY_COGNITO_CLIENT_ID", "factory-client")
    monkeypatch.setenv(
        "FACTORY_COGNITO_CALLBACK_URL",
        "https://factory.nokinc.com/auth/callback",
    )
    monkeypatch.setenv(
        "FACTORY_COGNITO_LOGOUT_URL",
        "https://factory.nokinc.com/auth/signed-out",
    )
    monkeypatch.setenv("FACTORY_PUBLIC_ORIGIN", "https://factory.nokinc.com")
    monkeypatch.delenv("FACTORY_AUTH_SECRET", raising=False)

    response = TestClient(
        build_app(), base_url="https://factory.nokinc.com"
    ).get("/auth/login", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "https://nokinc-factory-test.auth.us-east-1.amazoncognito.com/oauth2/authorize?"
    )
    assert "factory.nokinc.com%2Fauth%2Fcallback" in response.headers["location"]


def test_runtime_loads_exact_pilot_github_app_scope_without_network_calls(monkeypatch) -> None:
    monkeypatch.setenv("FACTORY_ENV", "development")
    monkeypatch.setenv("FACTORY_CHAT_MODEL_TURN_LIMIT", "2")
    monkeypatch.setenv("FACTORY_CHAT_MODEL_TENANT_ID", "00001")
    monkeypatch.setenv("FACTORY_PILOT_CONFIG", str(PILOT_CONFIG))
    monkeypatch.setenv("FACTORY_GITHUB_APP_ID", "5020848")
    monkeypatch.setenv("FACTORY_GITHUB_APP_INSTALLATION_ID", "163491501")
    monkeypatch.setenv(
        "FACTORY_GITHUB_APP_PRIVATE_KEY_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:441186133046:secret:github/apps/nokinc-factory-pilot/private-key",
    )
    provider_calls: list[tuple[str, dict[str, object]]] = []

    class FakeModelPort:
        def __init__(self, *, provider: str, **kwargs: object) -> None:
            self.provider = provider
            self.model = str(kwargs["model"])
            self.family = str(kwargs["family"])
            provider_calls.append((provider, kwargs))

        def complete(self, request) -> ModelResponse:
            if request.role == "grounded_architect_discussion":
                output = json.dumps({
                    "summary": "The auth token is saved by the token store.",
                    "claims": [{
                        "statement": "The auth token is saved by the token store.",
                        "citations": [{
                            "path": "src/auth.ts",
                            "line_start": 1,
                            "line_end": 1,
                            "quote": "tokenStore.save(token);",
                        }],
                    }],
                    "open_questions": [],
                })
            elif request.role == "business_analyst_elicitation":
                output = json.dumps({
                    "reply": "Who is affected by this issue?",
                    "open_questions": ["Who is affected?"],
                })
            elif request.role == "independent_evidence_review":
                output = json.dumps({"supported": True, "issues": [], "open_questions": []})
            else:
                output = json.dumps({
                    "supported": True,
                    "issues": [],
                    "open_questions": [],
                })
            return ModelResponse(
                status=ModelStatus.COMPLETED,
                model=self.model,
                family=self.family,
                output=output,
                provider_execution_id=f"fake-{self.provider}",
            )

    class FakeSourceReader:
        def __init__(self, *, repositories: tuple[str, ...], **kwargs: object) -> None:
            self.repositories = repositories

        def read_codebase_context(self, repository: str, question: str) -> RepositoryCodeContext:
            source = RepositorySourceFile(
                path="src/auth.ts",
                blob_sha="a" * 40,
                content_digest=content_digest("tokenStore.save(token);"),
                text="tokenStore.save(token);",
            )
            return RepositoryCodeContext(
                repository=repository,
                default_branch="main",
                tree_sha="b" * 40,
                context_digest=content_digest({"tree": "b" * 40, "file": source.content_digest}),
                files=(source,),
            )

    def model_factory(provider: str):
        def create(**kwargs: object) -> object:
            return FakeModelPort(provider=provider, **kwargs)

        return create

    monkeypatch.setattr(runtime, "OpenAIModelPort", model_factory("openai"), raising=False)
    monkeypatch.setattr(runtime, "GoogleGeminiModelPort", model_factory("google"), raising=False)
    monkeypatch.setattr(runtime, "BedrockModelPort", model_factory("aws-bedrock"), raising=False)
    monkeypatch.setattr(runtime, "GitHubAppRepositoryReader", FakeSourceReader)

    app = build_app()

    assert app.state.github_repository_allowlist == (
        "NOK-Apps/flur-sdk",
        "NOK-Apps/flur-frontend",
        "NOK-Apps/flur-backend",
    )
    assert app.state.github_app_broker is not None
    assert app.state.github_repository_reader is not None
    assert app.state.grounded_discussion is not None
    assert app.state.business_analyst is not None
    assert app.state.chat_model_turn_limit == 2
    assert {
        (provider, str(values["model"]), str(values["family"]))
        for provider, values in provider_calls
    } == {
        ("openai", "gpt-6-astra", "openai-astra"),
        ("aws-bedrock", "amazon.nova-pro-v1:0", "amazon-nova-pro"),
        ("openai", "gpt-5.6-luna", "openai-luna"),
        ("google", "gemini-3.8-flash", "google-gemini-flash"),
    }
    tokens_by_model = {
        str(values["model"]): values["max_output_tokens"]
        for _, values in provider_calls
    }
    assert tokens_by_model == {
        "gpt-6-astra": 1536,
        "amazon.nova-pro-v1:0": 512,
        "gpt-5.6-luna": 1536,
        "gemini-3.8-flash": 512,
    }

    architecture_result = app.state.grounded_discussion.answer(
        repository="NOK-Apps/flur-frontend",
        question="Where is the auth token saved?",
        profile="architecture",
    )
    coding_result = app.state.grounded_discussion.answer(
        repository="NOK-Apps/flur-sdk",
        question="Review token persistence.",
        profile="coding",
    )
    business_result = app.state.business_analyst.answer(
        work_item_id="wi-runtime-test",
        question="Refunds confuse our customers.",
    )

    assert architecture_result.status == "ANSWERED"
    assert architecture_result.model_runs[0].model == "gpt-6-astra"
    assert architecture_result.model_runs[1].model == "amazon.nova-pro-v1:0"
    assert coding_result.status == "ANSWERED"
    assert coding_result.model_runs[0].model == "gpt-5.6-luna"
    assert coding_result.model_runs[1].model == "gemini-3.8-flash"
    assert business_result.status == "ELICITING"
    assert business_result.model_runs[0].model == "gpt-6-astra"
    assert business_result.model_runs[1].model == "amazon.nova-pro-v1:0"


def test_runtime_rejects_pilot_installation_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("FACTORY_ENV", "development")
    monkeypatch.setenv("FACTORY_PILOT_CONFIG", str(PILOT_CONFIG))
    monkeypatch.setenv("FACTORY_GITHUB_APP_ID", "5020848")
    monkeypatch.setenv("FACTORY_GITHUB_APP_INSTALLATION_ID", "999999999")
    monkeypatch.setenv(
        "FACTORY_GITHUB_APP_PRIVATE_KEY_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:441186133046:secret:github/apps/nokinc-factory-pilot/private-key",
    )

    try:
        build_app()
    except RuntimeError as error:
        assert "pilot GitHub App configuration is invalid" in str(error)
    else:
        raise AssertionError("runtime accepted an installation outside pilot.yaml")
