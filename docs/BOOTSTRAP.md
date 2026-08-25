# Bootstrap — secure Stage-0 development

The factory cannot safely approve its own control-plane changes before those controls exist. Stage 0 therefore uses GitHub primitives to emulate the frozen architecture while the factory is built.

## What is authoritative

- `docs/factory-spec.md` — frozen architecture/implementation contract
- `docs/MVP.md` — MVP scope and slice order
- this file — how to bootstrap the GitHub demo surface

## Security model used by Stage 0

| Need | Mechanism |
|---|---|
| Business lifecycle | GitHub Issues + labels |
| Protected Gate 1–4 pause | `gate-approval.yml` referencing `gate-1`…`gate-4` environments |
| Bind approval to exact decision | workflow_dispatch input `decision_digest=sha256:...`; workflow run is provider evidence |
| One independent gate reviewer | environment required reviewer + `prevent_self_review=true` |
| Two-person T2 code approval | branch protection `required_approving_review_count=2` |
| Tests before implementation | `frozen-contract` + `baseline-assertion` |
| Deterministic verification | language-specific `gates.yml` |
| Independent frontier review | privileged `workflow_run` job after gates; never executes PR code |
| Cross-model review merge gate | privileged workflow sets `cross-model-review` status on PR head SHA |

**Important:** GitHub environment reviewers and PR reviewers are not interchangeable. An environment proceeds after any one configured reviewer approves; two-person control is therefore enforced by branch protection for T2 target code.

## Repository visibility

The demo targets are fictional and contain no real payment data. Bootstrap creates them **public**, because required environment reviewers are available on public repositories on all current GitHub plans.

`nokinc-factory` defaults to private. If your GitHub plan does not support protected branches on private repos, either use `--factory-public` for the demo or upgrade the GitHub plan. Bootstrap fails rather than silently weakening protection.

## MVP model provider

This scaffold uses **OpenAI only for the first MVP model path**:

- Domain Expert default: `openai:gpt-5.6-terra` via PydanticAI / Responses API.
- Privileged PR reviewer: `gpt-5.6-sol` via the OpenAI Responses API.
- Secret name: `OPENAI_API_KEY`.

This is an implementation choice for the first vertical slice, not an architectural lock-in. `ModelPort` remains provider-neutral and later qualification can add other frontier and self-hosted model families.

## Prerequisites

1. GitHub CLI (`gh`) authenticated with repository/admin rights in the target org/user namespace.
2. A separate GitHub human account with read/write access that will act as the protected environment reviewer.
3. For T2 merge demonstration, at least **two** people with write permission able to approve target PRs.
4. `OPENAI_API_KEY` exported before bootstrap, or set later as a repository secret. The privileged reviewer fails closed if absent.
5. Git Bash or WSL on Windows.

## One command

```bash
export OPENAI_API_KEY=...
./scripts/bootstrap.sh YOURORG --gate-reviewer REVIEWER_LOGIN --dry-run
./scripts/bootstrap.sh YOURORG --gate-reviewer REVIEWER_LOGIN
./scripts/verify.sh YOURORG

# Slice 3 only, after the one-repo loop is boring:
./scripts/bootstrap.sh YOURORG --gate-reviewer REVIEWER_LOGIN --all
./scripts/verify.sh YOURORG --all
```

Add `--factory-public` only if you intentionally want the factory repo public or need public-repo protection on a Free GitHub plan.

## Daily Slice-1 flow

1. Create/refine `[STORY]` until Business Ready.
2. Compute the BusinessReady digest and run **Actions → gate-approval → gate-1**.
3. Architect produces Solution Ready.
4. Compute the SolutionReady digest and run **gate-2**.
5. Test Author opens a `frozen-contract` PR. Only frozen test paths may change; new acceptance behavior must fail on the true baseline.
6. Merge the test PR.
7. Implementer opens an `implementation` PR. It may not modify any frozen acceptance/contract/regression fixtures or bindings.
8. Unprivileged deterministic gates execute PR code. No frontier API secret is present.
9. Only after those gates succeed, the base-trusted `workflow_run` reviewer fetches the diff and linked TaskContext **as data**, calls the configured independent reviewer model, posts findings, and writes the `cross-model-review` commit status.
10. T2 target PRs still require two human approving reviews before merge.

## Why the privileged review split matters

A same-repository PR can contain model-generated code. Secrets must not be available to any job that checks out or executes that code. The privileged reviewer therefore:

- is defined on the trusted default branch;
- starts only after the unprivileged `gates` workflow succeeds;
- never checks out the PR;
- never consumes PR artifacts/caches;
- obtains PR body, linked issues, base instructions and diff through the GitHub API as untrusted **data**;
- fails closed on oversized diffs, malformed reviewer output, API failure or an explicit `ESCALATE`/`CHANGES_REQUIRED` verdict.

## Known, deliberate MVP boundaries

- Stage-0 approval evidence is a GitHub workflow-run reference plus the exact decision digest; the full provider-neutral `ApprovalPort` evidence model lands in the factory implementation.
- Impact classification still uses conservative path/pattern rules until RI AST classification lands; unknown types fail closed.
- Copilot's hosted execution environment is not the final `SandboxPort` and must only touch these known demo repositories.
- SimpleChangeSet has no partial-merge recovery. Do not use it for brownfield production releases.
- Embeddings/RAG and the local/frontier model cascade are intentionally post-MVP.

## Do not proceed when verification fails

`./scripts/verify.sh` checks repository visibility, protected environments, required statuses, admin enforcement, T2 PR approval count, workflows and reviewer secret. A failed check is a hard stop, not a warning.
