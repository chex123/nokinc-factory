"""Runtime assembly for the authenticated factory API."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit

import yaml
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, model_validator

from nokinc_factory.adapters.cognito_login import CognitoHostedLogin, CognitoLoginSettings
from nokinc_factory.adapters.github_app_broker import (
    AwsSecretsManagerStore,
    GitHubAppConfig,
    GitHubAppCredentialBroker,
)
from nokinc_factory.adapters.github_repository_reader import GitHubAppRepositoryReader
from nokinc_factory.adapters.model_providers import (
    BedrockModelPort,
    GoogleGeminiModelPort,
    OpenAIModelPort,
)
from nokinc_factory.adapters.oidc_auth import OidcPrincipalVerifier
from nokinc_factory.application.business_analyst import BusinessAnalystDiscussion
from nokinc_factory.application.grounded_repository_discussion import (
    GroundedRepositoryDiscussion,
    GroundedRepositoryDiscussionRouter,
)
from nokinc_factory.application.model_pricing import model_context_limits
from nokinc_factory.application.service import (
    BrowserLoginPort,
    PrincipalVerifier,
    create_app,
)
from nokinc_factory.ports.model import ModelPort


class _ModelRouteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: str = Field(min_length=1, max_length=200)
    provider: Literal["openai", "google", "aws-bedrock"]
    runtime: str = Field(min_length=1, max_length=100)
    credential_ref: str = Field(min_length=1, max_length=1000)
    exact_model_id: str = Field(min_length=1, max_length=200)
    context_window_tokens: int = Field(strict=True, gt=0)
    max_input_tokens: int = Field(strict=True, gt=0)

    @model_validator(mode="after")
    def _runtime_matches_provider(self) -> _ModelRouteConfig:
        expected_runtime = {
            "openai": "native-provider-api-from-aws",
            "google": "native-google-api",
            "aws-bedrock": "bedrock-runtime",
        }[self.provider]
        if self.runtime != expected_runtime:
            raise ValueError("model provider runtime does not match its provider")
        if (
            self.provider == "aws-bedrock"
            and self.credential_ref != "iam-role://factory-model-runtime/bedrock"
        ):
            raise ValueError("Bedrock credentials must come from the task IAM role")
        limits = model_context_limits(provider=self.provider, model=self.exact_model_id)
        if limits is None or (
            self.context_window_tokens != limits.context_window_tokens
            or self.max_input_tokens != limits.max_input_tokens
        ):
            raise ValueError("pilot model context limits do not match provider documentation")
        return self


class _ModelPairConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doer: _ModelRouteConfig
    reviewer: _ModelRouteConfig


class _PilotModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=100)
    routing_principle: str = Field(min_length=1, max_length=500)
    coding: _ModelPairConfig
    architecture_and_business: _ModelPairConfig


class _PilotSecretConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    region: str = Field(pattern=r"^[a-z]{2}(?:-gov)?-[a-z]+-\d+$")
    account_id: str = Field(pattern=r"^[0-9]{12}$")


class _PilotRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    secrets: _PilotSecretConfig
    models: _PilotModelsConfig


def _pilot_config_path() -> Path | None:
    configured_path = os.getenv("FACTORY_PILOT_CONFIG", "").strip()
    candidates = (
        (Path(configured_path),)
        if configured_path
        else (
            Path("/app/pilot.yaml"),
            Path(__file__).resolve().parents[3] / "config" / "pilot.yaml",
        )
    )
    return next((path for path in candidates if path.is_file()), None)


def _pilot_github_app_config() -> GitHubAppConfig | None:
    environment = {
        "FACTORY_GITHUB_APP_ID": os.getenv("FACTORY_GITHUB_APP_ID", "").strip(),
        "FACTORY_GITHUB_APP_INSTALLATION_ID": os.getenv(
            "FACTORY_GITHUB_APP_INSTALLATION_ID", ""
        ).strip(),
        "FACTORY_GITHUB_APP_PRIVATE_KEY_SECRET_ARN": os.getenv(
            "FACTORY_GITHUB_APP_PRIVATE_KEY_SECRET_ARN", ""
        ).strip(),
    }
    configured = [bool(value) for value in environment.values()]
    if not any(configured):
        return None
    missing = next((name for name, value in environment.items() if not value), None)
    if missing is not None:
        raise RuntimeError(f"{missing} is required by the pilot GitHub App configuration")

    config_path = _pilot_config_path()
    if config_path is None:
        raise RuntimeError("pilot.yaml is required when the GitHub App is configured")
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        github = payload.get("github") if isinstance(payload, dict) else None
        app = github.get("app") if isinstance(github, dict) else None
        if not isinstance(github, dict) or not isinstance(app, dict):
            raise ValueError("invalid pilot GitHub settings")
        repositories = app.get("repositories")
        if not isinstance(repositories, list) or any(
            not isinstance(repository, str) for repository in repositories
        ):
            raise ValueError("invalid pilot repository allowlist")
        app_config = GitHubAppConfig.model_validate({
            "organization": github.get("organization"),
            "app_id": app.get("app_id"),
            "installation_id": app.get("installation_id"),
            "private_key_ref": app.get("private_key_ref"),
            "repositories": tuple(repositories),
        })
        if app_config.app_id != environment["FACTORY_GITHUB_APP_ID"]:
            raise ValueError("pilot GitHub App ID mismatch")
        if app_config.installation_id != environment["FACTORY_GITHUB_APP_INSTALLATION_ID"]:
            raise ValueError("pilot GitHub App installation ID mismatch")
        reference = urlsplit(app_config.private_key_ref)
        reference_parts = reference.path.lstrip("/").split("/", maxsplit=1)
        arn_parts = environment["FACTORY_GITHUB_APP_PRIVATE_KEY_SECRET_ARN"].split(":", 5)
        if (
            reference.scheme != "aws-secretsmanager"
            or len(reference_parts) != 2
            or len(arn_parts) != 6
            or arn_parts[0] != "arn"
            or arn_parts[2] != "secretsmanager"
            or arn_parts[3] != reference.netloc
            or arn_parts[4] != reference_parts[0]
            or not arn_parts[5].startswith("secret:")
        ):
            raise ValueError("pilot GitHub App secret reference mismatch")
        expected_secret_name = reference_parts[1]
        actual_secret_name = arn_parts[5][len("secret:"):]
        if re.fullmatch(
            re.escape(expected_secret_name) + r"(?:-[A-Za-z0-9]{6})?",
            actual_secret_name,
        ) is None:
            raise ValueError("pilot GitHub App secret reference mismatch")
        return app_config
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        raise RuntimeError("pilot GitHub App configuration is invalid") from None


def _load_pilot_model_config() -> _PilotRuntimeConfig:
    config_path = _pilot_config_path()
    if config_path is None:
        raise RuntimeError("pilot.yaml is required for model runtime")
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config = _PilotRuntimeConfig.model_validate(payload)
        for pair in (config.models.coding, config.models.architecture_and_business):
            for route in (pair.doer, pair.reviewer):
                if route.provider == "aws-bedrock":
                    continue
                reference = urlsplit(route.credential_ref)
                parts = reference.path.lstrip("/").split("/", maxsplit=1)
                if (
                    reference.scheme != "aws-secretsmanager"
                    or reference.netloc != config.secrets.region
                    or reference.username is not None
                    or reference.password is not None
                    or reference.query
                    or reference.fragment
                    or len(parts) != 2
                    or parts[0] != config.secrets.account_id
                    or not parts[1]
                ):
                    raise ValueError("model secret reference does not match pilot account")
        return config
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        raise RuntimeError("pilot model configuration is invalid") from None


def _model_port(
    route: _ModelRouteConfig,
    *,
    secrets: AwsSecretsManagerStore,
    region: str,
    max_output_tokens: int,
) -> ModelPort:
    if route.provider == "openai":
        return OpenAIModelPort(
            model=route.exact_model_id,
            family=route.family,
            credential_ref=route.credential_ref,
            secrets=secrets,
            max_output_tokens=max_output_tokens,
        )
    if route.provider == "google":
        return GoogleGeminiModelPort(
            model=route.exact_model_id,
            family=route.family,
            credential_ref=route.credential_ref,
            secrets=secrets,
            max_output_tokens=max_output_tokens,
        )
    return BedrockModelPort(
        model=route.exact_model_id,
        family=route.family,
        region=region,
        max_output_tokens=max_output_tokens,
    )


def _build_discussion_profile(
    *,
    source_reader: GitHubAppRepositoryReader,
    model_pair: _ModelPairConfig,
    secrets: AwsSecretsManagerStore,
    region: str,
) -> GroundedRepositoryDiscussion:
    return GroundedRepositoryDiscussion(
        source_reader=source_reader,
        doer=_model_port(
            model_pair.doer,
            secrets=secrets,
            region=region,
            max_output_tokens=1536,
        ),
        reviewer=_model_port(
            model_pair.reviewer,
            secrets=secrets,
            region=region,
            max_output_tokens=512,
        ),
        doer_provider=model_pair.doer.provider,
        doer_model=model_pair.doer.exact_model_id,
        doer_family=model_pair.doer.family,
        reviewer_provider=model_pair.reviewer.provider,
        reviewer_model=model_pair.reviewer.exact_model_id,
        reviewer_family=model_pair.reviewer.family,
        pricing_region=region,
    )


def _build_grounded_discussion(
    *,
    source_reader: GitHubAppRepositoryReader,
    secrets: AwsSecretsManagerStore,
) -> GroundedRepositoryDiscussionRouter:
    config = _load_pilot_model_config()
    architecture = _build_discussion_profile(
        source_reader=source_reader,
        model_pair=config.models.architecture_and_business,
        secrets=secrets,
        region=config.secrets.region,
    )
    coding = _build_discussion_profile(
        source_reader=source_reader,
        model_pair=config.models.coding,
        secrets=secrets,
        region=config.secrets.region,
    )
    return GroundedRepositoryDiscussionRouter(
        architecture=architecture,
        coding=coding,
    )


def _build_business_analyst(
    *,
    secrets: AwsSecretsManagerStore,
) -> BusinessAnalystDiscussion:
    config = _load_pilot_model_config()
    model_pair = config.models.architecture_and_business
    doer = _model_port(
        model_pair.doer,
        secrets=secrets,
        region=config.secrets.region,
        max_output_tokens=1536,
    )
    reviewer = _model_port(
        model_pair.reviewer,
        secrets=secrets,
        region=config.secrets.region,
        max_output_tokens=512,
    )
    return BusinessAnalystDiscussion(
        doer=doer,
        reviewer=reviewer,
        doer_provider=model_pair.doer.provider,
        doer_model=model_pair.doer.exact_model_id,
        doer_family=model_pair.doer.family,
        reviewer_provider=model_pair.reviewer.provider,
        reviewer_model=model_pair.reviewer.exact_model_id,
        reviewer_family=model_pair.reviewer.family,
        pricing_region=config.secrets.region,
    )


def _database_url_from_secret(secret_arn: str) -> str:
    import boto3  # type: ignore[import-untyped]

    try:
        response = boto3.client(
            "secretsmanager",
            region_name=os.getenv("FACTORY_AWS_REGION") or None,
        ).get_secret_value(SecretId=secret_arn)
        secret_string = response.get("SecretString")
        if not isinstance(secret_string, str):
            raise ValueError("database secret has no string value")
        values = json.loads(secret_string)
        if not isinstance(values, dict):
            raise ValueError("database secret must be a JSON object")
        host = values.get("host") or os.getenv("FACTORY_DATABASE_HOST", "").strip()
        port = values.get("port", os.getenv("FACTORY_DATABASE_PORT", "5432"))
        username = values["username"]
        password = values["password"]
        database = values.get("dbname") or os.getenv("FACTORY_DATABASE_NAME", "").strip()
        if isinstance(port, str):
            port = int(port)
        if (
            not isinstance(host, str)
            or not isinstance(port, int)
            or isinstance(port, bool)
            or not isinstance(username, str)
            or not isinstance(password, str)
            or not isinstance(database, str)
            or not host.strip()
            or not database.strip()
        ):
            raise ValueError("database secret fields have invalid types")
        return (
            f"postgresql+psycopg://{quote(username, safe='')}:{quote(password, safe='')}"
            f"@{host}:{port}/{quote(database, safe='')}"
        )
    except Exception as error:
        raise RuntimeError("unable to resolve the configured database secret") from error


def build_app() -> FastAPI:
    environment = os.getenv("FACTORY_ENV", "development").strip().lower()
    auth_mode = os.getenv("FACTORY_AUTH_MODE", "hmac").strip().lower()
    if environment in {"staging", "production"} and auth_mode != "oidc":
        raise RuntimeError("OIDC authentication is required for staging and production")
    if auth_mode not in {"hmac", "oidc"}:
        raise RuntimeError("FACTORY_AUTH_MODE must be hmac or oidc")

    database_url = os.getenv("FACTORY_DATABASE_URL", "").strip()
    database_secret_arn = os.getenv("FACTORY_DATABASE_SECRET_ARN", "").strip()
    secret = os.getenv("FACTORY_AUTH_SECRET", "")
    issuer = os.getenv("FACTORY_AUTH_ISSUER", "factory")
    audience = os.getenv("FACTORY_AUTH_AUDIENCE", "factory-api")
    chat_model_turn_limit_text = os.getenv("FACTORY_CHAT_MODEL_TURN_LIMIT", "0").strip()
    try:
        chat_model_turn_limit = int(chat_model_turn_limit_text)
    except ValueError:
        raise RuntimeError("FACTORY_CHAT_MODEL_TURN_LIMIT must be a nonnegative integer") from None
    if chat_model_turn_limit < 0:
        raise RuntimeError("FACTORY_CHAT_MODEL_TURN_LIMIT must be a nonnegative integer")
    chat_model_tenant_id = os.getenv("FACTORY_CHAT_MODEL_TENANT_ID", "").strip() or None
    principal_verifier: PrincipalVerifier | None = None
    browser_login: BrowserLoginPort | None = None
    cookie_auth_origin: str | None = None
    github_app_config = _pilot_github_app_config()
    aws_secrets = AwsSecretsManagerStore()
    github_app_broker = (
        GitHubAppCredentialBroker(
            config=github_app_config,
            secrets=aws_secrets,
        )
        if github_app_config is not None
        else None
    )
    github_repository_reader = (
        GitHubAppRepositoryReader(
            broker=github_app_broker,
            repositories=github_app_config.repositories,
            max_files=32,
            max_file_bytes=32_000,
            max_total_bytes=800_000,
        )
        if github_app_broker is not None and github_app_config is not None
        else None
    )
    grounded_discussion = (
        _build_grounded_discussion(
            source_reader=github_repository_reader,
            secrets=aws_secrets,
        )
        if github_repository_reader is not None
        else None
    )
    business_analyst = (
        _build_business_analyst(secrets=aws_secrets)
        if github_repository_reader is not None
        else None
    )
    if (
        environment in {"pilot", "staging", "production"}
        and github_repository_reader is not None
        and chat_model_turn_limit == 0
    ):
        raise RuntimeError(
            "FACTORY_CHAT_MODEL_TURN_LIMIT must be positive for model-backed chat"
        )
    if (
        environment in {"pilot", "staging", "production"}
        and github_repository_reader is not None
        and chat_model_turn_limit > 0
        and chat_model_tenant_id is None
    ):
        raise RuntimeError(
            "FACTORY_CHAT_MODEL_TENANT_ID is required when chat model calls are enabled"
        )

    if auth_mode == "oidc":
        oidc_provider = os.getenv("FACTORY_OIDC_PROVIDER", "external").strip().lower()
        if oidc_provider not in {"cognito", "external"}:
            raise RuntimeError("FACTORY_OIDC_PROVIDER must be cognito or external")
        oidc_issuer = os.getenv("FACTORY_OIDC_ISSUER", "").strip()
        oidc_audience = os.getenv("FACTORY_OIDC_AUDIENCE", "").strip()
        jwks_url = os.getenv("FACTORY_OIDC_JWKS_URL", "").strip()
        if not oidc_issuer or not oidc_audience or not jwks_url:
            raise RuntimeError(
                "FACTORY_OIDC_ISSUER, FACTORY_OIDC_AUDIENCE, and FACTORY_OIDC_JWKS_URL "
                "are required for OIDC authentication"
            )
        oidc_verifier = OidcPrincipalVerifier(
            issuer=oidc_issuer,
            audience=oidc_audience,
            jwks_url=jwks_url,
            tenant_claim=os.getenv("FACTORY_OIDC_TENANT_CLAIM", "tenant_id").strip(),
            roles_claim=os.getenv("FACTORY_OIDC_ROLES_CLAIM", "factory_roles").strip(),
        )
        principal_verifier = oidc_verifier

        if oidc_provider == "cognito":
            browser_settings = {
                "FACTORY_COGNITO_DOMAIN": os.getenv("FACTORY_COGNITO_DOMAIN", "").strip(),
                "FACTORY_COGNITO_CLIENT_ID": os.getenv(
                    "FACTORY_COGNITO_CLIENT_ID", ""
                ).strip(),
                "FACTORY_COGNITO_CALLBACK_URL": os.getenv(
                    "FACTORY_COGNITO_CALLBACK_URL", ""
                ).strip(),
                "FACTORY_COGNITO_LOGOUT_URL": os.getenv(
                    "FACTORY_COGNITO_LOGOUT_URL", ""
                ).strip(),
                "FACTORY_PUBLIC_ORIGIN": os.getenv("FACTORY_PUBLIC_ORIGIN", "").strip(),
            }
            missing = next((name for name, value in browser_settings.items() if not value), None)
            if missing is not None:
                raise RuntimeError(f"{missing} is required for Cognito browser authentication")
            if browser_settings["FACTORY_COGNITO_CLIENT_ID"] != oidc_audience:
                raise RuntimeError(
                    "FACTORY_COGNITO_CLIENT_ID must match FACTORY_OIDC_AUDIENCE"
                )
            callback_parts = urlsplit(browser_settings["FACTORY_COGNITO_CALLBACK_URL"])
            logout_parts = urlsplit(browser_settings["FACTORY_COGNITO_LOGOUT_URL"])
            origin_parts = urlsplit(browser_settings["FACTORY_PUBLIC_ORIGIN"])
            if (
                callback_parts.scheme != origin_parts.scheme
                or callback_parts.netloc != origin_parts.netloc
                or callback_parts.path != "/auth/callback"
                or logout_parts.scheme != origin_parts.scheme
                or logout_parts.netloc != origin_parts.netloc
                or logout_parts.path != "/auth/signed-out"
            ):
                raise RuntimeError(
                    "Cognito callback and logout URLs must use FACTORY_PUBLIC_ORIGIN"
                )
            browser_login = CognitoHostedLogin(
                settings=CognitoLoginSettings(
                    hosted_ui_base_url=browser_settings["FACTORY_COGNITO_DOMAIN"],
                    client_id=browser_settings["FACTORY_COGNITO_CLIENT_ID"],
                    callback_url=browser_settings["FACTORY_COGNITO_CALLBACK_URL"],
                    logout_url=browser_settings["FACTORY_COGNITO_LOGOUT_URL"],
                ),
                principal_verifier=oidc_verifier,
            )
            cookie_auth_origin = browser_settings["FACTORY_PUBLIC_ORIGIN"]

    if environment in {"pilot", "staging", "production"}:
        if not database_url:
            if not database_secret_arn:
                raise RuntimeError(
                    "FACTORY_DATABASE_URL or FACTORY_DATABASE_SECRET_ARN is required "
                    "for durable runtime"
                )
            database_url = _database_url_from_secret(database_secret_arn)
        if auth_mode == "hmac" and not secret:
            raise RuntimeError("FACTORY_AUTH_SECRET is required for production runtime")
        from sqlalchemy import create_engine

        from nokinc_factory.adapters.postgres_workflow_store import PostgresWorkflowStore

        store = PostgresWorkflowStore(create_engine(database_url, pool_pre_ping=True))
    else:
        store = None

    return create_app(
        auth_secret=(secret.encode() or None) if auth_mode == "hmac" else None,
        issuer=issuer,
        audience=audience,
        store=store,
        principal_verifier=principal_verifier,
        browser_login=browser_login,
        cookie_auth_origin=cookie_auth_origin,
        github_app_broker=github_app_broker,
        github_repository_reader=github_repository_reader,
        grounded_discussion=grounded_discussion,
        business_analyst=business_analyst,
        chat_model_turn_limit=chat_model_turn_limit,
        chat_model_tenant_id=chat_model_tenant_id,
        github_repositories=(
            github_app_config.repositories if github_app_config is not None else ()
        ),
    )


app = build_app()
def main() -> None:
    import uvicorn

    uvicorn.run(
        "nokinc_factory.application.runtime:app",
        host=os.getenv("FACTORY_API_HOST", "127.0.0.1"),
        port=int(os.getenv("FACTORY_API_PORT", "8080")),
    )
