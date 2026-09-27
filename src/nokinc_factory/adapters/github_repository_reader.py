"""Read-only GitHub repository metadata access through the installed App."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha1, sha256
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode, urlsplit

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, field_validator

from nokinc_factory.adapters.github_app_broker import GitHubAppCredentialPort
from nokinc_factory.adapters.github_issues_transport import (
    GitHubTransport,
    UrllibGitHubTransport,
)
from nokinc_factory.domain.review_base import content_digest

_GIT_SHA_PATTERN = r"^[0-9a-f]{40}$"
_TEXT_EXTENSIONS = frozenset({
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
    ".json", ".jsx", ".kt", ".md", ".mjs", ".php", ".py", ".rb", ".rs",
    ".sql", ".swift", ".tf", ".toml", ".ts", ".tsx", ".txt", ".xml",
    ".yaml", ".yml",
})
_MANIFEST_NAMES = frozenset({
    "cargo.toml", "go.mod", "package.json", "pyproject.toml", "requirements.txt",
})
_SENSITIVE_PATH_SEGMENTS = frozenset({
    ".env", "secret", "secrets", "credential", "credentials", "private",
    "private-key", "keys", "certificates",
})
_SENSITIVE_EXTENSIONS = frozenset({".pem", ".key", ".p12", ".pfx", ".keystore"})
_CREDENTIAL_CONTENT_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    r"\b(?:gh[pousr]_[A-Z0-9]{20,}|github_pat_[A-Z0-9_]{20,})\b",
    r"\bsk-(?:proj-|svcacct-)?[A-Z0-9_-]{24,}\b",
    r"\bAIza[A-Z0-9_-]{30,}\b",
    r"\bxox[baprs]-[A-Z0-9-]{20,}\b",
    r"\beyJ[A-Z0-9_-]{10,}\.[A-Z0-9_-]{10,}\.[A-Z0-9_-]{10,}\b",
    r"\bbearer\s+[A-Z0-9._~+/=-]{24,}",
    r"\b(?:api[_-]?key|client[_-]?secret|password|passwd|access[_-]?token|"
    r"refresh[_-]?token)\b\s*[:=]\s*['\"]?[A-Z0-9/+_=-]{20,}",
))


class GitHubTreeEntry(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    mode: str = Field(pattern=r"^[0-7]{6}$")
    type: str = Field(pattern=r"^(blob|tree|commit)$")
    sha: str = Field(pattern=_GIT_SHA_PATTERN)
    size: int | None = Field(default=None, ge=0)


class GitHubTreeResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    sha: str = Field(pattern=_GIT_SHA_PATTERN)
    truncated: bool
    tree: list[GitHubTreeEntry] = Field(max_length=20_000)


class GitHubContentResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    type: str = Field(pattern="^file$")
    path: str = Field(min_length=1, max_length=1024)
    sha: str = Field(pattern=_GIT_SHA_PATTERN)
    size: int = Field(ge=0)
    encoding: str = Field(pattern="^base64$")
    content: str = Field(max_length=200_000)


class RepositorySourceFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    blob_sha: str = Field(pattern=_GIT_SHA_PATTERN)
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    text: str = Field(min_length=1, max_length=32_000)


class RepositoryCodeContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str = Field(min_length=3)
    default_branch: str = Field(min_length=1)
    tree_sha: str = Field(pattern=_GIT_SHA_PATTERN)
    context_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    files: tuple[RepositorySourceFile, ...] = Field(min_length=1, max_length=8)


class GitHubRepositoryMetadata(BaseModel):
    """Minimal repository identity and routing data returned by GitHub."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    id: int = Field(gt=0)
    full_name: str = Field(min_length=3)
    html_url: AnyUrl
    default_branch: str = Field(min_length=1, max_length=255)
    private: bool
    visibility: str = Field(pattern=r"^(public|private|internal)$")
    archived: bool

    @field_validator("html_url")
    @classmethod
    def _require_https_github_url(cls, value: AnyUrl) -> AnyUrl:
        if (
            value.scheme != "https"
            or value.host != "github.com"
            or value.username is not None
            or value.password is not None
            or value.query
            or value.fragment
        ):
            raise ValueError("repository html_url must be an HTTPS github.com URL")
        return value


class GitHubAppRepositoryReader:
    """Read bounded, identity-checked repository metadata and source evidence."""

    def __init__(
        self,
        *,
        broker: GitHubAppCredentialPort,
        repositories: tuple[str, ...],
        transport_factory: Callable[[str], GitHubTransport] | None = None,
        api_url: str = "https://api.github.com",
        clock: Callable[[], datetime] | None = None,
        max_files: int = 8,
        max_file_bytes: int = 32_000,
        max_total_bytes: int = 128_000,
    ) -> None:
        if not repositories or len(repositories) != len(set(repositories)):
            raise ValueError("repository reader requires a distinct allowlist")
        if any(
            len(repository.split("/")) != 2
            or any(not part or part in {".", ".."} for part in repository.split("/"))
            for repository in repositories
        ):
            raise ValueError("repository reader allowlist must contain owner/name values")
        self._broker = broker
        self._repositories = repositories
        self._transport_factory = transport_factory or (
            lambda token: UrllibGitHubTransport(token, api_url=api_url)
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        if max_files <= 0 or max_files > 32:
            raise ValueError("repository context file limit must be between 1 and 32")
        if max_file_bytes <= 0 or max_file_bytes > 32_000:
            raise ValueError("repository context file limit must not exceed 32000 bytes")
        if max_total_bytes <= 0 or max_total_bytes > 800_000:
            raise ValueError("repository context limit must not exceed 800000 bytes")
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes

    def read(self, repository: str) -> GitHubRepositoryMetadata:
        transport = self._authorized_transport(repository)
        return self._read_metadata(repository, transport)

    def read_codebase_context(self, repository: str, question: str) -> RepositoryCodeContext:
        """Select and fetch bounded UTF-8 source files relevant to a question."""
        if not isinstance(question, str) or not question.strip() or len(question) > 20_000:
            raise ValueError("repository question must be nonblank and bounded")
        transport = self._authorized_transport(repository)
        metadata = self._read_metadata(repository, transport)
        owner, name = repository.split("/", maxsplit=1)
        tree_path = (
            f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/git/trees/"
            f"{quote(metadata.default_branch, safe='')}?recursive=1"
        )
        tree = transport.request("GET", tree_path, GitHubTreeResponse)
        if tree.truncated:
            raise ValueError("GitHub repository tree is truncated; source context is incomplete")
        selected = self._select_sources(tree.tree, question)
        source_files: list[RepositorySourceFile] = []
        total_bytes = 0
        for entry in selected:
            if entry.size is None or entry.size > self._max_file_bytes:
                continue
            content_path = (
                f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/contents/"
                f"{quote(entry.path, safe='/')}?{urlencode({'ref': metadata.default_branch})}"
            )
            response = transport.request("GET", content_path, GitHubContentResponse)
            if response.path != entry.path or response.sha != entry.sha:
                raise ValueError("GitHub source file identity mismatch")
            if response.size != entry.size or response.size > self._max_file_bytes:
                raise ValueError("GitHub source file exceeds the configured size bound")
            try:
                content = base64.b64decode("".join(response.content.split()), validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("GitHub source file content is invalid base64") from None
            if len(content) != response.size or b"\0" in content:
                raise ValueError("GitHub source file content is invalid or binary")
            expected_blob_sha = sha1(
                b"blob " + str(len(content)).encode("ascii") + b"\0" + content
            ).hexdigest()
            if expected_blob_sha != response.sha:
                raise ValueError("GitHub source file blob SHA mismatch")
            try:
                source_text = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if any(pattern.search(source_text) for pattern in _CREDENTIAL_CONTENT_PATTERNS):
                raise ValueError(
                    "Repository source contains credential-like material; model analysis is blocked"
                )
            total_bytes += len(content)
            if total_bytes > self._max_total_bytes:
                break
            source_files.append(RepositorySourceFile(
                path=entry.path,
                blob_sha=response.sha,
                content_digest="sha256:" + sha256(content).hexdigest(),
                text=source_text,
            ))
            if len(source_files) >= self._max_files:
                break
        if not source_files:
            raise ValueError("No bounded UTF-8 source files were available for this question")
        context_digest = content_digest({
            "repository": repository,
            "default_branch": metadata.default_branch,
            "tree_sha": tree.sha,
            "files": [
                {"path": source.path, "blob_sha": source.blob_sha,
                 "content_digest": source.content_digest}
                for source in source_files
            ],
        })
        return RepositoryCodeContext(
            repository=repository,
            default_branch=metadata.default_branch,
            tree_sha=tree.sha,
            context_digest=context_digest,
            files=tuple(source_files),
        )

    def _authorized_transport(self, repository: str) -> GitHubTransport:
        if repository not in self._repositories:
            raise PermissionError("repository is outside the GitHub App allowlist")
        token = self._broker.token_for(repository)
        repository_name = repository.rsplit("/", maxsplit=1)[-1]
        if repository_name not in token.repositories:
            raise ValueError("installation token is not scoped to the requested repository")
        now = self._clock().astimezone(UTC)
        if token.expires_at.astimezone(UTC) <= now + timedelta(seconds=30):
            raise ValueError("installation token is expired or near expiry")
        return self._transport_factory(token.token.get_secret_value())

    @staticmethod
    def _read_metadata(
        repository: str,
        transport: GitHubTransport,
    ) -> GitHubRepositoryMetadata:
        owner, name = repository.split("/", maxsplit=1)
        path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        metadata = transport.request("GET", path, GitHubRepositoryMetadata)
        if metadata.full_name.casefold() != repository.casefold():
            raise ValueError("GitHub repository identity mismatch")
        expected_path = f"/{quote(owner, safe='')}/{quote(name, safe='')}"
        parts = urlsplit(str(metadata.html_url))
        if parts.path != expected_path:
            raise ValueError("GitHub repository URL identity mismatch")
        return metadata

    def _select_sources(
        self,
        entries: list[GitHubTreeEntry],
        question: str,
    ) -> tuple[GitHubTreeEntry, ...]:
        query_tokens = set(re.findall(r"[a-z0-9]{3,}", question.casefold()))
        candidates: list[tuple[int, str, GitHubTreeEntry]] = []
        for entry in entries:
            path = PurePosixPath(entry.path)
            parts = tuple(part.casefold() for part in path.parts)
            if (
                entry.type != "blob"
                or entry.mode == "120000"
                or path.is_absolute()
                or ".." in path.parts
                or path.suffix.casefold() not in _TEXT_EXTENSIONS
                or path.suffix.casefold() in _SENSITIVE_EXTENSIONS
                or any(part in _SENSITIVE_PATH_SEGMENTS for part in parts)
                or path.name.casefold().startswith(".env")
                or entry.size is None
                or entry.size > self._max_file_bytes
            ):
                continue
            path_text = entry.path.casefold()
            score = sum(4 for token in query_tokens if token in path_text)
            if path.name.casefold() == "readme.md" and len(path.parts) == 1:
                score += 100
            elif path.name.casefold() in _MANIFEST_NAMES and len(path.parts) == 1:
                score += 50
            elif path.suffix.casefold() in {".py", ".ts", ".tsx", ".js", ".jsx"}:
                score += 5
            candidates.append((-score, entry.path.casefold(), entry))
        candidates.sort(key=lambda value: (value[0], value[1]))
        selected: list[GitHubTreeEntry] = []
        total_bytes = 0
        for _, _, entry in candidates:
            size = entry.size or 0
            if total_bytes + size > self._max_total_bytes:
                continue
            selected.append(entry)
            total_bytes += size
            if len(selected) >= self._max_files:
                break
        return tuple(selected)