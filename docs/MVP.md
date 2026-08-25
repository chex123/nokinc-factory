# MVP — a factory you can demonstrate

## The rule that shapes everything

**The factory does not demo itself.** It builds a separate target service. A
recursive demo is impossible to follow and proves nothing about generality.

```
nokinc-factory/              the factory                     (this repo)
nokinc-demo-payments/        target: FastAPI service         (Python)
nokinc-demo-payments-sdk/    target: typed client            (TypeScript)
nokinc-demo-infra/           target: Terraform               (HCL)
```

Three repos, three languages. *"One sentence, one ChangeSet, three repos, ordered
merge"* is a far stronger demo than one repo, and it proves the factory is not
biased toward the language it happens to be written in.

## What "working" means for the MVP

An observer sees five things, in order, in about twelve minutes:

1. A conversation that **refuses to guess** — the Domain Expert asks about the
   failure case, the refund window, the test data source, and will not produce a
   story until they are answered
2. A story with **executable acceptance criteria**, not prose
3. Tests written and merged **before** any implementation, proven to fail
4. Gates and **two independent model families** reviewing the same diff
5. A **running service** and a **traceability chain** from the sentence someone
   said to the spans the container emits

Items 3 and 5 are what nobody else demonstrates.

## MVP scope — build exactly this

| Component | Scope for the demo | Deferred |
|---|---|---|
| **CLI** | `chat` `gate` `status` `trace` | everything else |
| **Domain Expert** | PydanticAI agent → `BusinessReady`, refuses to guess | — |
| **Repository Intelligence** | Slice 2: catalogue, tree-sitter symbols, references, OpenAPI, test inventory | embeddings, RAG, call graph, runtime correlation |
| **MCP** | Slice 2: 8 read-only tools | any mutation, approval, merge, deploy, credentials |
| **Approvals** | Protected GitHub environment workflow binds one independent human approval to an exact decision digest; T2 code merge additionally requires **2 PR approvals** | full `ApprovalPort` evidence query + multi-provider approval |
| **Observability** | **Real OTLP → Jaeger. Every span carries `work_item.id`** | collector fleet, metrics backend |
| **Architect** | PydanticAI agent → `SolutionReady` + spans + approved intent | ChangeSet across repos |
| **Test Author** | Copilot coding agent on a `[TESTS]` issue | dedicated agent |
| **Implementer** | Copilot coding agent on an `[IMPL]` issue | dedicated agent |
| **WorkItemPort** | GitHub Issues adapter, one implementation | Jira, ADO |
| **Gate runner** | language-appropriate `gates.yml`; `.factory/toolchain.yaml` is the contract | central `factory gates run` adapter implementation |
| **Reviewers** | Copilot review + `cross-model-review.yml` | reconciliation loop |
| **Trace** | story → issues → PRs → commit → digest → spans | ReleaseBundle |
| **`SimpleChangeSet`** | Slice 3: worktree each, ordered PRs, sequential merge | versioning, evidence invalidation, candidate-SHA validation, `PARTIALLY_MERGED` recovery, contract overlap — all **production ChangeSet**, post-MVP |
| **ToolchainPort** | Python, TypeScript, HCL adapters | the rest |
| **Target services** | 3 repos, FastAPI + SDK + Terraform, assurance stub, OTel | k8s, pre-prod, canary |

**Deliberately out of the MVP:** embeddings and RAG · the model cascade ·
self-hosted GPU inference · contract overlap windows · `PARTIALLY_MERGED` recovery ·
ReleaseBundle/DeploymentBinding · pre-prod · Temporal · `SandboxPort` hardening
beyond containers · the model cascade.

### Why multi-repo IS in, but its hard parts are not

Registering repositories is trivial — clone URLs, a worktree each. The expense is
**coordinated release across repos that cannot merge atomically**: partial-merge
recovery, contract overlap windows, evidence invalidation across the set, ordered
deploy and rollback.

**Greenfield multi-repo is easy because there is no v1 to stay compatible with.**
Nothing is live, so a partial merge is survivable: deploy behind a feature flag
and finish the merge. Brownfield is where the hard machinery earns its keep, and
that arrives with the legacy system.

### How the factory actually reaches the target repos

Three surfaces, easily conflated:

```
DEVELOPER LAPTOP                   FACTORY (deployed service)
┌────────────────┐                 ┌───────────────────────────────────┐
│ VS Code        │                 │ FastAPI + workers                 │
│  Copilot agent │─── MCP ────────▶│ (1) MCP server  READ-MOSTLY       │
│                │   query / read  │      ri.find_references           │
└────────────────┘                 │      factory.get_changeset        │
                                   │                                   │
                                   │ (2) Agents (PydanticAI)           │
                                   │      @agent.tool read_file        │
                                   │      @agent.tool apply_patch      │
                                   │      @agent.tool run_gates        │
                                   │           │                       │
                                   │           v                       │
                                   │ (3) SANDBOX (ephemeral, per task) │
                                   │      git worktree per repo        │
                                   │      +-- demo-payments/           │
                                   │      +-- demo-payments-sdk/       │
                                   │      +-- demo-infra/              │
                                   │           │                       │
                                   └───────────┼───────────────────────┘
                                               │ GitHub API (adapters)
                                               v
                                           PRs opened
```

**(1) serves humans.** Read-mostly, because a laptop is less controlled than the
sandbox. A local agent able to approve or merge is an unaudited path around the
gate model.

**(2) is the execution path.** Plain Python functions registered as agent tools,
calling adapters. No MCP involved.

**(3) is where edits land.** Worktrees, gates, commits, PRs.

You *could* expose (2) over MCP so one tool surface serves both agents and
Copilot. Worth doing once the surface is stable; for the MVP it is indirection
over a function call in the same process.

## The demo target

Small enough to build live, real enough to be credible.

### nokinc-demo-payments (Python, FastAPI)

```
GET  /orders/{id}                order with its charge history
POST /refunds                    request a refund for a duplicate charge
GET  /refunds/{id}               refund status
```

Business rules that make the acceptance criteria interesting:
- Refund only if the same order has two charges within 24h for the same amount
- Refund window: 90 days from the later charge
- Never refund twice for the same duplicate — idempotent on `(order_id, charge_id)`
- Refunds above 500.00 require manual review rather than auto-issue

That last rule is the demo's punchline: it is a **business rule the LLM proposes
against and deterministic code enforces.** Ask for a 900.00 refund on stage and
watch it route to review rather than pay out.

### nokinc-demo-payments-sdk (TypeScript)

Typed client generated from the OpenAPI contract. `refundDuplicate()`,
`getRefund()`. Exists to prove polyglot gates and cross-repo ordering.

### nokinc-demo-infra (Terraform)

Container app or ECS service, Postgres, the feature flag. Exists so the ChangeSet
has an ordering constraint that actually matters: **infra before service before
SDK.**

### Declared spans (they become a gate):

```yaml
spans:   [refund.validate, refund.check_duplicate, refund.issue, refund.audit]
metrics: [refund_issued_total, refund_refused_total]
```

## Build order — vertical slices

Prove ONE repo end to end before adding horizontal plumbing. Multi-repo stays in
the MVP; it moves to Slice 3 so a multi-repo failure cannot block proving the
fundamental loop.

### Slice 1 — the loop works (MVP-A)

`nokinc-demo-payments` only.

| # | Story | Demo gain |
|---|---|---|
| 1 | GitHub `WorkItemPort` — issues, labels, comments | stories become issues |
| 2 | **ApprovalEvidence via GitHub environments** | status ≠ authorization |
| 3 | `factory gate <n> --approve` | gates are one command |
| 4 | Domain Expert agent + `factory chat` | the conversation |
| 5 | **Refusal behaviour** — no story until unknowns answered | **the credibility moment** |
| 6 | Architect agent → `SolutionReady` | design is generated |
| 7 | `assurance_wiring` + `span_topology` gates | assurance-by-construction |
| 8 | Dev deployment + real OTel traces in Jaeger | it actually runs |

**End of Slice 1: you have an MVP.** Everything after improves it.

### Slice 2 — the developer experience

| # | Story | Demo gain |
|---|---|---|
| 9 | Minimal deterministic RI — catalogue, tree-sitter symbols, references, OpenAPI, test inventory | *"which tests cover this?"* answered from evidence |
| 10 | Minimal read-mostly MCP — 8 tools | Copilot queries the factory, not GitHub search |
| 11 | `factory init` | one command to factory-enable a repo |

No embeddings. No vector store. No RAG. Deterministic retrieval only — enough to
establish the pattern.

### Slice 3 — multi-repo (MVP-B)

Rerun **the same story** across three repos.

| # | Story | Demo gain |
|---|---|---|
| 12 | `SimpleChangeSet` — worktrees, ordered PRs, sequential merge | **the cross-repo moment** |
| 13 | TypeScript and HCL toolchain adapters | polyglot gates, `NOT_AVAILABLE` visible |

### Slice 4 — the closing trace

| # | Story | Demo gain |
|---|---|---|
| 14 | Story id in commit trailer, image label, span attribute | the chain exists |
| 15 | `factory trace <n>` | **the closing shot** |
| 16 | Tier classifier wired into gates | T0 and T2 visibly differ |

## What makes this different from "just use Copilot"

Have the answer ready, because it is the first question you will be asked.

| | Copilot alone | This |
|---|---|---|
| Requirements | whatever was in the prompt | Definition of Ready, enforced, unknowns marked |
| Tests | written by the implementer | **frozen first, by a separate task, proven to fail** |
| Review | one model family | two families, structurally independent |
| Observability | if someone remembered | a gate — declared spans must exist |
| Traceability | commit message | sentence → issue → PR → commit → digest → span |
| Rules | in the prompt | deterministic code the model cannot talk past |
| Scope | one repo at a time | one ChangeSet, three repos, ordered merge |

## Payments is T2 — labels are lifecycle state, never authorization

The spec auto-promotes anything touching payments to **T2**, and T2 requires
verified identity, enforced separation of duties and an immutable audit trail.
A GitHub label gives none of those, and the PR author can apply it themselves.

Demonstrating the factory while breaking its own central rule would be noticed.

GitHub uses **two different mechanisms** because they prove different things:

1. A protected environment pauses `gate-approval.yml` and requires one independent reviewer. The workflow inputs bind the approval to the exact work item, gate and `sha256:` decision digest. The resulting workflow run is provider audit evidence.
2. Branch protection requires **two distinct approving PR reviews** before T2 target code can merge. GitHub environments cannot implement two-person approval by themselves: only one listed environment reviewer needs to approve.

```yaml
approval_evidence:
  work_item: 4711
  gate: G2
  solution_digest: sha256:...
  provider_workflow_run_id: ...
  environment: gate-2
```

Labels still carry **business state** (`stage:gate-2-approved`). They are applied only after the protected workflow is approved. Status therefore never substitutes for authorization.

The fictional demo targets are deliberately **public** so required environment reviewers are available on all current GitHub plans. The factory repository may remain private; private branch protection then requires a GitHub plan that supports it.

## Demo failure modes to rehearse

- **The agent guesses instead of asking.** Rehearse a vague prompt and confirm it
  asks. If it guesses, the demo's best moment is gone.
- **Copilot takes longer than you expect.** Have a pre-run PR ready to cut to.
- **The 900.00 refund pays out.** Test this specific case before you present.
  It is the punchline and it must land.
- **Spans missing.** Run `factory trace` beforehand and confirm 3/3.
