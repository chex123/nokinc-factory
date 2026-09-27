import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha1
from typing import Any

import pytest
from pydantic import BaseModel

from nokinc_factory.adapters.github_app_broker import InstallationToken
from nokinc_factory.adapters.github_repository_reader import (
    GitHubAppRepositoryReader,
    RepositoryCodeContext,
    RepositorySourceFile,
)

NOW = datetime(2026, 9, 25, 20, tzinfo=UTC)


class FakeBroker:
    def __init__(self, *, expires_at: datetime | None = None) -> None:
        self.repositories: list[str] = []
        self.expires_at = expires_at or NOW + timedelta(minutes=30)

    def token_for(self, repository: str) -> InstallationToken:
        self.repositories.append(repository)
        return InstallationToken(
            token="ghs-test-token",
            expires_at=self.expires_at,
            repositories=(repository.rsplit("/", maxsplit=1)[-1],),
        )


class FakeTransport:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.responses: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        response_model: type[BaseModel],
        body: BaseModel | None = None,
    ) -> BaseModel:
        assert body is None
        self.calls.append((method, path))
        return response_model.model_validate(self.responses.get(path, self.payload))


def _repository_payload(full_name: str = "NOK-Apps/flur-sdk") -> dict[str, Any]:
    return {
        "id": 42001,
        "name": full_name.rsplit("/", maxsplit=1)[-1],
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "default_branch": "main",
        "private": True,
        "visibility": "internal",
        "archived": False,
    }


def test_reader_uses_app_token_for_exact_read_only_repository_request() -> None:
    broker = FakeBroker()
    transport = FakeTransport(_repository_payload())
    reader = GitHubAppRepositoryReader(
        broker=broker,
        repositories=("NOK-Apps/flur-sdk",),
        transport_factory=lambda token: transport,
        clock=lambda: NOW,
    )

    repository = reader.read("NOK-Apps/flur-sdk")

    assert repository.full_name == "NOK-Apps/flur-sdk"
    assert repository.default_branch == "main"
    assert repository.visibility == "internal"
    assert repository.archived is False
    assert broker.repositories == ["NOK-Apps/flur-sdk"]
    assert transport.calls == [("GET", "/repos/NOK-Apps/flur-sdk")]


def test_reader_can_bound_context_at_a_large_model_input_scale() -> None:
    reader = GitHubAppRepositoryReader(
        broker=FakeBroker(),
        repositories=("NOK-Apps/flur-sdk",),
        max_files=32,
        max_file_bytes=32_000,
        max_total_bytes=800_000,
    )

    assert reader._max_files == 32
    assert reader._max_total_bytes == 800_000


def test_repository_context_accepts_the_runtime_file_limit() -> None:
    files = tuple(
        RepositorySourceFile(
            path=f"src/file-{index}.ts",
            blob_sha=f"{index + 1:040x}",
            content_digest="sha256:" + f"{index + 1:064x}",
            text="x",
        )
        for index in range(32)
    )

    context = RepositoryCodeContext(
        repository="NOK-Apps/flur-sdk",
        default_branch="main",
        tree_sha="a" * 40,
        context_digest="sha256:" + "f" * 64,
        files=files,
    )

    assert len(context.files) == 32


def test_reader_rejects_provider_identity_mismatch() -> None:
    reader = GitHubAppRepositoryReader(
        broker=FakeBroker(),
        repositories=("NOK-Apps/flur-sdk",),
        transport_factory=lambda token: FakeTransport(
            _repository_payload("NOK-Apps/flur-frontend")
        ),
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="identity mismatch"):
        reader.read("NOK-Apps/flur-sdk")


def test_reader_rejects_expired_installation_token_before_http_request() -> None:
    broker = FakeBroker(expires_at=NOW - timedelta(seconds=1))
    transport = FakeTransport(_repository_payload())
    reader = GitHubAppRepositoryReader(
        broker=broker,
        repositories=("NOK-Apps/flur-sdk",),
        transport_factory=lambda token: transport,
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="expired"):
        reader.read("NOK-Apps/flur-sdk")

    assert transport.calls == []


def _github_blob_sha(content: bytes) -> str:
    return sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest()


def _github_content(path: str, content: bytes) -> dict[str, Any]:
    return {
        "type": "file",
        "path": path,
        "sha": _github_blob_sha(content),
        "size": len(content),
        "encoding": "base64",
        "content": base64.b64encode(content).decode("ascii"),
    }


def test_code_context_selects_relevant_sources_and_excludes_secrets() -> None:
    auth_content = b"export function login() { return 'session'; }\n"
    readme = b"# Flur SDK\n\nA fintech client library.\n"
    tree = {
        "sha": "a" * 40,
        "truncated": False,
        "tree": [
            {
                "path": "README.md", "type": "blob", "mode": "100644",
                "sha": _github_blob_sha(readme), "size": len(readme),
            },
            {
                "path": "src/auth/login.ts", "type": "blob", "mode": "100644",
                "sha": _github_blob_sha(auth_content), "size": len(auth_content),
            },
            {
                "path": ".env.production", "type": "blob", "mode": "100644",
                "sha": "b" * 40, "size": 44,
            },
            {
                "path": "keys/private.pem", "type": "blob", "mode": "100644",
                "sha": "c" * 40, "size": 128,
            },
        ],
    }
    transport = FakeTransport(_repository_payload())
    transport.responses = {
        "/repos/NOK-Apps/flur-sdk/git/trees/main?recursive=1": tree,
        "/repos/NOK-Apps/flur-sdk/contents/README.md?ref=main": (
            _github_content("README.md", readme)
        ),
        "/repos/NOK-Apps/flur-sdk/contents/src/auth/login.ts?ref=main": (
            _github_content("src/auth/login.ts", auth_content)
        ),
    }
    reader = GitHubAppRepositoryReader(
        broker=FakeBroker(),
        repositories=("NOK-Apps/flur-sdk",),
        transport_factory=lambda token: transport,
        clock=lambda: NOW,
    )

    context = reader.read_codebase_context("NOK-Apps/flur-sdk", "how does auth login work?")

    assert isinstance(context, RepositoryCodeContext)
    assert context.repository == "NOK-Apps/flur-sdk"
    assert context.default_branch == "main"
    assert {source.path for source in context.files} == {"README.md", "src/auth/login.ts"}
    assert all(source.blob_sha for source in context.files)
    assert len(context.context_digest) == 71
    assert not any(
        ".env" in source.path or "private.pem" in source.path
        for source in context.files
    )


def test_code_context_rejects_truncated_repository_tree() -> None:
    transport = FakeTransport(_repository_payload())
    transport.responses["/repos/NOK-Apps/flur-sdk/git/trees/main?recursive=1"] = {
        "sha": "a" * 40,
        "truncated": True,
        "tree": [],
    }
    reader = GitHubAppRepositoryReader(
        broker=FakeBroker(),
        repositories=("NOK-Apps/flur-sdk",),
        transport_factory=lambda token: transport,
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="truncated"):
        reader.read_codebase_context("NOK-Apps/flur-sdk", "inspect auth")

def test_code_context_blocks_embedded_credentials_without_echoing_them() -> None:
    credential = f'const apiKey = "sk-proj-{"A" * 32}";\n'.encode("ascii")
    tree = {
        "sha": "a" * 40,
        "truncated": False,
        "tree": [{
            "path": "src/config.ts",
            "type": "blob",
            "mode": "100644",
            "sha": _github_blob_sha(credential),
            "size": len(credential),
        }],
    }
    transport = FakeTransport(_repository_payload())
    transport.responses = {
        "/repos/NOK-Apps/flur-sdk/git/trees/main?recursive=1": tree,
        "/repos/NOK-Apps/flur-sdk/contents/src/config.ts?ref=main": (
            _github_content("src/config.ts", credential)
        ),
    }
    reader = GitHubAppRepositoryReader(
        broker=FakeBroker(),
        repositories=("NOK-Apps/flur-sdk",),
        transport_factory=lambda token: transport,
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="credential-like material") as error:
        reader.read_codebase_context("NOK-Apps/flur-sdk", "explain client configuration")

    assert credential.decode("ascii") not in str(error.value)