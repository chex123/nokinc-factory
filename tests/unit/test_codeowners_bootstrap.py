"""Behavioral tests for bootstrap-generated CODEOWNERS controls."""

import pytest
from codeowners_test_helpers import (
    FACTORY,
    FACTORY_PATHS,
    TARGET,
    TARGET_PATHS,
    run_bash,
    run_bootstrap_identity_check,
    run_bootstrap_owner_validation,
)


def _bootstrap_permissions() -> dict[tuple[str, str], str | None]:
    return {
        (FACTORY, "alice"): "write",
        (FACTORY, "bob"): "maintain",
        (TARGET, "alice"): "admin",
        (TARGET, "bob"): "write",
    }


def test_bootstrap_generates_two_owners_for_every_canonical_path() -> None:
    result = run_bash(
        """
source scripts/bootstrap.sh
output_dir=$(mktemp -d)
trap 'rm -rf "$output_dir"' EXIT
write_factory_codeowners "$output_dir/factory-CODEOWNERS" alice bob
write_target_codeowners "$output_dir/target-CODEOWNERS" alice bob
printf '%s\n' '--- factory ---'
cat "$output_dir/factory-CODEOWNERS"
printf '%s\n' '--- target ---'
cat "$output_dir/target-CODEOWNERS"
"""
    )

    assert result.returncode == 0, result.stdout + result.stderr
    factory_content, target_content = result.stdout.split("--- target ---\n")
    assert factory_content.splitlines()[1:] == [f"{path} @alice @bob" for path in FACTORY_PATHS]
    assert target_content.splitlines() == [f"{path} @alice @bob" for path in TARGET_PATHS]


def test_bootstrap_validates_both_generated_owners_in_every_repository(tmp_path) -> None:
    result = run_bootstrap_owner_validation(tmp_path, _bootstrap_permissions())

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("owner", ["alice", "bob"])
@pytest.mark.parametrize("permission", ["read", "triage"])
def test_bootstrap_rejects_generated_owner_below_write(
    tmp_path,
    owner: str,
    permission: str,
) -> None:
    permissions = _bootstrap_permissions()
    permissions[(TARGET, owner)] = permission

    result = run_bootstrap_owner_validation(tmp_path, permissions)

    assert result.returncode != 0


def test_bootstrap_rejects_owner_permission_api_failure(tmp_path) -> None:
    permissions = _bootstrap_permissions()
    permissions[(FACTORY, "alice")] = None

    result = run_bootstrap_owner_validation(tmp_path, permissions)

    assert result.returncode != 0


def test_bootstrap_rejects_same_exact_owner_and_reviewer(tmp_path) -> None:
    result = run_bootstrap_identity_check(tmp_path, "chex123", "chex123", "chex123")

    assert result.returncode != 0


def test_bootstrap_rejects_case_variant_same_identity(tmp_path) -> None:
    result = run_bootstrap_identity_check(tmp_path, "chex123", "Chex123", "Chex123")

    assert result.returncode != 0


def test_bootstrap_accepts_two_distinct_eligible_identities(tmp_path) -> None:
    result = run_bootstrap_identity_check(tmp_path, "chex123", "triplexapps", "TriplexApps")

    assert result.returncode == 0, result.stdout + result.stderr


def test_bootstrap_rejects_unresolved_gate_reviewer_identity(tmp_path) -> None:
    result = run_bootstrap_identity_check(tmp_path, "chex123", "triplexapps", None)

    assert result.returncode != 0
