"""Behavioral tests for protected-branch governance verification."""

import base64
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from branch_protection_test_helpers import (
    REQUIRED_STATUS_CONTEXTS,
    BranchProtection,
    branch_protection,
    run_verify_branch_protection,
    serialize_branch_protection,
)
from codeowners_test_helpers import FACTORY, ROOT, TARGET


def _jq_filter_serialized_protection(protection: BranchProtection) -> str:
    """Evaluate verify.sh jq string literals, then feed the result to Bash."""
    source = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")
    start = source.index("verify_branch_protection()")
    end = source.index("\nif [[", start)
    literals = re.findall(r'join\(("(?:\\.|[^"\\])*")\)', source[start:end])
    assert len(literals) == 2
    assert "map(tostring | @base64) | join(" in source[start:end]
    context_separator, record_separator = (cast(str, json.loads(literal)) for literal in literals)
    assert context_separator == "\x1f"
    assert record_separator == "\x1e"
    contexts = (
        "null"
        if protection.contexts is None
        else context_separator.join(
            _base64_context(context)
            for context in protection.contexts
        )
    )
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
    return record_separator.join(_jq_tostring(field) for field in fields)


def _jq_tostring(value: bool | int | str | None) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _base64_context(context: str) -> str:
    return base64.b64encode(context.encode("utf-8")).decode("ascii")


@pytest.mark.parametrize(
    ("repository", "approval_count"),
    [
        (FACTORY, 1),
        (TARGET, 2),
    ],
)
def test_complete_branch_protection_contract_passes(
    tmp_path: Path,
    repository: str,
    approval_count: int,
) -> None:
    result = run_verify_branch_protection(
        tmp_path,
        repository,
        branch_protection(approval_count=approval_count),
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_exact_required_status_contexts_pass(tmp_path: Path) -> None:
    protection = replace(branch_protection(), contexts=REQUIRED_STATUS_CONTEXTS)
    result = run_verify_branch_protection(tmp_path, FACTORY, protection)

    assert result.returncode == 0, result.stdout + result.stderr


def test_combined_status_context_does_not_satisfy_required_contexts(tmp_path: Path) -> None:
    protection = replace(
        branch_protection(),
        contexts=("deterministic-gates frozen-contract baseline-assertion cross-model-review",),
    )
    result = run_verify_branch_protection(tmp_path, FACTORY, protection)

    assert result.returncode != 0


@pytest.mark.parametrize(
    "non_exact_context",
    ["deterministic-gates-extra", "extra-deterministic-gates"],
)
def test_status_context_requires_exact_identity(tmp_path: Path, non_exact_context: str) -> None:
    protection = replace(
        branch_protection(),
        contexts=(non_exact_context, *REQUIRED_STATUS_CONTEXTS[1:]),
    )
    result = run_verify_branch_protection(tmp_path, FACTORY, protection)

    assert result.returncode != 0


def test_duplicate_valid_status_contexts_pass(tmp_path: Path) -> None:
    protection = replace(
        branch_protection(),
        contexts=(*REQUIRED_STATUS_CONTEXTS, "deterministic-gates", "cross-model-review"),
    )
    result = run_verify_branch_protection(tmp_path, FACTORY, protection)

    assert result.returncode == 0, result.stdout + result.stderr


def test_jq_serialization_uses_control_separators_consumed_by_bash(tmp_path: Path) -> None:
    protection = branch_protection()
    result = run_verify_branch_protection(
        tmp_path,
        FACTORY,
        protection,
        serialized_response=_jq_filter_serialized_protection(protection),
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_unit_separator_inside_one_context_does_not_create_required_contexts(
    tmp_path: Path,
) -> None:
    protection = replace(
        branch_protection(),
        contexts=("\x1f".join(REQUIRED_STATUS_CONTEXTS),),
    )
    result = run_verify_branch_protection(
        tmp_path,
        FACTORY,
        protection,
        serialized_response=_jq_filter_serialized_protection(protection),
    )

    assert result.returncode != 0


def test_record_separator_inside_context_cannot_corrupt_outer_parser(tmp_path: Path) -> None:
    protection = replace(
        branch_protection(),
        contexts=(*REQUIRED_STATUS_CONTEXTS, "audit\x1econtrol"),
    )
    result = run_verify_branch_protection(
        tmp_path,
        FACTORY,
        protection,
        serialized_response=_jq_filter_serialized_protection(protection),
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_invalid_base64_context_serialization_fails_closed(tmp_path: Path) -> None:
    invalid_contexts = "\x1f".join(
        [
            *(_base64_context(context) for context in REQUIRED_STATUS_CONTEXTS),
            "not-base64!",
        ]
    )
    result = run_verify_branch_protection(
        tmp_path,
        FACTORY,
        branch_protection(),
        serialized_response=serialize_branch_protection(
            branch_protection(),
            encoded_contexts=invalid_contexts,
        ),
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    "protection",
    [
        replace(branch_protection(), strict=False),
        replace(branch_protection(), enforce_admins=False),
        replace(branch_protection(), dismiss_stale_reviews=False),
        replace(branch_protection(), require_code_owner_reviews=False),
        replace(branch_protection(), require_last_push_approval=False),
        replace(branch_protection(), conversation_resolution=False),
        replace(branch_protection(), allow_force_pushes=True),
        replace(branch_protection(), allow_deletions=True),
    ],
)
def test_false_required_protection_fields_fail_closed(
    tmp_path: Path,
    protection: BranchProtection,
) -> None:
    result = run_verify_branch_protection(tmp_path, FACTORY, protection)

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("repository", "approval_count"),
    [
        (FACTORY, 0),
        (TARGET, 1),
    ],
)
def test_insufficient_approval_count_fails_closed(
    tmp_path: Path,
    repository: str,
    approval_count: int,
) -> None:
    result = run_verify_branch_protection(
        tmp_path,
        repository,
        branch_protection(approval_count=approval_count),
    )

    assert result.returncode != 0


def test_missing_required_status_fails_closed(tmp_path: Path) -> None:
    protection = replace(
        branch_protection(),
        contexts=("deterministic-gates", "frozen-contract", "baseline-assertion"),
    )
    result = run_verify_branch_protection(tmp_path, FACTORY, protection)

    assert result.returncode != 0


@pytest.mark.parametrize(
    "protection",
    [
        replace(branch_protection(), strict=None),
        replace(branch_protection(), contexts=None),
        replace(branch_protection(), enforce_admins=None),
        replace(branch_protection(), dismiss_stale_reviews=None),
        replace(branch_protection(), require_code_owner_reviews=None),
        replace(branch_protection(), require_last_push_approval=None),
        replace(branch_protection(), approval_count=None),
        replace(branch_protection(), conversation_resolution=None),
        replace(branch_protection(), allow_force_pushes=None),
        replace(branch_protection(), allow_deletions=None),
    ],
)
def test_missing_protection_field_fails_closed(
    tmp_path: Path,
    protection: BranchProtection,
) -> None:
    result = run_verify_branch_protection(tmp_path, FACTORY, protection)

    assert result.returncode != 0


def test_branch_protection_api_failure_fails_closed(tmp_path: Path) -> None:
    result = run_verify_branch_protection(
        tmp_path,
        FACTORY,
        branch_protection(),
        protection_api_succeeds=False,
    )

    assert result.returncode != 0
