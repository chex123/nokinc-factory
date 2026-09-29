---
name: factory-image-deployment
description: >-
  Use whenever building, scanning, publishing, promoting, or deploying the
  Factory pilot container image to ECR/ECS. Enforces immutable image identity,
  completed vulnerability scans, and rollback-aware ECS promotion.
---

# Factory Image Deployment

Use this skill for every production Factory image change. The verified sequence
is implemented by `scripts/deploy_pilot.py` and invoked by
`.github/workflows/deploy-pilot.yml`.

## Repository Boundary and SDLC

- Resolve the Factory repository root with `git rev-parse --show-toplevel` and
   keep code, tests, artifacts, and linked worktrees inside that root. Use
   `<repo-root>/.worktrees/<task>` for isolation; never create a sibling worktree
   under the workspace wrapper or outside the repository. Preserve dirty user
   changes; do not reset, clean, stash, or switch their checkout.
- Use a feature branch and test-first changes. Run focused tests, the protected
   deployment quality sequence, and review before requesting promotion. Commit
   and push only with explicit user authorization; never merge or deploy around
   required review or environment approvals.
- Before creating the PR or dispatching this workflow for a Factory runtime or
   application change, run the local real-repository E2E gate in
   `docs/LOCAL-PILOT-VALIDATION.md`. Confirm a recorded result and durable trace;
   mocks and this workflow's own quality job do not replace that check. Missing
   local integration prerequisites are a blocking status, not a waiver. A
   documentation-only change does not need provider calls, but still needs its
   focused tests and lint.

## Required Controls

1. Deploy only from the protected `main` branch through the manual
   `deploy-pilot` workflow. Do not deploy with ad hoc `aws ecs update-service`
   commands.
2. The workflow must pass Ruff, strict mypy, unit and frozen acceptance tests,
   and the dependency audit before it enters the `pilot-deploy` environment.
3. The `pilot-deploy` environment must require an independent reviewer, prevent
   self-review, and allow only the protected default branch. A missing or
   unprotected environment is a hard stop.
   For `chex123/nokinc-factory`, `triplexapps` dispatches the workflow and `chex123` is the sole required `pilot-deploy` reviewer. This is one reviewer
   in addition to the initiator, not two additional reviewers. This setting does not change T2 branch-protection approval rules or the separate `gate-approval` workflow.
4. Use GitHub OIDC and the `AWS_PILOT_DEPLOY_ROLE_ARN` repository variable. The
   role trust must be limited to this repository's `pilot-deploy` environment.
   The role must be least-privilege; `iam:PassRole` is limited to the existing
   Factory task and execution roles. Never use root credentials or a long-lived
   AWS access key.
5. Use a unique tag. The script rejects an existing tag, requires ECR tag
   immutability and scan-on-push, builds `linux/amd64`, and resolves the pushed
   image to its registry digest.
6. Promotion blocks until the exact digest has a completed ECR scan. Any
   critical, high, or unclassified finding blocks deployment. An incomplete,
   failed, or unavailable scan also blocks. Never skip the scan or deploy by
   mutable tag.
7. The ECS task definition is copied from the live revision and only the
   Factory container image changes. The image is pinned by digest. Circuit
   breaker rollback must remain enabled, and the script verifies the final
   service is stable on the new revision with all desired tasks running.
8. Report the source commit, immutable tag, digest, scan status/severity counts,
   task-definition revision, and ECS rollout result. Do not print credentials,
   AWS login output, provider responses, or task environment secret values.
9. Leave `FACTORY_CHAT_MODEL_TURN_LIMIT` unchanged by default. Accept a positive
   integer for a finite cumulative tenant limit or `unlimited` only when the user
   explicitly authorizes uncapped testing. `unlimited` removes the application
   turn ceiling, not provider quotas or charges; it has no hard dollar ceiling.
   Every turn is durably reserved before two provider calls, and failed or
   interrupted reservations are never refunded.

## One-Time Setup

- Create repository variable `AWS_PILOT_DEPLOY_ROLE_ARN` for a role in account
  `441186133046`, region `us-east-1`.
- Configure the IAM OIDC provider as `token.actions.githubusercontent.com` with
   audience `sts.amazonaws.com`. This repository was created after GitHub's
   immutable-subject rollout; the role trust `sub` must be
   `repo:chex123@74789946/nokinc-factory@1346414547:environment:pilot-deploy`.
   Verify the live role trust matches this exact subject; the former name-only
   subject does not match tokens from this repository.
- Configure the `pilot-deploy` environment with an independent required
  reviewer, self-review prevention, and protected-branch deployment policy.
- Remove direct ECS promotion permissions from personal/operator identities
  after the OIDC path is proven; otherwise an operator can bypass the workflow
  even though the standard path is gated.

## Manual Invocation

From the Actions page, select `deploy-pilot` on `main` and supply a new tag such
as `pilot-<short-sha>-<utc-timestamp>`. The workflow runs tests, waits for the
protected environment, assumes the OIDC role, then calls the deployment script.
For explicitly authorized uncapped testing, set `model_turn_limit` to
`unlimited`; the live service remains unchanged until the protected deployment
completes. Leave the input blank on ordinary image deployments.
For local code-only checks, use:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_deploy_pilot.py -q
```

Do not run a local production promotion as a workaround for a blocked workflow.