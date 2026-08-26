"""Behavioral tests for canonical CODEOWNERS verification."""

from pathlib import Path

import pytest
from codeowners_test_helpers import (
    FACTORY,
    FACTORY_PATHS,
    TARGET,
    TARGET_PATHS,
    codeowners,
    run_verify_codeowners,
)

ELIGIBLE_PERMISSIONS = {
    "alice": ("alice", "write"),
    "bob": ("bob", "maintain"),
}


@pytest.mark.parametrize(
    ("repository", "paths"),
    [
        (FACTORY, FACTORY_PATHS),
        (TARGET, TARGET_PATHS),
    ],
)
def test_valid_canonical_codeowners_with_two_eligible_distinct_owners_passes(
    tmp_path: Path,
    repository: str,
    paths: tuple[str, ...],
) -> None:
    result = run_verify_codeowners(tmp_path, repository, codeowners(paths), ELIGIBLE_PERMISSIONS)

    assert result.returncode == 0, result.stdout + result.stderr


def test_codeowners_retrieval_is_explicitly_bound_to_protected_branch(tmp_path: Path) -> None:
    result = run_verify_codeowners(
        tmp_path,
        FACTORY,
        codeowners(FACTORY_PATHS),
        ELIGIBLE_PERMISSIONS,
        require_protected_branch_ref=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("repository", "paths", "one_owner_path"),
    [
        (repository, paths, path)
        for repository, paths in ((FACTORY, FACTORY_PATHS), (TARGET, TARGET_PATHS))
        for path in paths
    ],
)
def test_each_expected_protected_path_rejects_a_single_owner(
    tmp_path: Path,
    repository: str,
    paths: tuple[str, ...],
    one_owner_path: str,
) -> None:
    result = run_verify_codeowners(
        tmp_path,
        repository,
        codeowners(paths, {one_owner_path: ("@alice",)}),
        ELIGIBLE_PERMISSIONS,
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("repository", "paths"),
    [
        (FACTORY, FACTORY_PATHS),
        (TARGET, TARGET_PATHS),
    ],
)
def test_missing_expected_protected_path_fails(
    tmp_path: Path,
    repository: str,
    paths: tuple[str, ...],
) -> None:
    result = run_verify_codeowners(
        tmp_path,
        repository,
        codeowners(paths[1:]),
        ELIGIBLE_PERMISSIONS,
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("repository", "paths"),
    [
        (FACTORY, FACTORY_PATHS),
        (TARGET, TARGET_PATHS),
    ],
)
def test_duplicate_expected_protected_path_fails(
    tmp_path: Path,
    repository: str,
    paths: tuple[str, ...],
) -> None:
    result = run_verify_codeowners(
        tmp_path,
        repository,
        f"{codeowners(paths)}\n{paths[0]} @alice @bob",
        ELIGIBLE_PERMISSIONS,
    )

    assert result.returncode != 0


@pytest.mark.parametrize("owner", ["@missing", "@factory/maintainers", "owner-token"])
def test_nonexistent_or_nonindividual_owner_fails(tmp_path: Path, owner: str) -> None:
    result = run_verify_codeowners(
        tmp_path,
        FACTORY,
        codeowners(FACTORY_PATHS, {"/.github/": ("@alice", owner)}),
        ELIGIBLE_PERMISSIONS,
    )

    assert result.returncode != 0


@pytest.mark.parametrize("permission", ["read", "triage"])
def test_insufficient_owner_permission_fails(tmp_path: Path, permission: str) -> None:
    result = run_verify_codeowners(
        tmp_path,
        FACTORY,
        codeowners(FACTORY_PATHS, {"/.github/": ("@alice", "@reader")} ),
        {"alice": ("alice", "admin"), "reader": ("reader", permission)},
    )

    assert result.returncode != 0


def test_missing_codeowners_or_permission_api_failure_fails_closed(tmp_path: Path) -> None:
    missing_codeowners = run_verify_codeowners(
        tmp_path,
        FACTORY,
        codeowners(FACTORY_PATHS),
        ELIGIBLE_PERMISSIONS,
        codeowners_api_succeeds=False,
    )
    missing_permission = run_verify_codeowners(
        tmp_path,
        FACTORY,
        codeowners(FACTORY_PATHS),
        {"alice": ("alice", "admin"), "bob": None},
    )

    assert missing_codeowners.returncode != 0
    assert missing_permission.returncode != 0


def test_missing_canonical_identity_in_permission_response_fails_closed(tmp_path: Path) -> None:
    result = run_verify_codeowners(
        tmp_path,
        FACTORY,
        codeowners(FACTORY_PATHS),
        {"alice": ("", "write"), "bob": ("bob", "admin")},
    )

    assert result.returncode != 0


def test_case_variant_codeowner_tokens_count_as_one_identity(tmp_path: Path) -> None:
    result = run_verify_codeowners(
        tmp_path,
        FACTORY,
        codeowners(FACTORY_PATHS, {"/.github/": ("@alice", "@Alice")} ),
        {
            "alice": ("alice", "write"),
            "Alice": ("Alice", "maintain"),
            "bob": ("bob", "write"),
        },
    )

    assert result.returncode != 0


def test_canonical_identity_casing_is_deduplicated(tmp_path: Path) -> None:
    result = run_verify_codeowners(
        tmp_path,
        FACTORY,
        codeowners(FACTORY_PATHS, {"/.github/": ("@alice", "@Alice")} ),
        {
            "alice": ("Alice", "write"),
            "Alice": ("ALICE", "maintain"),
            "bob": ("bob", "write"),
        },
    )

    assert result.returncode != 0


def test_different_canonical_eligible_identities_pass(tmp_path: Path) -> None:
    result = run_verify_codeowners(
        tmp_path,
        FACTORY,
        codeowners(FACTORY_PATHS),
        {
            "alice": ("Alice", "write"),
            "bob": ("BOB", "admin"),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
