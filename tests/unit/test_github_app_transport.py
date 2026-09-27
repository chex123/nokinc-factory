from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, SecretStr

from nokinc_factory.adapters.github_app_broker import (
    BrokeredGitHubTransport,
    InstallationToken,
)

NOW = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)


class Response(BaseModel):
    value: str


class Broker:
    def __init__(self) -> None:
        self.calls = 0

    def token_for(self, repository: str) -> InstallationToken:
        self.calls += 1
        assert repository == "flur-sdk"
        return InstallationToken(
            token=SecretStr("ghs-token"), expires_at=NOW + timedelta(minutes=10),
            repositories=(repository,),
        )


def test_brokered_transport_scopes_and_caches_installation_token(monkeypatch) -> None:
    broker = Broker()
    requests: list[str] = []

    class FakeTransport:
        def __init__(self, token: str, *, api_url: str, timeout: float) -> None:
            assert token == "ghs-token"
            assert api_url == "https://api.github.com"
            assert timeout == 5.0

        def request(self, method: str, path: str, response_model, body=None):
            requests.append(path)
            return response_model(value=path)

    monkeypatch.setattr(
        "nokinc_factory.adapters.github_app_broker.UrllibGitHubTransport", FakeTransport,
    )
    transport = BrokeredGitHubTransport(
        broker=broker, repository="flur-sdk", clock=lambda: NOW,
        timeout=5.0,
    )

    assert transport.request("GET", "/one", Response).value == "/one"
    assert transport.request("GET", "/two", Response).value == "/two"
    assert broker.calls == 1
    assert requests == ["/one", "/two"]
