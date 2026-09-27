"""One disposable Git view per observation, never the source index/config.

Only allowlisted builtin settings and bounded attribute/ignore data are copied.
Worktree diffs use copied bytes; no source filter/textconv/fsmonitor can execute.
Source refs/object storage remain a trusted, local, optimistically checked input.
Git itself expands configured paths/HOME; empty XDG uses Git's normal fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from base64 import b64decode
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from nokinc_factory.adapters.git_capture_io import metadata, read_regular
from nokinc_factory.adapters.git_capture_limits import CaptureBudget
from nokinc_factory.domain.preflight import RawWorktree, validate_candidate_path

GitExecute = Callable[[tuple[str, ...], Path], bytes]
GitRead = Callable[[tuple[str, ...]], bytes]
CONFIG_DEFAULTS = {
    "core.autocrlf": "false", "core.eol": "native",
    "core.filemode": "false" if os.name == "nt" else "true",
}
CONFIG_READS = {
    ("config", "--get", *options, "--default", default, key)
    for key, default in CONFIG_DEFAULTS.items() for options in ((), ("--type=bool",))
}
_RULE_FILES = (("core.attributesfile", "attributes"), ("core.excludesfile", "ignore"))
_PATH_READ = ("config", "--get", "--null", "--type=path", "--default")


def is_config_read(arguments: tuple[str, ...]) -> bool:
    """Only data-only scalar/path lookups may see trusted user configuration."""
    return arguments in CONFIG_READS or (
        len(arguments) == 7 and arguments[:5] == _PATH_READ
        and arguments[-1] in {key for key, _ in _RULE_FILES}
    )


def _optional_file(path: Path | None, budget: CaptureBudget) -> bytes | None:
    budget.remaining()
    if path is None:
        return None
    try:
        return read_regular(path, max_bytes=budget.limits.max_file_bytes, budget=budget)[0]
    except FileNotFoundError:
        return None


def _settings(repository: Path, execute: GitExecute, budget: CaptureBudget) -> tuple[str, ...]:
    values = []
    for key, default in CONFIG_DEFAULTS.items():
        budget.remaining()
        values.append(execute(("config", "--get", "--default", default, key), repository)
                      .decode("utf-8").strip())
    for index, key in ((0, "core.autocrlf"), (2, "core.filemode")):
        budget.remaining()
        if values[index] not in {"true", "false"} and not (index == 0 and values[index] == "input"):
            values[index] = execute(
                ("config", "--get", "--type=bool", "--default", CONFIG_DEFAULTS[key], key),
                repository,
            ).decode("ascii").strip()
    for value, allowed in zip(values, ({"true", "false", "input"},
                                     {"native", "lf", "crlf"}, {"true", "false"}), strict=False):
        if value not in allowed:
            raise ValueError("Unsupported Git builtin normalization setting")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    for key, name in _RULE_FILES:
        budget.remaining()
        # XDG is literal (even relative or beginning with '~'), unlike config
        # paths. Anchor it to Git's cwd before asking Git to expand the default.
        default = (repository / budget.path(xdg) / "git" / name).as_posix() if xdg else (
            f"~/.config/git/{name}" if os.name == "nt" or "HOME" in os.environ else ""
        )
        budget.path(default)
        raw = execute((*_PATH_READ, default, key), repository)
        if not raw.endswith(b"\0") or b"\0" in raw[:-1]:
            raise ValueError("Unsupported Git config path response")
        # NUL framing preserves leading/trailing whitespace and explicit empty
        # settings. --type=path handles Git HOME, ~user and %(prefix), not Python.
        values.append(budget.path(raw[:-1].decode("utf-8")))
    return tuple(values)


def _quote_path(path: Path) -> str:
    """Git config permits these escapes, not JSON's ASCII Unicode escapes."""
    value = path.as_posix()
    if any(ord(char) < 32 and char not in "\b\t\n" for char in value):
        raise ValueError("Unsupported control character in Git config path")
    escapes: dict[str, str | int | None] = {
        "\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t", "\n": "\\n",
    }
    escaped = value.translate(str.maketrans(escapes))
    return f'"{escaped}"'


def decode_commit(raw: bytes) -> str:
    value = raw.decode("ascii").strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None:
        raise ValueError("Git returned an invalid commit identity")
    return value


@dataclass(frozen=True)
class GitCaptureSource:
    repository: Path
    git_directory: Path
    objects: Path
    head: str
    index: bytes | None
    object_format: str
    settings: tuple[str, ...]
    rules: tuple[tuple[Path | None, bytes | None], ...]
    anchors: tuple[Path, ...]
    anchor_metadata: tuple[tuple[int, ...] | None, ...]
    budget: CaptureBudget

    @classmethod
    def capture(
        cls, repository: Path, execute: GitExecute, budget: CaptureBudget,
    ) -> GitCaptureSource:
        def git_path(*arguments: str) -> Path:
            raw = execute(("rev-parse", "--path-format=absolute", *arguments), repository)
            path = Path(budget.path(raw.decode("utf-8").rstrip("\n"))).resolve()
            budget.path(str(path))
            return path

        if git_path("--show-toplevel") != repository:
            raise ValueError("Capture requires the repository root, not a subdirectory")
        directory, common = git_path("--absolute-git-dir"), git_path("--git-common-dir")
        if execute(("rev-parse", "--shared-index-path"), repository).strip():
            raise ValueError("Unsupported split index")
        object_format = execute(("rev-parse", "--show-object-format"), repository).decode().strip()
        if object_format not in {"sha1", "sha256"}:
            raise ValueError("Unsupported Git object format")
        settings = _settings(repository, execute, budget)
        global_rules = tuple(repository / value if value else None for value in settings[3:])
        paths = (common / "info/attributes", common / "info/exclude", *global_rules)
        anchors = (repository / ".git", directory, directory / "HEAD", directory / "index",
                   directory / "index.lock", common / "config",
                   *(path for path in paths if path is not None))
        budget.metadata(len(anchors))
        head_file = read_regular(directory / "HEAD", budget=budget)[0]
        if head_file.startswith(b"ref: "):
            reference = validate_candidate_path(budget.path(head_file[5:].decode("utf-8").strip()))
            if not reference.startswith("refs/"):
                raise ValueError("Unsupported symbolic HEAD")
            anchors += (common / reference, common / "packed-refs")
        before = metadata(anchors, budget=budget)
        head = decode_commit(execute(("rev-parse", "--verify", "HEAD^{commit}"), repository))
        result = cls(repository, directory, common / "objects", head,
                 _optional_file(directory / "index", budget), object_format, settings,
                 tuple((path, _optional_file(path, budget)) for path in paths),
                 anchors, before, budget)
        result.verify_metadata()
        return result

    @property
    def git_inputs_digest(self) -> str:
        self.budget.remaining()
        raw = json.dumps([self.settings, [None if content is None else content.hex()
                                         for _, content in self.rules]], separators=(",", ":"))
        return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()

    def verify_metadata(self) -> None:
        if (metadata(self.anchors, budget=self.budget) != self.anchor_metadata
                or (self.git_directory / "index.lock").exists()):
            raise ValueError("Git HEAD/index/ignore state changed during capture")

    def verify(self, execute: GitExecute) -> None:
        self.verify_metadata()
        if (
            decode_commit(execute(("rev-parse", "--verify", "HEAD^{commit}"), self.repository))
            != self.head
            or _optional_file(self.git_directory / "index", self.budget) != self.index
            or tuple(_optional_file(path, self.budget) for path, _ in self.rules)
            != tuple(content for _, content in self.rules)
            or _settings(self.repository, execute, self.budget) != self.settings
        ):
            raise ValueError("Git HEAD/index/normalization inputs changed during capture")
        self.verify_metadata()

    @contextmanager
    def view(self, execute: GitExecute) -> Iterator[GitCaptureView]:
        with TemporaryDirectory(prefix="nokinc-git-capture-") as temporary:
            directory = Path(temporary)
            self.budget.path(str(directory / "worktree"))
            (directory / "objects/info").mkdir(parents=True)
            (directory / "refs").mkdir()
            (directory / "info").mkdir()
            worktree = directory / "worktree"
            worktree.mkdir()
            version = int(self.object_format == "sha256")
            config = (
                f"[core]\nrepositoryformatversion={version}\nbare=false\n"
                f"autocrlf={self.settings[0]}\neol={self.settings[1]}\n"
                f"filemode={self.settings[2]}\n"
                f"attributesfile={_quote_path(directory / 'global-attributes')}\n"
                f"excludesfile={_quote_path(directory / 'global-exclude')}\n"
            )
            if version:
                config += "[extensions]\nobjectformat=sha256\n"
            (directory / "config").write_text(config, encoding="utf-8")
            (directory / "HEAD").write_text(self.head + "\n", encoding="ascii")
            if self.index is not None:
                (directory / "index").write_bytes(self.index)
            for name, (_, content) in zip(
                ("info/attributes", "info/exclude", "global-attributes", "global-exclude"),
                self.rules, strict=True,
            ):
                self.budget.remaining()
                (directory / name).write_bytes(content or b"")
            with (directory / "info/attributes").open("ab") as stream:
                stream.write(b"\n* -filter\n")
            (directory / "objects/info/alternates").write_text(
                _quote_path(self.objects) + "\n", encoding="utf-8",
            )
            yield GitCaptureView(self, directory, worktree, execute)


@dataclass(frozen=True)
class GitCaptureView:
    source: GitCaptureSource
    directory: Path
    worktree: Path
    execute: GitExecute

    def read(self, arguments: tuple[str, ...]) -> bytes:
        return self._read(arguments, self.worktree)

    def live_inventory(self) -> bytes:
        # Enumeration includes Git's live ignore reads; only copied bytes reach
        # content diffs. This is not a nofollow claim about Git's own enumeration.
        return self._read(("ls-files", "--others", "--exclude-standard", "-z"),
                          self.source.repository)

    def _read(self, arguments: tuple[str, ...], worktree: Path) -> bytes:
        return self.execute(
            (f"--git-dir={self.directory}", f"--work-tree={worktree}", *arguments),
            self.source.repository,
        )

    def populate(self, raw: RawWorktree) -> str:
        for file in raw.files:
            self.source.budget.path(file.path)
            target = self.worktree / file.path
            self.source.budget.path(str(target))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b64decode(file.content_base64))
            if os.name != "nt":
                target.chmod(0o755 if file.path in raw.executable_paths else 0o644)
        # Rebuild ONCE: copied source stat caches must not hide normalized bytes.
        entries = self.read(("ls-files", "--stage", "-z"))
        tree = decode_commit(self.read(("write-tree",)))
        (self.directory / "index").unlink(missing_ok=True)
        self.read(("read-tree", tree))
        if self.read(("ls-files", "--stage", "-z")) != entries:
            raise ValueError("Unsupported index representation (e.g. intent-to-add)")
        return tree