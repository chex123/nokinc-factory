"""Shared subprocess fixtures for CODEOWNERS shell-script tests."""

import base64
import os
import shlex
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

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
PermissionResponse = tuple[str, str] | None
BootstrapPermission = str | None


def codeowners(
    paths: Sequence[str],
    owners_by_path: Mapping[str, Sequence[str]] | None = None,
) -> str:
    owners_by_path = owners_by_path or {}
    return "\n".join(
        f"{path} {' '.join(owners_by_path.get(path, ('@alice', '@bob')))}" for path in paths
    )


def run_verify_codeowners(
    tmp_path: Path,
    repository: str,
    codeowners_text: str,
    permissions: Mapping[str, PermissionResponse],
    *,
    codeowners_api_succeeds: bool = True,
    require_protected_branch_ref: bool = False,
) -> subprocess.CompletedProcess[str]:
    encoded_codeowners = base64.b64encode(codeowners_text.encode("utf-8")).decode("ascii")
    contents_path = f"repos/test-org/{repository}/contents/.github/CODEOWNERS?ref=main"
    collaborators_prefix = f"repos/test-org/{repository}/collaborators/"
    permission_cases = "\n".join(
        _permission_case(owner, response) for owner, response in permissions.items()
    )
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
contents_path={shlex.quote(contents_path)}
collaborators_prefix={shlex.quote(collaborators_prefix)}
encoded_codeowners={shlex.quote(encoded_codeowners)}
codeowners_api_succeeds={"1" if codeowners_api_succeeds else "0"}
require_protected_branch_ref={"1" if require_protected_branch_ref else "0"}
[[ "$1" == "api" ]] || exit 1
if [[ "$require_protected_branch_ref" == "1" && "$2" == *"/contents/.github/CODEOWNERS" ]]; then
    exit 1
fi
if [[ "$2" == "$contents_path" ]]; then
  [[ "$codeowners_api_succeeds" == "1" ]] || exit 1
  printf '%s\\n' "$encoded_codeowners"
  exit 0
fi
if [[ "$2" == "$collaborators_prefix"*"/permission" ]]; then
  owner="${{2#"$collaborators_prefix"}}"
  owner="${{owner%/permission}}"
  case "$owner" in
{permission_cases}
    *) exit 1 ;;
  esac
fi
exit 1
""",
        encoding="utf-8",
        newline="\n",
    )
    fake_gh.chmod(0o755)
    return run_bash(
        f"""
source scripts/verify.sh
FAIL=0
verify_codeowners test-org {shlex.quote(repository)}
""",
        path_prefix=tmp_path,
    )


def run_bootstrap_identity_check(
    tmp_path: Path,
    actor: str,
    reviewer_input: str,
    canonical_reviewer: str | None,
) -> subprocess.CompletedProcess[str]:
    reviewer_path = f"users/{reviewer_input}"
    fake_gh = tmp_path / "gh"
    if canonical_reviewer is None:
        response = "exit 1"
    else:
        response = f"printf '%s\\n' {shlex.quote(canonical_reviewer)}"
    fake_gh.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == "api" && "$2" == {shlex.quote(reviewer_path)} ]] || exit 1
{response}
""",
        encoding="utf-8",
        newline="\n",
    )
    fake_gh.chmod(0o755)
    return run_bash(
        f"""
source scripts/bootstrap.sh
[[ {shlex.quote(actor)} != {shlex.quote(reviewer_input)} ]] || exit 1
canonical_reviewer=""
resolve_canonical_github_login {shlex.quote(reviewer_input)} canonical_reviewer
require_case_insensitively_distinct_identities {shlex.quote(actor)} "$canonical_reviewer"
""",
        path_prefix=tmp_path,
    )


def run_bootstrap_owner_validation(
    tmp_path: Path,
    permissions: Mapping[tuple[str, str], BootstrapPermission],
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
    return run_bash(
        """
source scripts/bootstrap.sh
ME=alice
GATE_REVIEWER=bob
verify_generated_codeowner_permissions test-org nokinc-factory nokinc-demo-payments
""",
        path_prefix=tmp_path,
    )


def run_bash(script: str, *, path_prefix: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if path_prefix is not None:
        environment["PATH"] = f"{path_prefix}{os.pathsep}{environment.get('PATH', '')}"
    script_path = ROOT / f".codeowners-test-{uuid.uuid4().hex}.sh"
    script_path.write_text(script, encoding="utf-8", newline="\n")
    try:
        return subprocess.run(
            ["bash", script_path.name],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
    finally:
        script_path.unlink(missing_ok=True)


def _permission_case(owner: str, response: PermissionResponse) -> str:
    if response is None:
        action = "exit 1"
    else:
        canonical_login, permission = response
        quoted_login = shlex.quote(canonical_login)
        quoted_permission = shlex.quote(permission)
        action = (
            "if [[ \"$4\" == \"--jq\" && \"$5\" == \".permission\" ]]; then "
            f"printf '%s\\n' {quoted_permission}; else "
            f"printf '%s\\t%s\\n' {quoted_login} {quoted_permission}; "
            "fi; exit 0"
        )
    return f"    {shlex.quote(owner)}) {action} ;;"
