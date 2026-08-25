#!/usr/bin/env bash
# Bootstrap the MVP GitHub surface.
#
# Usage:
#   ./scripts/bootstrap.sh ORG --gate-reviewer reviewer_login [--all] [--dry-run] [--factory-public]
#
# Security properties:
# - demo targets are PUBLIC so required environment reviewers work on all current GitHub plans
# - environment approval pauses the workflow and prevents self-review
# - T2 code merges require TWO distinct PR approvals via branch protection
# - cross-model review runs privileged only after unprivileged gates, never executes PR code
set -euo pipefail

ORG="${1:?usage: bootstrap.sh <github-org> --gate-reviewer <github-login> [--all] [--dry-run] [--factory-public]}"
shift
DRY=0
FACTORY_PUBLIC=0
ALL_TARGETS=0
GATE_REVIEWER=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --factory-public) FACTORY_PUBLIC=1; shift ;;
    --all) ALL_TARGETS=1; shift ;;
    --gate-reviewer) GATE_REVIEWER="${2:?--gate-reviewer needs a GitHub login}"; shift 2 ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
done
[[ -n "$GATE_REVIEWER" ]] || { echo "--gate-reviewer is required; it must be a human other than the workflow initiator."; exit 2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(dirname "$HERE")"
FACTORY="nokinc-factory"
TARGETS=(nokinc-demo-payments)
if [[ "$ALL_TARGETS" -eq 1 ]]; then
  TARGETS+=(nokinc-demo-payments-sdk nokinc-demo-infra)
fi
ALL=("$FACTORY" "${TARGETS[@]}")

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
run() { if [[ "$DRY" -eq 1 ]]; then printf '  [dry] %s\n' "$*"; else eval "$@"; fi; }

LABELS=(
  "story|0E8A16|Business Ready story"
  "design|0E8A16|Solution Ready design"
  "stage:new|0E8A16|new work item"
  "stage:refining|0E8A16|refining business request"
  "stage:business-ready|0E8A16|awaiting Gate 1"
  "stage:designing|0E8A16|design in progress"
  "stage:solution-ready|0E8A16|awaiting Gate 2"
  "stage:implementing|0E8A16|implementation in progress"
  "stage:dev-verifying|0E8A16|development verification in progress"
  "stage:dev-verified|0E8A16|development verification complete"
  "stage:preprod-verifying|0E8A16|pre-production verification in progress"
  "stage:preprod-verified|0E8A16|pre-production verification complete"
  "stage:releasing|0E8A16|release in progress"
  "stage:done|0E8A16|work item complete"
  "stage:blocked|B60205|work item blocked"
  "stage:rejected|B60205|work item rejected"
  "stage:rolled-back|B60205|work item rolled back"
  "stage:gate-1-approved|1D76DB|Gate 1 passed"
  "stage:gate-2-approved|1D76DB|Gate 2 passed"
  "stage:gate-3-approved|1D76DB|Gate 3 passed"
  "stage:gate-4-approved|1D76DB|Gate 4 passed"
  "frozen-contract|B60205|tests only; no production code"
  "implementation|5319E7|implementation; frozen tests immutable"
  "tier:T0|C2E0C6|constrained change class"
  "tier:T1|FEF2C0|standard"
  "tier:T2|F9D0C4|payments/authz/PII - two-person approval"
)

command -v gh >/dev/null || { echo "gh CLI required: https://cli.github.com"; exit 1; }

# A dry run must be safe and fast even when GitHub is unreachable. Do not perform
# any live API/auth/repository lookup in --dry-run mode. The namespace is used as
# the display actor only; real identity and reviewer validation happen on apply.
if [[ "$DRY" -eq 1 ]]; then
  ME="$ORG"
  REVIEWER_ID="DRY_RUN_ONLY"
else
  gh auth status >/dev/null 2>&1 || { echo "run: gh auth login"; exit 1; }
  ME="$(gh api user --jq .login)"
  [[ "$ME" != "$GATE_REVIEWER" ]] || { echo "--gate-reviewer must differ from the bootstrap/workflow initiator ($ME)."; exit 1; }
  REVIEWER_ID="$(gh api "users/$GATE_REVIEWER" --jq .id)"
fi

say "Bootstrapping into $ORG as $ME; protected gate reviewer: $GATE_REVIEWER"

# Repositories. Targets are public intentionally: GitHub required environment reviewers
# are available on public repos on all current plans. Factory stays private unless asked.
for r in "${ALL[@]}"; do
  [[ -d "$ROOT/$r" ]] || { echo "missing directory $ROOT/$r"; exit 1; }
  visibility="--public"
  [[ "$r" == "$FACTORY" && "$FACTORY_PUBLIC" -eq 0 ]] && visibility="--private"

  if [[ "$DRY" -eq 1 ]]; then
    say "Ensuring $r ($visibility)"
    echo "  [dry] if missing: initialize local repo, commit scaffold, create $ORG/$r and push"
  elif gh repo view "$ORG/$r" >/dev/null 2>&1; then
    echo "  = $r exists"
  else
    say "Creating $r ($visibility)"
    run "(cd '$ROOT/$r' && git init -qb main 2>/dev/null || true)"
    run "(cd '$ROOT/$r' && git add -A && git commit -qm 'scaffold')"
    run "(cd '$ROOT/$r' && gh repo create '$ORG/$r' $visibility --source=. --push)"
  fi
done

# Existing targets must be public or the environment reviewer demo is not enforceable
# on Free/Pro/Team. Do not silently degrade.
if [[ "$DRY" -eq 0 ]]; then
  for r in "${TARGETS[@]}"; do
    private="$(gh repo view "$ORG/$r" --json isPrivate --jq .isPrivate)"
    [[ "$private" == "false" ]] || {
      echo "$r is private. Required environment reviewers are not available on private repos on Free/Pro/Team."
      echo "Make this fictional demo repo public, or use GitHub Enterprise Cloud. Aborting rather than weakening gates."
      exit 1
    }
  done
fi

# Required environment reviewers must already have repository access. Fail with a
# clear prerequisite error instead of sending an invalid/empty reviewer rule.
if [[ "$DRY" -eq 0 ]]; then
  for r in "${TARGETS[@]}"; do
    permission="$(gh api "repos/$ORG/$r/collaborators/$GATE_REVIEWER/permission" --jq .permission 2>/dev/null || true)"
    case "$permission" in
      read|triage|write|maintain|admin) ;;
      *)
        echo "$GATE_REVIEWER must be an accepted collaborator on $ORG/$r before it can be an environment reviewer."
        echo "Grant at least read permission and accept the invitation, then rerun bootstrap."
        exit 1
        ;;
    esac
  done
fi

# Common factory control files only. Language-specific gates.yml stays owned by each target.
say "Propagating common factory controls (NOT language-specific gates)"
for r in "${TARGETS[@]}"; do
  run "mkdir -p '$ROOT/$r/.github/workflows' '$ROOT/$r/.github/ISSUE_TEMPLATE'"
  run "cp '$ROOT/$FACTORY/.github/workflows/cross-model-review.yml' '$ROOT/$r/.github/workflows/'"
  run "cp '$ROOT/$FACTORY/.github/workflows/gate-approval.yml' '$ROOT/$r/.github/workflows/'"
  run "cp '$ROOT/$FACTORY/.github/ISSUE_TEMPLATE/'*.yml '$ROOT/$r/.github/ISSUE_TEMPLATE/'"
  run "cp '$ROOT/$FACTORY/templates/target-copilot-instructions.md' '$ROOT/$r/.github/copilot-instructions.md'"
  run "cp '$ROOT/$FACTORY/templates/target-AGENTS.md' '$ROOT/$r/AGENTS.md'"
  run "mkdir -p '$ROOT/$r/docs' && cp '$ROOT/$FACTORY/docs/factory-spec.md' '$ROOT/$r/docs/factory-spec.md'"
  run "printf '/tests/acceptance/ @%s\n/tests/contract/ @%s\n/tests/regression/ @%s\n/.github/ @%s\n' '$ME' '$ME' '$ME' '$ME' > '$ROOT/$r/.github/CODEOWNERS'"
done
run "printf '/tests/acceptance/ @%s\n/docs/factory-spec.md @%s\n/.github/ @%s\n' '$ME' '$ME' '$ME' > '$ROOT/$FACTORY/.github/CODEOWNERS'"

say "Creating labels"
for r in "${ALL[@]}"; do
  for spec in "${LABELS[@]}"; do
    IFS='|' read -r name colour desc <<< "$spec"
    run "gh label create '$name' --color '$colour' --description '$desc' --repo '$ORG/$r' --force >/dev/null"
  done
done

# Protected environment approval is a workflow pause, not two-person control.
# It binds one independent human approval to the exact workflow_dispatch inputs.
say "Creating protected gate environments on public demo targets"
for r in "${TARGETS[@]}"; do
  for env in gate-1 gate-2 gate-3 gate-4; do
    PAYLOAD=$(printf '{"reviewers":[{"type":"User","id":%s}],"prevent_self_review":true}' "$REVIEWER_ID")
    if [[ "$DRY" -eq 1 ]]; then
      echo "  [dry] PUT repos/$ORG/$r/environments/$env reviewer=$GATE_REVIEWER prevent_self_review=true"
    else
      printf '%s' "$PAYLOAD" | gh api -X PUT "repos/$ORG/$r/environments/$env" --input - >/dev/null
    fi
  done
  echo "  + $r gate-1..4"
done

say "Setting reviewer secret"
if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  for r in "${ALL[@]}"; do
    run "gh secret set OPENAI_API_KEY --repo '$ORG/$r' --body \"\$OPENAI_API_KEY\""
  done
else
  echo "OPENAI_API_KEY is not set. Cross-model review is fail-closed; set it before the first PR."
fi

# Bootstrap-generated controls must reach main BEFORE main becomes protected.
# Otherwise the bootstrap deadlocks itself: the first protected push is rejected
# because PR-only updates and required status checks are already active.
say "Pushing bootstrap configuration before branch protection"
for r in "${ALL[@]}"; do
  run "(cd '$ROOT/$r' && git add -A && (git diff --cached --quiet || git commit -qm 'factory: protected MVP controls'))"
  run "(cd '$ROOT/$r' && git push -q)"
done

# Two distinct approvals are enforced by branch protection. Environment reviewers
# cannot enforce two-person approval: GitHub proceeds after any ONE listed reviewer.
# This is deliberately the LAST mutating bootstrap step.
say "Protecting main"
for r in "${ALL[@]}"; do
  approvals=1
  [[ "$r" != "$FACTORY" ]] && approvals=2
  payload=$(cat <<JSON
{
  "required_status_checks": {"strict": true, "contexts": ["deterministic-gates", "frozen-contract", "baseline-assertion", "cross-model-review"]},
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": $approvals,
    "require_last_push_approval": true
  },
  "restrictions": null,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
)
  if [[ "$DRY" -eq 1 ]]; then
    echo "  [dry] protect $r approvals=$approvals"
  elif ! printf '%s' "$payload" | gh api -X PUT "repos/$ORG/$r/branches/main/protection" --input - >/dev/null; then
    echo "Failed to protect $r. Public repos support protected branches on all current plans; private factory repos require Pro/Team/Enterprise."
    echo "Re-run with --factory-public or upgrade the GitHub plan. Aborting rather than degrading."
    exit 1
  fi
done

say "Bootstrap complete"
cat <<EOF
Before the first T2 PR:
  * add at least TWO people with write permission to each demo target so branch protection can collect two distinct approvals
  * enable GitHub Copilot coding agent in the repos you will use
  * invoke Gate 1/2 through Actions -> gate-approval with the exact sha256 decision digest

Verify:
  ./scripts/verify.sh $ORG
EOF
