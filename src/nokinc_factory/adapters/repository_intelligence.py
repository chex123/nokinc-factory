"""Bounded deterministic repository catalogue and retrieval primitives."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

from nokinc_factory.domain.review_base import content_digest

_IGNORED_PARTS = frozenset({
    ".git", ".venv", "__pycache__", "node_modules", ".terraform", "dist", "build",
})


@dataclass(frozen=True)
class RepositoryFile:
    path: str
    byte_length: int
    digest: str


@dataclass(frozen=True)
class RepositorySymbol:
    path: str
    name: str
    kind: str
    line: int


@dataclass(frozen=True)
class SearchMatch:
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class RepositorySnapshot:
    files: tuple[str, ...]
    file_digests: tuple[RepositoryFile, ...]
    symbols: tuple[RepositorySymbol, ...]
    test_ids: tuple[str, ...]
    content_digest: str


class RepositoryIntelligence:
    """Read-only, bounded local RI; no repository code is executed."""

    def __init__(self, root: Path, *, max_files: int = 10_000,
                 max_total_bytes: int = 32 * 1024 * 1024,
                 max_file_bytes: int = 2 * 1024 * 1024) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise ValueError("repository root must be a directory")
        if max_files <= 0 or max_total_bytes <= 0 or max_file_bytes <= 0:
            raise ValueError("repository intelligence limits must be positive")
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes
        self.max_file_bytes = max_file_bytes

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ValueError("repository path escapes root") from exc

    def _files(self) -> tuple[RepositoryFile, ...]:
        result: list[RepositoryFile] = []
        total = 0
        for path in sorted(self.root.rglob("*")):
            relative_parts = path.relative_to(self.root).parts
            if any(part in _IGNORED_PARTS for part in relative_parts):
                continue
            if path.is_symlink():
                raise ValueError(f"symlink is not allowed: {path}")
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > self.max_file_bytes:
                raise ValueError(f"file exceeds byte limit: {path}")
            total += size
            if len(result) >= self.max_files or total > self.max_total_bytes:
                raise ValueError("repository exceeds intelligence budget")
            result.append(RepositoryFile(
                path=self._relative(path), byte_length=size,
                digest="sha256:" + sha256(path.read_bytes()).hexdigest(),
            ))
        return tuple(result)

    @staticmethod
    def _python_symbols(relative: str, source: str) -> tuple[RepositorySymbol, ...]:
        try:
            tree = ast.parse(source, filename=relative)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"Python source is not parseable: {relative}") from exc
        symbols: list[RepositorySymbol] = []

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.parents: list[str] = []

            def _visit_definition(self, node: ast.ClassDef | ast.FunctionDef
                                  | ast.AsyncFunctionDef, kind: str) -> None:
                name = ".".join((*self.parents, node.name))
                symbols.append(RepositorySymbol(relative, name, kind, node.lineno))
                self.parents.append(node.name)
                self.generic_visit(node)
                self.parents.pop()

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self._visit_definition(node, "class")

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._visit_definition(node, "function")

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._visit_definition(node, "function")

        Visitor().visit(tree)
        return tuple(symbols)

    def snapshot(self) -> RepositorySnapshot:
        files = self._files()
        symbols: list[RepositorySymbol] = []
        test_ids: list[str] = []
        for entry in files:
            if not entry.path.endswith(".py"):
                continue
            source = (self.root / entry.path).read_text(encoding="utf-8")
            symbols.extend(self._python_symbols(entry.path, source))
            tree = ast.parse(source, filename=entry.path)
            for node in ast.walk(tree):
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test")):
                    test_ids.append(f"{entry.path}::{node.name}")
        symbols_tuple = tuple(sorted(symbols, key=lambda item: (item.path, item.line, item.name)))
        test_tuple = tuple(sorted(set(test_ids)))
        digest = content_digest({
            "files": [asdict(item) for item in files],
            "symbols": [asdict(item) for item in symbols_tuple],
            "test_ids": test_tuple,
        })
        return RepositorySnapshot(
            files=tuple(item.path for item in files), file_digests=files,
            symbols=symbols_tuple, test_ids=test_tuple, content_digest=digest,
        )

    def read(self, relative_path: str, *, max_bytes: int = 256 * 1024) -> str:
        if max_bytes <= 0:
            raise ValueError("read byte limit must be positive")
        path = self.root / relative_path
        if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
            raise ValueError("repository path is invalid")
        if path.is_symlink() or not path.is_file():
            raise ValueError("repository file is unavailable")
        data = path.read_bytes()
        if len(data) > max_bytes:
            raise ValueError("repository read exceeds byte limit")
        return data.decode("utf-8")

    def search(self, query: str, *, max_results: int = 100) -> tuple[SearchMatch, ...]:
        if not query.strip() or max_results <= 0:
            raise ValueError("search query and result limit are required")
        needle = query.casefold()
        matches: list[SearchMatch] = []
        for entry in self._files():
            try:
                source = self.read(entry.path, max_bytes=self.max_file_bytes)
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(source.splitlines(), start=1):
                if needle in line.casefold():
                    matches.append(SearchMatch(entry.path, line_number, line[:1000]))
                    if len(matches) >= max_results:
                        return tuple(matches)
        return tuple(matches)
