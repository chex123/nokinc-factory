"""Hash the complete declared flow tree, including nested flows/scripts/baselines.

This detects ordinary drift; it is not a filesystem sandbox or atomic snapshot.
Authoritative runners must mount approved inputs read-only and separately bind
the installed app binary, external fixtures and device image to the pipeline run.
"""

import hashlib
import os
from pathlib import Path

MAX_FLOW_BYTES = 50_000_000
MAX_FLOW_FILES = 1000
MAX_FLOW_ENTRIES = 2000
MAX_FLOW_DEPTH = 16


def flow_digest(root: Path) -> str:
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise ValueError("declared flow directory is unavailable or redirected")
    digest = hashlib.sha256()
    byte_count = 0
    yaml_count = 0
    for path in _bounded_files(root):
        size = path.stat().st_size
        if byte_count + size > MAX_FLOW_BYTES:
            raise ValueError("flow inputs exceed the configured size boundary")
        with path.open("rb") as stream:
            content = stream.read(MAX_FLOW_BYTES - byte_count + 1)
        byte_count += len(content)
        if byte_count > MAX_FLOW_BYTES:
            raise ValueError("flow inputs exceed the configured size boundary")
        name = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        yaml_count += path.suffix.lower() in {".yaml", ".yml"}
    if not yaml_count:
        raise ValueError("no flow YAML files were found")
    return "sha256:" + digest.hexdigest()


def _bounded_files(root: Path) -> list[Path]:
    pending = [(root, 0)]
    files: list[Path] = []
    entries = 0
    while pending:
        directory, depth = pending.pop()
        if depth > MAX_FLOW_DEPTH:
            raise ValueError("flow discovery exceeds depth limit")
        with os.scandir(directory) as children:
            for child in children:
                entries += 1
                if entries > MAX_FLOW_ENTRIES:
                    raise ValueError("flow discovery exceeds entry limit")
                path = Path(child.path)
                if path.is_symlink() or path.is_junction():
                    raise ValueError("redirected paths are not supported in flow inputs")
                if child.is_dir(follow_symlinks=False):
                    pending.append((path, depth + 1))
                elif child.is_file(follow_symlinks=False):
                    files.append(path)
                    if len(files) > MAX_FLOW_FILES:
                        raise ValueError("flow inputs exceed the configured size boundary")
                else:
                    raise ValueError("flow input is not a regular file")
    return sorted(files)