import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, generate_private_key
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from nokinc_factory.adapters.github_app_broker import (
    AwsSecretsManagerStore,
    GitHubAppConfig,
    GitHubAppCredentialBroker,
    InstallationToken,
    SecretValueStore,
)

NOW = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)


class MemorySecrets:
    def __init__(self, value: str) -> None:
        self.value = value
        self.requests: list[str] = []

    def get(self, secret_ref: str) -> str:
        self.requests.append(secret_ref)
        return self.value


class FakeAppTransport:
    def __init__(self) -> None:
        self.jwt: str | None = None
        self.body: dict[str, Any] | None = None

    def create_installation_token(
        self, *, installation_id: str, jwt: str, repositories: tuple[str, ...],
    ) -> InstallationToken:
        self.jwt = jwt
        self.body = {"installation_id": installation_id, "repositories": repositories}
        return InstallationToken(
            token="ghs-test-token", expires_at=NOW.replace(minute=30),
            repositories=repositories,
        )


def config(
    private_key_ref: str = (
        "aws-secretsmanager://us-east-1/441186133046/"
        "github/apps/nokinc-factory-pilot/private-key"
    ),
) -> GitHubAppConfig:
    return GitHubAppConfig(
        app_id="5020848", installation_id="163491501", private_key_ref=private_key_ref,
        organization="NOK-Apps", repositories=("flur-sdk", "flur-frontend"),
    )


def pem() -> tuple[RSAPrivateKey, str]:
    key = generate_private_key(public_exponent=65537, key_size=2048)
    value = key.private_bytes(
        encoding=Encoding.PEM, format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    ).decode("ascii")
    return key, value


def test_broker_mints_short_lived_repo_scoped_installation_token() -> None:
    _, private_key = pem()
    secrets = MemorySecrets(private_key)
    transport = FakeAppTransport()
    broker = GitHubAppCredentialBroker(
        config=config(), secrets=secrets, transport=transport, clock=lambda: NOW,
    )

    token = broker.token_for("flur-sdk")

    assert token.token.get_secret_value() == "ghs-test-token"
    assert token.repositories == ("flur-sdk",)
    assert secrets.requests == [config().private_key_ref]
    assert transport.body == {"installation_id": "163491501", "repositories": ("flur-sdk",)}
    assert transport.jwt is not None
    assert transport.jwt.count(".") == 2


def test_broker_sends_repository_name_when_allowlist_uses_owner_name() -> None:
    _, private_key = pem()
    secrets = MemorySecrets(private_key)
    transport = FakeAppTransport()
    broker = GitHubAppCredentialBroker(
        config=GitHubAppConfig(
            app_id="5020848",
            installation_id="163491501",
            private_key_ref=(
                "aws-secretsmanager://us-east-1/441186133046/"
                "github/apps/nokinc-factory-pilot/private-key"
            ),
            organization="NOK-Apps",
            repositories=("NOK-Apps/flur-sdk",),
        ),
        secrets=secrets,
        transport=transport,
        clock=lambda: NOW,
    )

    token = broker.token_for("NOK-Apps/flur-sdk")

    assert token.repositories == ("flur-sdk",)
    assert transport.body == {"installation_id": "163491501", "repositories": ("flur-sdk",)}


def test_unallowlisted_repository_and_malformed_secret_fail_closed() -> None:
    _, private_key = pem()
    broker = GitHubAppCredentialBroker(
        config=config(), secrets=MemorySecrets(private_key),
        transport=FakeAppTransport(), clock=lambda: NOW,
    )
    with pytest.raises(PermissionError, match="allowlist"):
        broker.token_for("flur-backend")

    malformed = GitHubAppCredentialBroker(
        config=config(), secrets=MemorySecrets("not a pem"),
        transport=FakeAppTransport(), clock=lambda: NOW,
    )
    with pytest.raises(ValueError, match="private key"):
        malformed.token_for("flur-sdk")


def test_secret_store_protocol_does_not_expose_values_in_errors() -> None:
    assert isinstance(MemorySecrets("key"), SecretValueStore)


def test_aws_secret_store_resolves_full_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSecretsManager:
        def __init__(self) -> None:
            self.secret_id: str | None = None

        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            self.secret_id = SecretId
            return {"SecretString": "private-key-material"}

    fake_client = FakeSecretsManager()
    fake_boto3 = SimpleNamespace(
        client=lambda service_name, region_name: (
            fake_client if service_name == "secretsmanager" and region_name == "us-east-1" else None
        )
    )
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    reference = (
        "aws-secretsmanager://us-east-1/441186133046/"
        "github/apps/nokinc-factory-pilot/private-key"
    )

    value = AwsSecretsManagerStore().get(reference)

    assert value == "private-key-material"
    assert fake_client.secret_id == "github/apps/nokinc-factory-pilot/private-key"
