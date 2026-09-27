"""Collect exact pytest inventories and build pinned baseline contracts.

This helper only creates evidence inputs. The trusted pipeline must establish the
source/runner/environment digests and retain the subprocess output. A model or
candidate test file cannot choose those identities. The actual test execution is
performed by ``pytest_probe`` in the same isolated worker boundary.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import ClassVar

import pytest

from nokinc_factory.adapters.gate_evidence_io import read_evidence
from nokinc_factory.domain.gate_evidence import (
    BaselineContract,
    CaseID,
    GitSHA,
    ProbeBinding,
)
from nokinc_factory.domain.review_base import (
    Digest,
    Identifier,
    ReviewModel,
    content_digest,
    distinct,
)

MAX_INVENTORY_FILES = 10_000
MAX_INVENTORY_BYTES = 32 * 1024 * 1024


class TestInventory(ReviewModel):
    __test__: ClassVar[bool] = False
    work_item_id: Identifier
    source_sha: GitSHA
    suite_digest: Digest
    runner_digest: Digest
    environment_digest: Digest
    case_ids: tuple[CaseID, ...]
    collection_errors: int
    completed: bool
    exit_code: int


@dataclass
class _InventoryPlugin:
    case_ids: list[str]
    collection_errors: int = 0

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed or report.skipped:
            self.collection_errors += 1

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.case_ids = sorted(item.nodeid for item in session.items)


def _suite_digest(root: Path, paths: tuple[str, ...]) -> str:
    entries: list[dict[str, str]] = []
    total = 0
    for relative in sorted(paths):
        if (not relative or relative in {".", ".git"} or relative.startswith("/")
            or Path(relative).is_absolute() or ".." in Path(relative).parts
            or ".git" in Path(relative).parts):
            raise ValueError("Invalid suite path")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("Suite path escapes its root") from exc
        if candidate.is_symlink():
            raise ValueError("Suite path contains a symlink")
        if candidate.is_file():
            files: tuple[Path, ...] = (candidate,)
        elif candidate.is_dir():
            files = tuple(sorted(path for path in candidate.rglob("*")
                                 if path.is_file() and not path.is_symlink()))
        else:
            raise ValueError(f"Suite path does not exist: {relative}")
        for file in files:
            total += file.stat().st_size
            if total > MAX_INVENTORY_BYTES or len(entries) >= MAX_INVENTORY_FILES:
                raise ValueError("Suite inventory exceeds its evidence budget")
            entries.append({
                "path": file.relative_to(root.resolve()).as_posix(),
                "digest": "sha256:" + sha256(file.read_bytes()).hexdigest(),
            })
    return content_digest(entries)


def collect_inventory(*, root: Path, paths: tuple[str, ...], work_item_id: str,
                      source_sha: str, runner_digest: str, environment_digest: str,
                      run_id: str = "inventory",
                      binding_output: Path | None = None) -> TestInventory:
    plugin = _InventoryPlugin(case_ids=[])
    previous_directory = Path.cwd()
    try:
        os.chdir(root)
        result = pytest.main(["--collect-only", "-q", *paths], plugins=[plugin])
    finally:
        os.chdir(previous_directory)
    inventory = TestInventory(
        work_item_id=work_item_id, source_sha=source_sha,
        suite_digest=_suite_digest(root, paths), runner_digest=runner_digest,
        environment_digest=environment_digest, case_ids=tuple(plugin.case_ids),
        collection_errors=plugin.collection_errors, completed=True, exit_code=int(result),
    )
    if binding_output is not None:
        binding_output.open("x", encoding="utf-8").write(ProbeBinding(
            work_item_id=work_item_id, run_id=run_id,
            source_sha=source_sha, suite_digest=inventory.suite_digest,
            runner_digest=runner_digest, environment_digest=environment_digest,
        ).model_dump_json())
    return inventory


def build_contract(baseline: TestInventory, frozen: TestInventory, *, story_kind: str,
                   output: Path) -> BaselineContract:
    baseline = TestInventory.model_validate(baseline)
    frozen = TestInventory.model_validate(frozen)
    distinct(baseline.case_ids)
    distinct(frozen.case_ids)
    contract = BaselineContract(
        work_item_id=baseline.work_item_id, story_kind=story_kind,
        baseline_sha=baseline.source_sha, baseline_suite_digest=baseline.suite_digest,
        frozen_suite_digest=frozen.suite_digest, runner_digest=baseline.runner_digest,
        environment_digest=baseline.environment_digest, baseline_cases=baseline.case_ids,
        frozen_cases=frozen.case_ids,
        expected_red_cases=tuple(sorted(set(frozen.case_ids) - set(baseline.case_ids))),
    )
    output.open("x", encoding="utf-8").write(contract.model_dump_json())
    return contract


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--root", type=Path, default=Path("."))
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--binding-output", type=Path)
    collect.add_argument("--work-item-id", required=True)
    collect.add_argument("--source-sha", required=True)
    collect.add_argument("--runner-digest", required=True)
    collect.add_argument("--environment-digest", required=True)
    collect.add_argument("--run-id", default="inventory")
    collect.add_argument("--path", action="append", required=True)
    contract = sub.add_parser("contract")
    contract.add_argument("--baseline", type=Path, required=True)
    contract.add_argument("--frozen", type=Path, required=True)
    contract.add_argument(
        "--story-kind",
        choices=("NEW_BEHAVIOR", "BUG_FIX", "REFACTOR", "INFRASTRUCTURE"),
        required=True,
    )
    contract.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            inventory = collect_inventory(
                root=args.root.resolve(), paths=tuple(args.path), work_item_id=args.work_item_id,
                source_sha=args.source_sha, runner_digest=args.runner_digest,
                environment_digest=args.environment_digest, run_id=args.run_id,
                binding_output=args.binding_output,
            )
            args.output.open("x", encoding="utf-8").write(inventory.model_dump_json())
            return 0 if inventory.exit_code == 0 and not inventory.collection_errors else 1
        build_contract(
            read_evidence(args.baseline, TestInventory), read_evidence(args.frozen, TestInventory),
            story_kind=args.story_kind, output=args.output,
        )
        return 0
    except (OSError, ValueError, RuntimeError):
        print("Invalid or unavailable pytest evidence inputs.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())