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

## Required Controls

1. Deploy only from the protected `main` branch through the manual
   `deploy-pilot` workflow. Do not deploy with ad hoc `aws ecs update-service`
   commands.
2. The workflow must pass Ruff, strict mypy, unit and frozen acceptance tests,
   and the dependency audit before it enters the `pilot-deploy` environment.
3. The `pilot-deploy` environment must require an independent reviewer, prevent
   self-review, and allow only the protected default branch. A missing or
   unprotected environment is a hard stop.
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
9. Leave `FACTORY_CHAT_MODEL_TURN_LIMIT` unchanged by default. Raise it only when
   the user explicitly authorizes a count of additional turns; the value is the
   cumulative ceiling, each turn consumes two provider calls, and failed or
   interrupted reservations are never refunded.

## One-Time Setup

- Create repository variable `AWS_PILOT_DEPLOY_ROLE_ARN` for a role in account
  `441186133046`, region `us-east-1`.
- Configure the IAM OIDC trust subject as
  `repo:chex123/nokinc-factory:environment:pilot-deploy` and audience
  `sts.amazonaws.com`.
- Configure the `pilot-deploy` environment with an independent required
  reviewer, self-review prevention, and protected-branch deployment policy.
- Remove direct ECS promotion permissions from personal/operator identities
  after the OIDC path is proven; otherwise an operator can bypass the workflow
  even though the standard path is gated.

## Manual Invocation

From the Actions page, select `deploy-pilot` on `main` and supply a new tag such
as `pilot-<short-sha>-<utc-timestamp>`. The workflow runs tests, waits for the
protected environment, assumes the OIDC role, then calls the deployment script.
For the current authorization of five additional turns, set the cumulative
`model_turn_limit` input to `7`; leave it blank on ordinary image deployments.
For local code-only checks, use:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_deploy_pilot.py -q
```

Do not run a local production promotion as a workaround for a blocked workflow.