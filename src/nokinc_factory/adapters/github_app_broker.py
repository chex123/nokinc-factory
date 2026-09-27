"""AWS Secrets Manager-backed GitHub App installation-token broker.

The broker owns no long-lived GitHub token. It retrieves the App private key
from the configured secret backend, creates a short-lived App JWT, and requests
an installation token scoped to one allowlisted repository. Secret values are
never included in exceptions, models, or logs.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import Message
from typing import IO, Any, Protocol, TypeVar, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from nokinc_factory.adapters.github_issues_transport import (
    GitHubTransport,
    UrllibGitHubTransport,
)


@runtime_checkable
class SecretValueStore(Protocol):
    def get(self, secret_ref: str) -> str: ...


class GitHubAppTransport(Protocol):
    def create_installation_token(
        self, *, installation_id: str, jwt: str, repositories: tuple[str, ...],
    ) -> InstallationToken: ...


class GitHubAppCredentialPort(Protocol):
    def token_for(self, repository: str) -> InstallationToken: ...


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class InstallationToken(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: SecretStr
    expires_at: datetime
    repositories: tuple[str, ...] = Field(min_length=1)


class GitHubAppConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    organization: str = Field(min_length=1)
    app_id: str = Field(pattern=r"^[1-9][0-9]*$")
    installation_id: str = Field(pattern=r"^[1-9][0-9]*$")
    private_key_ref: str = Field(min_length=1)
    repositories: tuple[str, ...] = Field(min_length=1)

    @field_validator("repositories")
    @classmethod
    def _repositories_distinct(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("GitHub repository allowlist must be distinct")
        return values


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _jwt(app_id: str, private_key_pem: str, now: datetime) -> str:
    try:
        key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"), password=None,
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("GitHub App private key is invalid") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("GitHub App private key must be RSA")
    current = now.astimezone(UTC)
    issued = int(current.timestamp()) - 60
    expires = issued + 540
    header = _b64(b'{"alg":"RS256","typ":"JWT"}')
    payload = _b64(json.dumps({"iat": issued, "exp": expires, "iss": app_id},
                              separators=(",", ":")).encode("utf-8"))
    unsigned = f"{header}.{payload}".encode("ascii")
    signature = key.sign(unsigned, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64(signature)}"


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str,
        headers: Message, newurl: str,
    ) -> None:
        return None


class UrllibGitHubAppTransport:
    """Minimal GitHub App exchange transport with no implicit redirects."""

    def __init__(self, *, api_url: str = "https://api.github.com", timeout: float = 30.0) -> None:
        if not api_url.startswith("https://") or timeout <= 0:
            raise ValueError("GitHub App transport requires HTTPS and positive timeout")
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout
        self._opener = build_opener(_NoRedirectHandler())

    def create_installation_token(
        self, *, installation_id: str, jwt: str, repositories: tuple[str, ...],
    ) -> InstallationToken:
        body = json.dumps({"repositories": list(repositories)}).encode("utf-8")
        request = Request(
            f"{self._api_url}/app/installations/{installation_id}/access_tokens",
            data=body,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {jwt}",
                "Content-Type": "application/json",
                "User-Agent": "nokinc-factory",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw: Any = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("GitHub App installation-token exchange failed") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("token"), str):
            raise RuntimeError("GitHub App installation-token response is invalid")
        expires = raw.get("expires_at")
        if not isinstance(expires, str):
            raise RuntimeError("GitHub App installation-token expiry is invalid")
        try:
            expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("GitHub App installation-token expiry is invalid") from exc
        return InstallationToken(
            token=SecretStr(raw["token"]), expires_at=expires_at,
            repositories=repositories,
        )


class AwsSecretsManagerStore:
    """Lazy boto3 adapter; AWS credentials come from the runtime IAM role."""

    def get(self, secret_ref: str) -> str:
        parsed = urlsplit(secret_ref)
        if parsed.scheme != "aws-secretsmanager" or not parsed.netloc:
            raise ValueError("secret reference must use aws-secretsmanager://")
        parts = parsed.path.lstrip("/").split("/", 1)
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1]:
            raise ValueError("secret reference is malformed")
        try:
            import boto3  # type: ignore[import-untyped]
            response = boto3.client("secretsmanager", region_name=parsed.netloc).get_secret_value(
                SecretId=parts[1],
            )
        except Exception as exc:
            raise RuntimeError("AWS secret retrieval failed") from exc
        value = response.get("SecretString")
        if not isinstance(value, str) or not value:
            raise ValueError("AWS secret does not contain a string value")
        return value


class GitHubAppCredentialBroker:
    def __init__(
        self, *, config: GitHubAppConfig, secrets: SecretValueStore,
        transport: GitHubAppTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = GitHubAppConfig.model_validate(config)
        self._secrets = secrets
        self._transport = transport or UrllibGitHubAppTransport()
        self._clock = clock or (lambda: datetime.now(UTC))

    def token_for(self, repository: str) -> InstallationToken:
        if repository not in self._config.repositories:
            raise PermissionError("repository is outside the GitHub App allowlist")
        repository_name = repository.rsplit("/", maxsplit=1)[-1]
        private_key = self._secrets.get(self._config.private_key_ref)
        jwt = _jwt(self._config.app_id, private_key, self._clock())
        return self._transport.create_installation_token(
            installation_id=self._config.installation_id,
            jwt=jwt,
            repositories=(repository_name,),
        )


class BrokeredGitHubTransport(GitHubTransport):
    """Use cached short-lived App installation tokens for normal GitHub REST calls."""

    def __init__(
        self, *, broker: GitHubAppCredentialPort, repository: str,
        api_url: str = "https://api.github.com", timeout: float = 30.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._broker = broker
        self._repository = repository
        self._api_url = api_url
        self._timeout = timeout
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cached: InstallationToken | None = None

    def _token(self) -> str:
        now = self._clock().astimezone(UTC)
        if self._cached is None or self._cached.expires_at <= now + timedelta(seconds=60):
            self._cached = self._broker.token_for(self._repository)
        return self._cached.token.get_secret_value()

    def request(
        self, method: str, path: str, response_model: type[ResponseModelT],
        body: BaseModel | None = None,
    ) -> ResponseModelT:
        return UrllibGitHubTransport(
            self._token(), api_url=self._api_url, timeout=self._timeout,
        ).request(method, path, response_model, body)
