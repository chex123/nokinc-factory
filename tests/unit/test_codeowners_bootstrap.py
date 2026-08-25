"""Behavioral contract for canonical bootstrap CODEOWNERS verification."""

import base64
import os
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
FACTORY = "nokinc-factory"
TARGET = "nokinc-demo-payments"
FACTORY_PATHS = (
    "/tests/acceptance/",
    "/docs/factory-spec.md",
    "/.github/",
)
TARGET_PATHS = (
    "/tests/acceptance/",
    "/tests/contract/",
    "/tests/regression/",
    "/.github/",
)
ELIGIBLE_PERMISSIONS = {"alice": "write", "bob": "maintain"}


def _codeowners(
    paths: Sequence[str],
    owners_by_path: Mapping[str, Sequence[str]] | None = None,
) -> str:
    owners_by_path = owners_by_path or {}
    return "\n".join(
        f"{path} {' '.join(owners_by_path.get(path, ('@alice', '@bob')))}" for path in paths
    )


def _run_codeowners_verification(
    tmp_path: Path,
    repository: str,
    codeowners: str,
    permissions: Mapping[str, str | None],
    *,
    codeowners_api_succeeds: bool = True,
) -> subprocess.CompletedProcess[str]:
    encoded_codeowners = base64.b64encode(codeowners.encode("utf-8")).decode("ascii")
    contents_path = f"repos/test-org/{repository}/contents/.github/CODEOWNERS"
    collaborators_prefix = f"repos/test-org/{repository}/collaborators/"
    permission_entries = "|".join(
        f"{owner}={permission if permission is not None else 'api-failure'}"
        for owner, permission in permissions.items()
    )
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
contents_path={shlex.quote(contents_path)}
collaborators_prefix={shlex.quote(collaborators_prefix)}
permission_entries={shlex.quote(permission_entries)}
encoded_codeowners={shlex.quote(encoded_codeowners)}
codeowners_api_succeeds={"1" if codeowners_api_succeeds else "0"}
[[ "$1" == "api" ]] || exit 1
if [[ "$2" == "$contents_path" ]]; then
    [[ "$codeowners_api_succeeds" == "1" ]] || exit 1
    printf '%s\\n' "$encoded_codeowners"
    exit 0
fi
if [[ "$2" == "$collaborators_prefix"*"/permission" ]]; then
    owner="${{2#"$collaborators_prefix"}}"
    owner="${{owner%/permission}}"
    IFS='|' read -r -a entries <<< "$permission_entries"
    for entry in "${{entries[@]}}"; do
        candidate="${{entry%%=*}}"
        permission="${{entry#*=}}"
        if [[ "$candidate" == "$owner" ]]; then
            [[ "$permission" != "api-failure" ]] || exit 1
            printf '%s\\n' "$permission"
            exit 0
        fi
    done
fi
exit 1
""",
        encoding="utf-8",
        newline="\n",
    )
    fake_gh.chmod(0o755)
    script = f"""
source scripts/verify.sh
FAIL=0
if verify_codeowners "test-org" {shlex.quote(repository)}; then
  exit 0
fi
exit 1
"""
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


def _bootstrap_permissions() -> dict[tuple[str, str], str | None]:
    return {
        (repository, owner): permission
        for repository, permission_by_owner in {
            FACTORY: {"alice": "write", "bob": "maintain"},
            TARGET: {"alice": "admin", "bob": "write"},
        }.items()
        for owner, permission in permission_by_owner.items()
    }


def _run_bootstrap_owner_validation(
    tmp_path: Path,
    permissions: Mapping[tuple[str, str], str | None],
) -> subprocess.CompletedProcess[str]:
    permission_entries = "|".join(
        f"{repository}/{owner}={permission if permission is not None else 'api-failure'}"
        for (repository, owner), permission in permissions.items()
    )
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
permission_entries={shlex.quote(permission_entries)}
[[ "$1" == "api" ]] || exit 1
path="$2"
prefix="repos/test-org/"
[[ "$path" == "$prefix"*"/collaborators/"*"/permission" ]] || exit 1
rest="${{path#"$prefix"}}"
repository="${{rest%%/collaborators/*}}"
owner="${{rest#*/collaborators/}}"
owner="${{owner%/permission}}"
key="$repository/$owner"
IFS='|' read -r -a entries <<< "$permission_entries"
for entry in "${{entries[@]}}"; do
    candidate="${{entry%%=*}}"
    permission="${{entry#*=}}"
    if [[ "$candidate" == "$key" ]]; then
        [[ "$permission" != "api-failure" ]] || exit 1
        printf '%s\\n' "$permission"
        exit 0
    fi
done
exit 1
""",
        encoding="utf-8",
        newline="\n",
    )
    fake_gh.chmod(0o755)
    script = """
source scripts/bootstrap.sh
ME=alice
GATE_REVIEWER=bob
if verify_generated_codeowner_permissions test-org nokinc-factory nokinc-demo-payments; then
  exit 0
fi
exit 1
"""
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"},
    )


def test_bootstrap_generates_two_owners_for_every_canonical_path() -> None:
    factory_codeowners = ROOT / ".bootstrap-test-factory-CODEOWNERS"
    target_codeowners = ROOT / ".bootstrap-test-target-CODEOWNERS"
    factory_codeowners.unlink(missing_ok=True)
    target_codeowners.unlink(missing_ok=True)
    script = """
source scripts/bootstrap.sh
write_factory_codeowners .bootstrap-test-factory-CODEOWNERS alice bob
write_target_codeowners .bootstrap-test-target-CODEOWNERS alice bob
printf '%s\n' '--- factory ---'
cat .bootstrap-test-factory-CODEOWNERS
printf '%s\n' '--- target ---'
cat .bootstrap-test-target-CODEOWNERS
"""

    try:
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        factory_codeowners.unlink(missing_ok=True)
        target_codeowners.unlink(missing_ok=True)

    assert result.returncode == 0, result.stdout + result.stderr
    factory_content, target_content = result.stdout.split("--- target ---\n")
    assert factory_content.splitlines()[1:] == [f"{path} @alice @bob" for path in FACTORY_PATHS]
    assert target_content.splitlines() == [f"{path} @alice @bob" for path in TARGET_PATHS]


def test_bootstrap_validates_both_generated_owners_in_every_repository(tmp_path: Path) -> None:
    result = _run_bootstrap_owner_validation(tmp_path, _bootstrap_permissions())

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("owner", ["alice", "bob"])
@pytest.mark.parametrize("permission", ["read", "triage"])
def test_bootstrap_rejects_generated_owner_below_write(
    tmp_path: Path,
    owner: str,
    permission: str,
) -> None:
    permissions = _bootstrap_permissions()
    permissions[(TARGET, owner)] = permission

    result = _run_bootstrap_owner_validation(tmp_path, permissions)

    assert result.returncode != 0


def test_bootstrap_rejects_owner_permission_api_failure(tmp_path: Path) -> None:
    permissions = _bootstrap_permissions()
    permissions[(FACTORY, "alice")] = None

    result = _run_bootstrap_owner_validation(tmp_path, permissions)

    assert result.returncode != 0


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
    paths: Sequence[str],
) -> None:
    result = _run_codeowners_verification(
        tmp_path,
        repository,
        _codeowners(paths),
        ELIGIBLE_PERMISSIONS,
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
    paths: Sequence[str],
    one_owner_path: str,
) -> None:
    result = _run_codeowners_verification(
        tmp_path,
        repository,
        _codeowners(paths, {one_owner_path: ("@alice",)}),
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
    paths: Sequence[str],
) -> None:
    result = _run_codeowners_verification(
        tmp_path,
        repository,
        _codeowners(paths[1:]),
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
    paths: Sequence[str],
) -> None:
    result = _run_codeowners_verification(
        tmp_path,
        repository,
        f"{_codeowners(paths)}\n{paths[0]} @alice @bob",
        ELIGIBLE_PERMISSIONS,
    )

    assert result.returncode != 0


@pytest.mark.parametrize("owner", ["@missing", "@factory/maintainers", "owner-token"])
def test_nonexistent_or_nonindividual_owner_fails(tmp_path: Path, owner: str) -> None:
    result = _run_codeowners_verification(
        tmp_path,
        FACTORY,
        _codeowners(FACTORY_PATHS, {"/.github/": ("@alice", owner)}),
        ELIGIBLE_PERMISSIONS,
    )

    assert result.returncode != 0


@pytest.mark.parametrize("permission", ["read", "triage"])
def test_insufficient_owner_permission_fails(tmp_path: Path, permission: str) -> None:
    result = _run_codeowners_verification(
        tmp_path,
        FACTORY,
        _codeowners(FACTORY_PATHS, {"/.github/": ("@alice", "@reader")}),
        {"alice": "admin", "reader": permission},
    )

    assert result.returncode != 0


def test_missing_codeowners_or_permission_api_failure_fails_closed(tmp_path: Path) -> None:
    missing_codeowners = _run_codeowners_verification(
        tmp_path,
        FACTORY,
        _codeowners(FACTORY_PATHS),
        ELIGIBLE_PERMISSIONS,
        codeowners_api_succeeds=False,
    )
    missing_permission = _run_codeowners_verification(
        tmp_path,
        FACTORY,
        _codeowners(FACTORY_PATHS),
        {"alice": "admin", "bob": None},
    )

    assert missing_codeowners.returncode != 0
    assert missing_permission.returncode != 0
