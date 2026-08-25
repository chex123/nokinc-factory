#!/usr/bin/env bash
set -euo pipefail
FACTORY="nokinc-factory"
FACTORY_CODEOWNERS_PATHS=(
  "/tests/acceptance/"
  "/docs/factory-spec.md"
  "/.github/"
)
TARGET_CODEOWNERS_PATHS=(
  "/tests/acceptance/"
  "/tests/contract/"
  "/tests/regression/"
  "/.github/"
)
FAIL=0
ok(){ printf '  \033[32mok\033[0m   %s\n' "$*"; }
bad(){ printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=1; }

is_individual_codeowner() {
  [[ "$1" =~ ^@[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?$ ]]
}

is_expected_codeowners_path() {
  local candidate="$1"
  shift
  local expected
  for expected in "$@"; do
    [[ "$candidate" == "$expected" ]] && return 0
  done
  return 1
}

verify_codeowners() {
  local org="$1"
  local repository="$2"
  local encoded_codeowners codeowners path line owner permission
  local valid=1
  local -a expected_paths fields owners
  local -A path_counts path_owners seen_owners

  if [[ "$repository" == "$FACTORY" ]]; then
    expected_paths=("${FACTORY_CODEOWNERS_PATHS[@]}")
  else
    expected_paths=("${TARGET_CODEOWNERS_PATHS[@]}")
  fi

  if ! encoded_codeowners=$(gh api "repos/$org/$repository/contents/.github/CODEOWNERS" --jq .content); then
    bad "CODEOWNERS unavailable"
    return 1
  fi
  if ! codeowners=$(printf '%s' "$encoded_codeowners" | tr -d '\r\n' | base64 --decode 2>/dev/null); then
    bad "CODEOWNERS content could not be decoded"
    return 1
  fi

  for path in "${expected_paths[@]}"; do
    path_counts["$path"]=0
    path_owners["$path"]=""
  done

  while IFS= read -r line || [[ -n "$line" ]]; do
    fields=()
    read -r -a fields <<< "$line"
    [[ "${#fields[@]}" -eq 0 || "${fields[0]}" == \#* ]] && continue

    path="${fields[0]}"
    if ! is_expected_codeowners_path "$path" "${expected_paths[@]}"; then
      bad "CODEOWNERS has unsupported path rule: $path"
      valid=0
      continue
    fi

    path_counts["$path"]=$((path_counts["$path"] + 1))
    if [[ "${path_counts[$path]}" -gt 1 ]]; then
      bad "CODEOWNERS has duplicate protected path: $path"
      valid=0
      continue
    fi
    path_owners["$path"]="${fields[*]:1}"
  done <<< "$codeowners"

  for path in "${expected_paths[@]}"; do
    if [[ "${path_counts[$path]}" -eq 0 ]]; then
      bad "CODEOWNERS is missing protected path: $path"
      valid=0
      continue
    fi

    owners=()
    read -r -a owners <<< "${path_owners[$path]}"
    seen_owners=()
    local eligible_owner_count=0
    for owner in "${owners[@]}"; do
      if ! is_individual_codeowner "$owner"; then
        bad "CODEOWNERS owner for $path is not an individual user: $owner"
        valid=0
        continue
      fi
      if ! permission=$(gh api "repos/$org/$repository/collaborators/${owner#@}/permission" --jq .permission); then
        bad "CODEOWNERS owner permission lookup failed: $owner"
        valid=0
        continue
      fi
      case "$permission" in
        write|maintain|admin) ;;
        *)
          bad "CODEOWNERS owner lacks write permission: $owner ($permission)"
          valid=0
          continue
          ;;
      esac
      if [[ -z "${seen_owners[$owner]+set}" ]]; then
        seen_owners["$owner"]=1
        eligible_owner_count=$((eligible_owner_count + 1))
      fi
    done
    if [[ "$eligible_owner_count" -lt 2 ]]; then
      bad "CODEOWNERS protected path needs two distinct eligible owners: $path"
      valid=0
    fi
  done

  [[ "$valid" -eq 1 ]]
}

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  return 0
fi

ORG="${1:?usage: verify.sh <github-org> [--all]}"
MODE="${2:-}"
TARGETS=(nokinc-demo-payments)
[[ "$MODE" == "--all" ]] && TARGETS+=(nokinc-demo-payments-sdk nokinc-demo-infra)
ALL=("$FACTORY" "${TARGETS[@]}")
REQUIRED=(deterministic-gates frozen-contract baseline-assertion cross-model-review)
for r in "${ALL[@]}"; do
  printf '\n\033[1m%s\033[0m\n' "$r"
  gh repo view "$ORG/$r" >/dev/null 2>&1 && ok "repo exists" || { bad "repo missing"; continue; }
  verify_codeowners "$ORG" "$r" || true
  gh api "repos/$ORG/$r/contents/.github/workflows/gates.yml" >/dev/null 2>&1 && ok "language-appropriate gates.yml present" || bad "gates.yml missing"
  gh api "repos/$ORG/$r/contents/.github/workflows/cross-model-review.yml" >/dev/null 2>&1 && ok "privileged review workflow present" || bad "review workflow missing"
  gh api "repos/$ORG/$r/branches/main/protection" >/dev/null 2>&1 && ok "main protected" || bad "main unprotected"
  contexts=$(gh api "repos/$ORG/$r/branches/main/protection" --jq '.required_status_checks.contexts[]?' 2>/dev/null || true)
  for c in "${REQUIRED[@]}"; do echo "$contexts" | grep -Fxq "$c" && ok "required status: $c" || bad "missing required status: $c"; done
  approvals=$(gh api "repos/$ORG/$r/branches/main/protection" --jq '.required_pull_request_reviews.required_approving_review_count' 2>/dev/null || echo 0)
  if [[ "$r" == nokinc-factory ]]; then [[ "$approvals" -ge 1 ]] && ok "PR approvals=$approvals" || bad "factory approval count <1"; else [[ "$approvals" -ge 2 ]] && ok "T2 PR approvals=$approvals" || bad "target approval count <2"; fi
  admins=$(gh api "repos/$ORG/$r/branches/main/protection" --jq '.enforce_admins.enabled' 2>/dev/null || echo false)
  [[ "$admins" == true ]] && ok "admins cannot bypass" || bad "admin enforcement disabled"
  if [[ "$r" != nokinc-factory ]]; then
    private=$(gh repo view "$ORG/$r" --json isPrivate --jq .isPrivate)
    [[ "$private" == false ]] && ok "target is public (required env reviewers supported)" || bad "target is private"
    gh api "repos/$ORG/$r/environments/gate-2" >/dev/null 2>&1 && ok "gate-2 environment exists" || bad "gate-2 environment missing"
  fi
  gh secret list --repo "$ORG/$r" --json name --jq '.[].name' 2>/dev/null | grep -q OPENAI_API_KEY && ok "reviewer secret set" || bad "OPENAI_API_KEY missing"
done
printf '\n'
[[ "$FAIL" -eq 0 ]] && { echo "All bootstrap invariants verified."; exit 0; }
echo "Verification failed. Do not start the first governed story until all items are green."; exit 1
