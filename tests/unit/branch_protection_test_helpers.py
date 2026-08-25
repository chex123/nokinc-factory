"""Shared subprocess fixtures for protected-branch governance tests."""

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codeowners_test_helpers import run_bash

REQUIRED_STATUS_CONTEXTS = (
    "deterministic-gates",
    "frozen-contract",
    "baseline-assertion",
    "cross-model-review",
)


@dataclass(frozen=True)
class BranchProtection:
    strict: bool | None
    contexts: tuple[str, ...] | None
    enforce_admins: bool | None
    dismiss_stale_reviews: bool | None
    require_code_owner_reviews: bool | None
    require_last_push_approval: bool | None
    approval_count: int | None
    conversation_resolution: bool | None
    allow_force_pushes: bool | None
    allow_deletions: bool | None


def branch_protection(*, approval_count: int | None = 1) -> BranchProtection:
    return BranchProtection(
        strict=True,
        contexts=REQUIRED_STATUS_CONTEXTS,
        enforce_admins=True,
        dismiss_stale_reviews=True,
        require_code_owner_reviews=True,
        require_last_push_approval=True,
        approval_count=approval_count,
        conversation_resolution=True,
        allow_force_pushes=False,
        allow_deletions=False,
    )


def run_verify_branch_protection(
    tmp_path: Path,
    repository: str,
    protection: BranchProtection,
    *,
    protection_api_succeeds: bool = True,
    serialized_response: str | None = None,
) -> subprocess.CompletedProcess[str]:
    protection_path = f"repos/test-org/{repository}/branches/main/protection"
    protection_response = serialized_response or _branch_protection_response(protection)
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
protection_path={shlex.quote(protection_path)}
protection_response={shlex.quote(protection_response)}
protection_api_succeeds={"1" if protection_api_succeeds else "0"}
[[ "$1" == "api" && "$2" == "$protection_path" && "$3" == "--jq" ]] || exit 1
[[ "$protection_api_succeeds" == "1" ]] || exit 1
printf '%s\n' "$protection_response"
""",
        encoding="utf-8",
        newline="\n",
    )
    fake_gh.chmod(0o755)
    return run_bash(
        f"""
source scripts/verify.sh
FAIL=0
verify_branch_protection test-org {shlex.quote(repository)}
""",
        path_prefix=tmp_path,
    )


def _branch_protection_response(protection: BranchProtection) -> str:
    contexts = "null" if protection.contexts is None else "\x1f".join(protection.contexts)
    fields = (
        protection.strict,
        contexts,
        protection.enforce_admins,
        protection.dismiss_stale_reviews,
        protection.require_code_owner_reviews,
        protection.require_last_push_approval,
        protection.approval_count,
        protection.conversation_resolution,
        protection.allow_force_pushes,
        protection.allow_deletions,
    )
    return "\x1e".join(_protection_field(field) for field in fields)


def _protection_field(value: bool | int | str | None) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)
