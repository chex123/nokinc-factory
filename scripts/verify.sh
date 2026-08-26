#!/usr/bin/env bash
# Governance verifier for bootstrap-established GitHub controls.
#
# Why: T2 authorization depends on verified identity, separation of duties,
# and independently enforced review. This script verifies canonical distinct
# CODEOWNER identities and protected-branch controls. Missing, malformed, or
# drifted governance evidence fails closed; otherwise the Factory could report
# governance healthy while review or authorization controls are bypassable.
#
# Spec: docs/factory-spec.md Part 1 (State, Approval and Reconciliation) and
# Part 7 (The Agents / Cross-Model Review).
set -euo pipefail
FACTORY="nokinc-factory"
PROTECTED_BRANCH="main"
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
REQUIRED_STATUS_CONTEXTS=(
  "deterministic-gates"
  "frozen-contract"
  "baseline-assertion"
  "cross-model-review"
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
  local encoded_codeowners codeowners path line owner permission canonical_owner permission_response extra
  local valid=1
  local -a expected_paths fields owners
  local -A path_counts path_owners seen_owners

  if [[ "$repository" == "$FACTORY" ]]; then
    expected_paths=("${FACTORY_CODEOWNERS_PATHS[@]}")
  else
    expected_paths=("${TARGET_CODEOWNERS_PATHS[@]}")
  fi

  if ! encoded_codeowners=$(gh api "repos/$org/$repository/contents/.github/CODEOWNERS?ref=$PROTECTED_BRANCH" --jq .content); then
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
      if ! permission_response=$(gh api "repos/$org/$repository/collaborators/${owner#@}/permission" --jq '[.user.login, .permission] | @tsv'); then
        bad "CODEOWNERS owner permission lookup failed: $owner"
        valid=0
        continue
      fi
      IFS=$'\t' read -r canonical_owner permission extra <<< "$permission_response"
      if [[ -z "$canonical_owner" || -z "$permission" || -n "$extra" ]]; then
        bad "CODEOWNERS owner permission response lacks canonical identity: $owner"
        valid=0
        continue
      fi
      if ! is_individual_codeowner "@$canonical_owner"; then
        bad "CODEOWNERS owner permission response is not an individual identity: $owner"
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
      canonical_owner="${canonical_owner,,}"
      if [[ -z "${seen_owners[$canonical_owner]+set}" ]]; then
        seen_owners["$canonical_owner"]=1
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

verify_branch_protection() {
  local org="$1"
  local repository="$2"
  local protection_response status_contexts encoded_context decoded_context required_context
  local strict contexts enforce_admins dismiss_stale_reviews code_owner_reviews
  local last_push_approval approval_count conversation_resolution force_pushes deletions
  local required_approvals=2
  local -a fields configured_contexts
  local -A configured_context_set=()
  local valid=1

  [[ "$repository" == "$FACTORY" ]] && required_approvals=1

  if ! protection_response=$(gh api "repos/$org/$repository/branches/$PROTECTED_BRANCH/protection" --jq '[
    (.required_status_checks.strict | tostring),
    (.required_status_checks.contexts | if type == "array" then map(tostring | @base64) | join("\u001f") else "null" end),
    (.enforce_admins.enabled | tostring),
    (.required_pull_request_reviews.dismiss_stale_reviews | tostring),
    (.required_pull_request_reviews.require_code_owner_reviews | tostring),
    (.required_pull_request_reviews.require_last_push_approval | tostring),
    (.required_pull_request_reviews.required_approving_review_count | tostring),
    (.required_conversation_resolution.enabled | tostring),
    (.allow_force_pushes.enabled | tostring),
    (.allow_deletions.enabled | tostring)
  ] | join("\u001e")'); then
    bad "$PROTECTED_BRANCH branch protection unavailable"
    return 1
  fi

  IFS=$'\036' read -r -a fields <<< "$protection_response"
  if [[ "${#fields[@]}" -ne 10 ]]; then
    bad "$PROTECTED_BRANCH branch protection response is malformed"
    return 1
  fi

  strict="${fields[0]}"
  contexts="${fields[1]}"
  enforce_admins="${fields[2]}"
  dismiss_stale_reviews="${fields[3]}"
  code_owner_reviews="${fields[4]}"
  last_push_approval="${fields[5]}"
  approval_count="${fields[6]}"
  conversation_resolution="${fields[7]}"
  force_pushes="${fields[8]}"
  deletions="${fields[9]}"

  [[ "$strict" == "true" ]] && ok "strict status checks" || { bad "strict status checks disabled"; valid=0; }
  IFS=$'\037' read -r -a configured_contexts <<< "$contexts"
  for encoded_context in "${configured_contexts[@]}"; do
    if ! decoded_context=$(
      if ! printf '%s' "$encoded_context" | base64 --decode 2>/dev/null; then
        exit 1
      fi
      printf '\034'
    ); then
      bad "status context base64 decoding failed"
      valid=0
      continue
    fi
    decoded_context="${decoded_context%$'\034'}"
    if [[ -z "$decoded_context" ]]; then
      bad "status context base64 decoding produced an empty identity"
      valid=0
      continue
    fi
    configured_context_set["$decoded_context"]=1
  done
  for required_context in "${REQUIRED_STATUS_CONTEXTS[@]}"; do
    if [[ -z "${configured_context_set[$required_context]+set}" ]]; then
      bad "missing required status: $required_context"
      valid=0
    fi
  done
  [[ "$enforce_admins" == "true" ]] && ok "admins cannot bypass" || { bad "admin enforcement disabled"; valid=0; }
  [[ "$dismiss_stale_reviews" == "true" ]] && ok "stale reviews dismissed" || { bad "stale reviews are not dismissed"; valid=0; }
  [[ "$code_owner_reviews" == "true" ]] && ok "code-owner reviews required" || { bad "code-owner reviews disabled"; valid=0; }
  [[ "$last_push_approval" == "true" ]] && ok "last-push approval required" || { bad "last-push approval disabled"; valid=0; }
  if [[ "$approval_count" =~ ^[0-9]+$ ]] && (( approval_count >= required_approvals )); then
    ok "PR approvals=$approval_count"
  else
    bad "approval count below required minimum of $required_approvals"
    valid=0
  fi
  [[ "$conversation_resolution" == "true" ]] && ok "conversation resolution required" || { bad "conversation resolution disabled"; valid=0; }
  [[ "$force_pushes" == "false" ]] && ok "force pushes disabled" || { bad "force pushes enabled"; valid=0; }
  [[ "$deletions" == "false" ]] && ok "branch deletions disabled" || { bad "branch deletions enabled"; valid=0; }

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
for r in "${ALL[@]}"; do
  printf '\n\033[1m%s\033[0m\n' "$r"
  gh repo view "$ORG/$r" >/dev/null 2>&1 && ok "repo exists" || { bad "repo missing"; continue; }
  verify_codeowners "$ORG" "$r" || true
  gh api "repos/$ORG/$r/contents/.github/workflows/gates.yml" >/dev/null 2>&1 && ok "language-appropriate gates.yml present" || bad "gates.yml missing"
  gh api "repos/$ORG/$r/contents/.github/workflows/cross-model-review.yml" >/dev/null 2>&1 && ok "privileged review workflow present" || bad "review workflow missing"
  verify_branch_protection "$ORG" "$r" || true
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
