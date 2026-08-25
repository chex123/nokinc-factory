#!/usr/bin/env bash
set -euo pipefail
ORG="${1:?usage: verify.sh <github-org> [--all]}"
MODE="${2:-}"
FAIL=0
ok(){ printf '  \033[32mok\033[0m   %s\n' "$*"; }
bad(){ printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=1; }
TARGETS=(nokinc-demo-payments)
[[ "$MODE" == "--all" ]] && TARGETS+=(nokinc-demo-payments-sdk nokinc-demo-infra)
ALL=(nokinc-factory "${TARGETS[@]}")
REQUIRED=(deterministic-gates frozen-contract baseline-assertion cross-model-review)
for r in "${ALL[@]}"; do
  printf '\n\033[1m%s\033[0m\n' "$r"
  gh repo view "$ORG/$r" >/dev/null 2>&1 && ok "repo exists" || { bad "repo missing"; continue; }
  codeowners=$(gh api "repos/$ORG/$r/contents/.github/CODEOWNERS" --jq .content 2>/dev/null | tr -d '\n' | base64 --decode 2>/dev/null || true)
  github_owner_count=$(printf '%s\n' "$codeowners" | awk '$1 == "/.github/" { for (i = 2; i <= NF; i++) if ($i ~ /^@/) print $i }' | sort -u | wc -l | tr -d '[:space:]')
  [[ "$github_owner_count" -ge 2 ]] && ok "/.github/ code owners=$github_owner_count" || bad "/.github/ has fewer than two code owners"
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
