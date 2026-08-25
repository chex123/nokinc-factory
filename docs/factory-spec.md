# Software Factory — Implementation Specification v2.5

**Status: CLOSED.** Supersedes v2.4. Change log at the end.

v2.5 adds the Retrieval Planner, TaskContext, the governed model cascade with a qualification matrix, and independent reviewer retrieval. No architectural boundary changed.

---

# Part 1 — State, Approval and Reconciliation

## Three kinds of state

**The ALM work item owns the business lifecycle. The workflow engine owns execution. The approval system owns authorization evidence.** Different things, different stores — but the *business lifecycle* exists in exactly one place.

| System | Authoritative for |
|---|---|
| **ALM work item** (ADO, Jira, GitHub Issues) | Requested change, business lifecycle state, visible status, human history |
| **Workflow engine** | Execution attempts, agent task state, retries, leases, artifacts, tool calls, test evidence |
| **Approval system** | Who authorized what, with verified identity and immutable audit |

A human gate is a person performing an approval, which moves the work item. The factory observes the transition and acts.

## Transitions are compare-and-swap

```json
{
  "work_item_id": "4711",
  "workflow_run_id": "wf_9a3c...",
  "transition_id": "t_0007",
  "expected_current_state": "SOLUTION_READY",
  "target_state": "IMPLEMENTING",
  "event_id": "evt_...",
  "timestamp": "...",
  "actor": "..."
}
```

A duplicate webhook fails the comparison and is dropped.

## Inbox / outbox / reconciler

CAS handles duplicates. It does not handle lost webhooks, or "the ALM update succeeded but our local write failed." There is no distributed transaction across a SaaS ALM, your database and your workflow engine.

```
Provider webhook
      ↓
   INBOX            dedupe on event_id, persist before processing
      ↓
Workflow transaction     (local, atomic)
      ↓
   OUTBOX           intent to update the provider, same transaction
      ↓
Provider update          retried until acknowledged
```

Plus a periodic **reconciler**: for every non-terminal work item, does ALM business state agree with the factory's expected state?

### Conflict policy

| Divergence | Resolution |
|---|---|
| ALM ≠ factory, **business lifecycle** | **ALM wins.** Authoritative by design. |
| Execution state disagreement | **Workflow engine wins.** ALM is a projection. |
| Approval in ALM, no matching factory evidence | **Human queue.** Never auto-resolve an authorization. |
| Factory believes approved, ALM shows none | **Halt.** Treat as a security event. |

**Track divergence rate.** A reconciler constantly fixing things is reporting a defect, not doing its job.

## Approval is not a status change

```
ApprovalPort
  ├── Azure DevOps  → environment approvals and checks (can block self-approval)
  ├── GitHub        → environment protection rules, required reviewers (can prevent self-review)
  ├── Jira          → governed workflow transition with permission scheme
  └── GitHub Issues → label only — NOT sufficient for T2
```

```yaml
approval_capabilities:
  verified_identity: true
  separation_of_duties: true      # approver ≠ author, enforced by the provider
  immutable_audit: true
  time_bound: false
```

```yaml
T2_requires:
  verified_identity: true
  separation_of_duties: true
  immutable_audit: true
```

**Fail closed, from day one.** Retrofitting "actually T2 can't run here" after teams have shipped on a label-based adapter is a conversation you do not want.

---

# Part 2 — Repository Intelligence

## Two views of one engine

The engine is shared with the assurance plane. **The views are not.**

```
                    REPOSITORY INTELLIGENCE ENGINE
                                │
             ┌──────────────────┴──────────────────┐
             ▼                                     ▼
      DEVELOPMENT VIEW                     CODE MODEL SNAPSHOT
      mutable                              immutable
      branch and PR aware                  content-addressed
      continuously reindexed               bound to a specific build
             │                                     │
             ▼                                     ▼
      SOFTWARE FACTORY                     ASSURANCE PLANE
      "what breaks if I change this?"      "what was this built to do?"
```

Without the split:

```
image A deployed Monday → source changes Tuesday → RI reindexes
   → assurance asks "what was image A built to do?"
      → receives Tuesday's model            ← WRONG, and confidently so
```

Build-derived intent is meaningless unless bound to the artifact actually running.

## CodeModelSnapshot

```yaml
code_model_snapshot:
  snapshot_id: sha256:...          # content address of the model itself
  git_sha: a1b2c3...
  build_id: 28473
  image_digest: sha256:...
  build_config_digest: sha256:...  # build-time defaults ONLY — not runtime config
  extractor_versions:
    typescript: 4.2.1
    java: 3.9.0
    terraform: 2.1.4
    openapi: 1.8.0
  generated_at: 2026-08-24T09:14:02Z
  generated_by: build-pipeline     # NOT a separate indexing job
```

**Runtime configuration is deliberately absent.** The snapshot describes what the build *contains*. Runtime configuration is deployment identity and lives in the DeploymentBinding (Part 12). Conflating them was a defect in v2.1.

**Two rules easy to get wrong:**

1. **The snapshot is a build artifact produced by the build pipeline**, signed alongside SBOM and provenance. A separate indexing job that "runs at build time" is a race condition, not a binding.
2. **Extractor versions are a supply-chain concern.** Upgrading a parser changes what build-derived intent means for every subsequent build. Pin them, list them in the capability manifest, treat an upgrade as a reviewed change.

## What RI answers, and how

Deterministic first. Retrieval only for prose.

| Question | Answered by |
|---|---|
| What consumes `CustomerStatus`? | AST + call graph — **deterministic** |
| What breaks if this API changes? | Contract graph — **deterministic** |
| Which repo owns this table? | Schema ownership map — **deterministic** |
| Which tests exercise this path? | Coverage map — **deterministic** |
| What Terraform deploys this? | IaC graph — **deterministic** |
| **Is this change security-sensitive?** | **Module registry + path patterns — deterministic** |
| Why did we choose this pattern? | ADR retrieval — **RAG** |

Agents do not "remember the repo." They **query this subsystem**.

## Security-sensitive module registry

Required by impact classification (Part 3) and by risk tiering (Part 4). Declared, version-controlled, reviewed like code.

```yaml
security_sensitive:
  paths:
    - "**/auth/**"
    - "**/authz/**"
    - "**/crypto/**"
    - "**/payments/**"
    - "**/pii/**"
    - "**/migrations/**"
  patterns:
    - authorization_predicate      # ownership / permission checks
    - sql_construction             # dynamic query building
    - transaction_boundary
    - secret_access
    - outbound_network_call
    - serialization_deserialization
    - concurrency_primitive
  annotations:
    - "@SecuritySensitive"         # in-code marker, honoured by the classifier
```

**Fail closed:** an unrecognised module, new file type or unmatched pattern classifies as *potentially sensitive* and promotes the change. Never default to "safe."

## Trust boundary between factory and assurance

Sharing the engine must not mean sharing production evidence. A coding agent must never acquire fleet-wide access to production command lines, SQL, network evidence or model prompts.

```
ASSURANCE RAW EVIDENCE            ← privileged, restricted, encrypted
        │
        ▼
Assurance deterministic queries   ← inside the assurance trust domain
        │
        ▼
   RuntimeFactsPort               ← SANITIZATION HAPPENS HERE, on this side
        │
        ▼
Repository Intelligence → Factory agents
```

Sanitization happens inside the assurance domain, before the boundary is crossed. Same principle as redacting at instrumentation rather than at the collector.

```yaml
runtime_facts_capabilities:
  endpoint_call_frequency: {available: true,  granularity: hourly}
  error_rates_by_endpoint: {available: true,  granularity: hourly}
  observed_dependencies:   {available: true,  granularity: service}
  slow_query_shapes:       {available: true,  redaction: literals_stripped}
  raw_sql:                 {available: false}
  process_ancestry:        {available: false}
  network_destinations:    {available: true,  granularity: hostname_only}
  model_prompts:           {available: false}
```

Two flows, two trust levels: assurance **pulls** CodeModelSnapshots from RI; the factory **pulls** sanitized runtime facts from assurance.

## Retrieval architecture — the codebase never enters the context window

**Repository Intelligence knows the system. The model receives the relevant slice for the current task.**

This holds regardless of context window size. A million-token window is a larger budget, not a different architecture. An experienced engineer does not memorise four million lines before changing one feature; they find the entry point, trace dependencies, inspect the contract and tests, change, compile, follow failures outward. The agent works the same way.

```
                   ENTIRE SOFTWARE ESTATE
                              │
                              ▼
                   REPOSITORY INTELLIGENCE
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
     Code graph            Search              Embeddings
   deterministic       lexical/symbol           semantic
          └───────────────────┼───────────────────┘
                              ▼
                      CONTEXT BUILDER
                              ▼
                  small, task-specific context pack
                              ▼
                             LLM
                              ▼
                    tool calls for more, on demand
```

### Three retrieval mechanisms, in priority order

**A. Deterministic code retrieval — primary.** Symbol lookup, definitions, references, imports, call relationships, OpenAPI consumers, event publishers and consumers, database ownership, IaC ownership, tests touching code.

*"Where is `processRefund()` called?"* is a reference-graph traversal. Never an embedding search.

**B. Lexical and structural search.** When you approximately know the token: `CustomerStatus`, `refund.requested`, `/v2/payments`. Regex and symbol-aware search. Still deterministic.

**C. Embeddings and RAG — secondary, and mostly for prose.**

Primary use: ADRs, architecture documents, requirements, historical incident reports, READMEs, product documentation, design discussions. *"Why did we choose eventual consistency for refund events?"* has no symbol.

Legitimate secondary use — semantic code discovery when the name is unknown: *"where does the system validate that a customer owns an order?"*

**The rule:**

> **Embeddings discover. Structured code intelligence verifies.**

```
semantic discovery → candidate symbols → AST / reference verification → actual context
```

Never hand an embedding hit to an agent as fact.

### The Retrieval Planner

Do not expose AST, lexical and semantic retrieval to agents as three tools and hope they choose well. **The agent states what it needs to know; Repository Intelligence decides how that question is best established.**

```
Agent: "what do I need to know?"
              ▼
     RETRIEVAL PLANNER
              │
   ┌──────────┼──────────┐
   ▼          ▼          ▼
AST / LSP  lexical    semantic
   │          │          │
   └──────────┼──────────┘
              ▼
           rerank
              ▼
       graph expansion
              ▼
      repository facts
```

Routing is by question type, and it is deterministic:

| Question | Route |
|---|---|
| *Who calls `issueRefund()`?* | LSP / AST references. **Never embeddings.** |
| *Who consumes `refund.completed`?* | Event and contract graph |
| *What breaks if this schema changes?* | Dependency graph traversal |
| *Why did we make refunds asynchronous?* | ADR and document RAG |
| *Where does this monolith decide refund eligibility?* | Lexical + semantic → candidate symbols → **AST verification** |

The last row is the hybrid case and the only one where embeddings lead. They produce candidates; the symbol table establishes the relationship.

> **Semantic retrieval proposes relevance. Structured repository evidence establishes relationships.**

### Context Builder — a distinct component from Repository Intelligence

RI is **persistent system knowledge**. The Context Builder is a **task-specific context compiler**. Conflating them is how you end up stuffing repositories into prompts.

```
Workflow
   ▼
Context Builder ◄──── Repository Intelligence (via Retrieval Planner)
   ▼
ModelPort
   ▼
Agent
```

Its job, in order: include authoritative context; request relevant RI evidence; rank it; fit the model's usable context budget; permit on-demand expansion.

**A 1M-token context window is headroom, not a repository database.**

### TaskContext and ContextPack are different artefacts

**TaskContext** is the authoritative input set — what the work *is*. Identical for every agent on the task, including reviewers.

```yaml
task_context:
  work_item: STORY-428
  original_request_digest: sha256:...
  business_ready_digest: sha256:...
  solution_ready_digest: sha256:...
  changeset_version: 12
  repository_snapshots:
    payments-api: sha256:...
    payments-sdk: sha256:...
  acceptance_suite_digest: sha256:...
```

**ContextPack** is one agent's retrieved slice — what that agent actually saw. Different per agent, per run.

```yaml
context_pack:
  id: cp_8237
  task_context: <digest>
  agent_role: architect
  model_resolution: {provider: ..., model: ..., tier: M3}
  budget_tokens: 180000
  selected_symbols: [...]
  selected_contracts: [...]
  selected_docs: [...]
  runtime_fact_refs: [...]
  retrieval_queries: [...]
  token_count: 41_200
  expansions_during_run: 6
```

**Why the split matters diagnostically.** When an Architect misses something, these two artefacts tell you which defect you have:

| Evidence present in TaskContext? | In the agent's ContextPack? | Diagnosis |
|---|---|---|
| Yes | Yes | The model reasoned badly |
| Yes | No | **The Context Builder never supplied it** |
| No | No | The upstream story or design was incomplete |

Those are three different fixes. Without the split you cannot tell them apart, and you will tune prompts to solve retrieval bugs.

**Metric: retrieval miss rate** — how often a review finding cites evidence the producer's ContextPack did not contain. It measures Context Builder quality directly.

### Context packs are recorded and content-addressed

```yaml
context_pack:
  pack_id: sha256:...
  budget_tokens: 180000          # derived from ModelPort capability
  story: 4711
  changeset_version: 7
  contents:
    - {kind: story, ref: "..."}
    - {kind: acceptance_criteria, ref: "..."}
    - {kind: adr, ref: "ADR-027"}
    - {kind: symbol, ref: "payments-api:RefundService#issue"}
    - {kind: contract, ref: "refund-api:v2"}
    - {kind: schema, ref: "refund_transactions"}
    - {kind: test, ref: "acceptance/refund_duplicate.feature"}
  retrieval_method:
    deterministic: 14
    lexical: 3
    semantic: 2
  expansions_during_run: 6       # tool calls that pulled more context
```

**Recording is not optional.** If eval results vary because the context pack varied and you did not record it, `pass^k` measures nothing. Same reasoning as recording the resolved model (Part 11). The pack ID goes in the run record alongside the resolved model identifier.

`ContextBudget = model.max_context` is derived from `ModelPort`, so a model swap adjusts retrieval depth automatically rather than silently truncating.

### Capability slice — the strangler query

For legacy work, RI produces a **capability slice**: everything that participates in one business capability, across repos, plus what it could not determine.

```yaml
capability_slice: refund
repos: 4
source_files: 37
tables: [refunds, payments, transactions]
external_systems: [payment_gateway, notification_service]
events: [refund.requested, refund.completed]
runtime_calls:                    # from RuntimeFactsPort
  - api → payment_gateway
  - worker → notifications
tests: 21
unknown:
  - "quarterly reconciliation path — no static caller, no observed trace"
```

**The `unknown` list is the most important field.** It is the same fail-closed principle as `NOT_ASSESSABLE`: a capability slice that silently omits what it could not find produces a confident, incomplete extraction plan. Unknowns are resolved explicitly at Gate 1.

---

# Part 3 — ChangeSet

```
WorkItem 4711
    │
    ▼
ChangeSet cs_881 (v7)
    ├── infrastructure      PR 61   order 0
    ├── sdk-typescript      PR 92   order 1
    ├── payment-service     PR 338  order 2
    └── frontend-web        PR 147  order 3
```

```yaml
changeset: cs_881
version: 7
work_item: 4711
state: PREPARED          # PREPARED | MERGING | MERGED | PARTIALLY_MERGED | ABANDONED

repos:
  - {name: infrastructure,  base_sha: 0789..., pr: 61,  order: 0}
  - {name: sdk-typescript,  base_sha: a1b2..., pr: 92,  order: 1}
  - {name: payment-service, base_sha: c3d4..., pr: 338, order: 2}
  - {name: frontend-web,    base_sha: e5f6..., pr: 147, order: 3}

contract_versions:
  refund_api: {from: v1, to: v2, overlap_required: true}

deploy_order:   [infrastructure, sdk, backend, frontend]
rollback_order: [frontend, backend, sdk, infrastructure]
feature_flags:  [refund_duplicate_detection_v2]

evidence:
  gate_2_approval:  {changeset_version: 7, at: "...", by: "..."}
  acceptance_tests: {changeset_version: 7, frozen_at: "..."}
  contract_tests:   {changeset_version: 7, frozen_at: "..."}
  security_review:  {changeset_version: 5, at: "..."}     # ← stale
```

## Rollback ordering and contract overlap

If the frontend has shipped and the backend must roll back, you have a partial deployment. Only safe if **contract versions overlap** — the backend supports v1 and v2 simultaneously.

```
SDK v2 publishes → backend supports v1 AND v2 (overlap window)
   → frontend moves to v2 → v1 retired later, as a separate change
```

**A ChangeSet that cannot be partially rolled back safely must be flagged at Gate 2** — restructured into overlapping contracts, or explicitly accepted as all-or-nothing with a named risk owner.

## Evidence invalidation is impact-based, not surface-based

Any material mutation creates a new ChangeSet version and invalidates dependent evidence. **Invalidation is decided by deterministic impact classification, not by whether the public interface changed.**

A change with no surface change can still alter authorization, cryptography, SQL construction, transaction boundaries, concurrency, PII handling, egress or secret access:

```diff
- if (user.id == order.owner)
-     refund()
+ refund()
```

No contract change. Obviously invalidates security review.

```
Implementation diff
      ↓
RI deterministic impact classification
      ├── touches security-sensitive module or pattern?
      ├── authorization or authentication logic?
      ├── data-flow or PII handling change?
      ├── persistence or transaction semantics?
      ├── concurrency primitive?
      ├── new dependency?
      ├── approved-intent impact (new egress, data store, capability)?
      └── critical-domain module?
```

| Impact class | Invalidates |
|---|---|
| Interface surface changed | Contract tests, Gate 2, security review |
| **Security-sensitive code touched** | **Security review, Gate 2 for T2, all affected tests** |
| **Authorization or authn logic changed** | **Security review, Gate 2 — always, regardless of tier** |
| **Data-flow or PII handling changed** | **Security review, privacy review, Gate 2** |
| Persistence or transaction semantics changed | Gate 2, rollback strategy, security review |
| New outbound destination or data store | Gate 2, security review, approved-intent delta |
| Database migration changed | Gate 2, rollback strategy, security review |
| IaC changed | Gate 2, policy gate |
| New repository added to the ChangeSet | Gate 2, deploy and rollback ordering |
| New dependency added | Licence scan, SCA, SBOM, Gate 2 if transitively security-sensitive |
| Ordinary implementation change, no sensitive impact | Affected test runs only |
| Comment, formatting or docstring only | Nothing |
| **Impact cannot be classified** | **Everything — fail closed, promote to human review** |

The classifier is **deterministic** — registry lookup and pattern matching in RI. It is not an LLM judging "does this look security-relevant?" That would reintroduce the problem one level up.

## Merge-candidate revalidation

Immediately before merge, against the **exact candidate merge SHAs** — not the branch heads that were tested:

```
resolve exact candidate merge SHAs
   → RI impact analysis on the merged result
   → contract compatibility check
   → tests and security gates re-run
   → ChangeSet internal consistency (ordering, overlaps, flags)
   → merge
```

Use provider merge queues where available.

## Multi-repo merge is not atomic

Repositories cannot generally be merged in one transaction. Model it:

```
PREPARED
   ↓
MERGING
   ↓
   ├── MERGED
   │
   └── PARTIALLY_MERGED
             ↓
        resolution policy
             ├── roll forward — complete remaining merges
             └── roll back — revert merged PRs
```

**A partial merge must never equal a release.** Eligibility for a ReleaseBundle requires:

```
all required PRs merged
+ exact digests built for every artifact
+ ChangeSet consistency verified
```

`PARTIALLY_MERGED` is survivable precisely because of the contract-overlap and feature-flag design. The resolution policy must be declared in the ChangeSet, not decided during the incident:

```yaml
partial_merge_policy: roll_forward   # roll_forward | roll_back
max_partial_duration_minutes: 30
on_timeout: escalate
```

---

# Part 4 — Flow, Gates and Risk Tiering

```
   Human chats with Domain Expert (backed by RI)
            ▼
      BUSINESS READY
      ┌─────────────┐
      │  GATE 1     │  "Do we understand what needs to be done?"
      └─────────────┘
            ▼
   Architect designs using RI → ChangeSet v1
            ▼
      SOLUTION READY
      ┌─────────────┐
      │  GATE 2     │  "Do we approve this solution? Build it."
      └─────────────┘
            ▼
   Test Author freezes acceptance + contract tests
   Implementer builds with TDD inner loop
   Deterministic gates → Judge → verification policy → PRs
   Merge-candidate revalidation → merge → DEV → tests fire
      ┌─────────────┐
      │  GATE 3     │  Pre-prod entry (configurable)
      └─────────────┘
            ▼
   Pre-prod: risk-based suite + assurance verification
      ┌─────────────┐
      │  GATE 4     │  "Release to production?"
      └─────────────┘
            ▼
   Canary → full
```

## States

```
NEW → REFINING → BUSINESS_READY ──[G1]──> DESIGNING → SOLUTION_READY ──[G2]──>
IMPLEMENTING → DEV_VERIFYING → DEV_VERIFIED ──[G3]──>
PREPROD_VERIFYING → PREPROD_VERIFIED ──[G4]──> RELEASING → DONE
```

Plus `BLOCKED`, `REJECTED`, `ROLLED_BACK`.

## Gate 3 earns its own removal

```yaml
gate_3:
  mode: human            # human | auto
  auto_promote_when:
    first_pass_gate_rate: "> 0.85 over 20 consecutive stories"
    human_edit_distance:  "< 0.10 over 20 consecutive stories"
    judge_override_rate:  "< 0.05"
```

## Risk tier is derived from impact, not from category

**A category label is not a risk assessment.** `AUTHORIZATION_ENABLED=false` is a config change. An auth library major upgrade is a dependency bump. Both would be T0 under a naive category table, and both are dangerous.

**T0 is a set of pre-approved constrained change classes, each defined by predicates:**

```yaml
T0_dependency_update:
  only_if:
    - version_change: patch          # not minor, not major
    - public_api_diff: none
    - no_new_transitive_dependencies
    - no_new_licence_risk
    - no_open_critical_or_high_cve
    - not_security_sensitive_module   # per the RI registry
    - deterministic_tests: pass

T0_config:
  only_fields:
    - UI_COPY
    - LOG_LEVEL
    - SAFE_TIMEOUT_RANGE              # bounded numeric range, declared
  never_fields:
    - "*AUTH*"
    - "*SECRET*"
    - "*KEY*"
    - "*ENDPOINT*"
    - "*FEATURE_ENABLED*"

T0_cosmetic:
  only_if:
    - impact_class: [comment, formatting, docstring]
```

**Automatic promotion out of T0**, regardless of category, if the change touches:

```
auth · authz · network egress · secrets · payments · PII
data retention · database behaviour · cryptography · security policy
```

**Fail closed:** a change the classifier cannot categorise is **not** T0. Unknown means T1 minimum.

| Tier | Definition | Gates | Approval |
|---|---|---|---|
| **T0** | Matches a declared constrained change class | PR approval only | Any engineer |
| **T1** | Everything not T0 or T2 | G1, G2, G3*, G4 | Product owner + tech lead |
| **T2** | Touches the auto-promotion list, or Architect designates | All, plus architecture and security review | Two people, verified identity, separation of duties |

**Watch approval volume per person.** Above roughly ten a day they have stopped reading. A rising number is a defect in your tiering, not a productivity win.

---

# Part 5 — Business Ready (Gate 1)

Answers: **do we understand what needs to be accomplished?** No architecture required.

| Required | Notes |
|---|---|
| **Problem and value** | Who has the problem, what it costs, what changes when this ships |
| **Scope — in and out** | Explicit "out" list. Most effective control on agent scope creep. |
| **BDD examples** | Gherkin scenarios. **At least one failure or edge case is mandatory.** |
| **Business rules** | Conditions, limits, eligibility, thresholds — in business language |
| **Test data needs** | What data proves the behaviour. Not yet how it is generated. |
| **NFR / SLO impact** | A number, or an explicit "no change" |
| **Security and data classification** | PII, payment, health, or none |
| **Known constraints** | Legacy dependency, third-party API, regulatory deadline |
| **Preliminary risk tier** | Agent recommends, human confirms |
| **Rough size and confidence** | T-shirt size from RI. Prevents Gate 1 approving a fantasy. |

```gherkin
Scenario: Customer requests refund for a genuine duplicate charge
  Given order 8827 was charged twice on 2026-08-18
  And the customer is authenticated as the order owner
  When they request a refund for the duplicate
  Then a refund of 49.99 GBP is issued against the second charge
  And an audit record links the refund to the original charge

Scenario: Refund is refused when the charge was not duplicated
  Given order 8830 was charged once
  When a duplicate-charge refund is requested
  Then the request is refused with reason NOT_A_DUPLICATE
  And no refund is issued
```

**The Domain Expert must ask rather than assume.** An assumption here becomes an acceptance criterion, then a test, then code. It is instructed to say "I don't know — you need to tell me" and mark unknowns rather than resolve them.

---

# Part 6 — Solution Ready (Gate 2)

Answers: **do we approve this solution and authorize implementation?**

| Required | Notes |
|---|---|
| **Affected repositories** | From the dependency graph, not from memory |
| **Service boundary decision** | New service, extend existing, or which existing |
| **API / event contracts** | Full schema delta, versioning, overlap window |
| **Database changes** | Expand/migrate/contract plan |
| **IaC changes** | What infrastructure moves |
| **Security design** | Authz changes, secrets, data flow |
| **Observability spec** | Declared spans and metrics |
| **Approved-intent delta** | New outbound destinations, data stores, capabilities |
| **Containment contract delta** | Leader/quorum/lease constraints, min healthy instances |
| **Rollback / fix-forward strategy** | See below |
| **Deployment impact** | Ordering, downtime, feature flags |
| **Final risk tier** | Confirmed, derived from impact |
| **ChangeSet** | Repos, ordering, contract overlaps, partial-merge policy |
| **ADR** | If an architectural decision was made |

## Observability and approved-intent make outputs assurance-designed

```yaml
observability:
  spans:   [refund.validate, refund.issue, refund.audit]
  metrics: [refund_issued_total, refund_refused_total{reason}]

approved_intent_delta:
  new_outbound_destinations: [payments-gateway.internal]
  new_data_stores: []
  new_capabilities: []

containment_delta:
  min_healthy_instances: unchanged
  isolate_leader: false
```

The service cannot be built without declaring its telemetry and intended behaviour. Empty is fine; **empty-because-checked** differs from **empty-because-forgotten**, so state it.

## Rollback / fix-forward

Production database evolution is expand → deploy compatible code → migrate → contract later. A destructive backward migration is frequently more dangerous than fixing forward, because it discards data written in the interim.

```yaml
rollback_strategy: fix_forward   # fix_forward | feature_flag | revert | backward_migration
justification: "Additive column only; rollback would drop customer-entered data"
feature_flag: refund_duplicate_detection_v2
data_written_during_rollout: "retained; ignored by the v1 code path"
```

## Definition of Done — checked at Gate 4

- Every Gherkin scenario passes in pre-prod
- All deterministic gates green against the **merged** candidate
- **Verification policy decision: PASS** (see Part 7 — the Judge is an input, not the gate)
- **Declared spans actually observed** in pre-prod telemetry
- Approved-intent manifest updated and approved
- **Rollback or fix-forward path exercised**, not assumed
- ADR written where applicable
- Cost of the story recorded

---

# Part 7 — The Agents

Five. Fewer breaks a separation that matters; more adds coordination cost without quality.

## 1. Domain Expert

Chat interface, backed by RI so answers are grounded rather than recalled.

- **Greenfield:** product context, service catalogue, house patterns
- **Legacy:** queries the knowledge graph — code structure, schema, observed runtime behaviour (via `RuntimeFactsPort`), existing API surface

**Output:** Business Ready story. **Never architecture.**

## 2. Architect

**Input:** Business Ready story, plus RI.
**Output:** Solution Ready package and ChangeSet v1.
**Skipped for T0.**

## 3. Test Author — independent

**Sees:** baseline source, existing tests, existing contracts and APIs, current behaviour, approved architecture, Repository Intelligence.

**Does not see:** the **candidate implementation** for this change — the Implementer's diff, its reasoning, or tests it adds afterwards — until independent test creation is frozen.

*(The independence required is from the candidate, not from the baseline. An author blind to the existing codebase cannot write grounded regression tests.)*

**Owns and freezes:** BDD acceptance tests **with working step bindings** · contract tests for every changed external contract · adversarial and negative cases · test data fixtures and generators · regression tests for reported bugs.

**Does not own:** implementation-level unit tests. It cannot predict internal structure.

## 4. Implementer

**Inner loop — TDD:** unit test → minimum implementation → pass → refactor → repeat.

**May** add unit tests freely.
**May not** alter approved Gherkin, acceptance tests, contract tests or independent regression tests. An attempt is a hard gate failure escalated to a human.

**Bounded:** maximum 3 repair attempts. Same failure signature twice → escalate rather than burn attempt three.

## 5. Judge — a specialization of the Cross-Model Review capability

The Judge is not a bespoke agent. It is the Cross-Model Review capability (below) applied to implementation artefacts.

**Sees:** story, acceptance criteria, tests, final diff, gate results.
**Does not see:** the Implementer's reasoning, self-assessment or intermediate attempts.
**Uses a different model family** where available, reducing correlated blind spots.

```json
{
  "assessment": "PASS | CONCERNS | FAIL",
  "criteria": [
    {"id": "AC-1", "result": "PASS", "evidence": "test_refund_duplicate:42"},
    {"id": "AC-2", "result": "CONCERNS",
     "note": "Passes, but asserts only the status code, not the refunded amount"}
  ],
  "simpler_alternative": "...",
  "risks_not_covered": ["..."]
}
```

## The verification policy — this is the gate

The Judge produces an **assessment**. A deterministic policy produces the **decision**.

```
Judge → structured assessment
             ↓
   DETERMINISTIC VERIFICATION POLICY
             ↓
   PASS / ESCALATE / FAIL
```

```yaml
verification_policy:
  version: 4
  on_judge_assessment:
    FAIL:     {action: block, return_to: IMPLEMENTING}
    CONCERNS: {action: human_review}
    PASS:     {action: continue}
  overrides:
    - if: {tier: T2}
      then: {action: human_review}          # T2 never auto-passes on Judge alone
    - if: {impact_class: [authz_changed, pii_dataflow_changed]}
      then: {action: human_review}
    - if: {judge_unavailable: true}
      then: {action: human_review}          # fail closed, never fail open
```

**Invariant: an LLM cannot approve its own conclusion.** This is the same analysis / policy / actuation separation as the assurance plane, and it is why Definition of Done says *verification policy decision*, never *Judge verdict*.

## Cross-Model Review — a reusable capability, not more agents

High-value semantic outputs should not be presented as final by the model that produced them. But the answer is **not** five reviewer agents. It is one governed capability any producer can call.

```
                     TASK CONTEXT
                    /            \
                   ▼              ▼
            PRODUCER (Model A)  REVIEWER (Model B)
                   │              ▲
                   └── artifact ──┘
                                  │
                       structured findings
                                  ▼
                        PRODUCER (Model A)
                       reconcile finding-by-finding
                                  ▼
                            ARTIFACT v2
                                  ▼
                     DETERMINISTIC POLICY
                          /          \
                       PASS        unresolved
                         ▼              ▼
                    next stage        HUMAN
```

### What the reviewer receives — and does not

**Receives:** the shared **TaskContext** — original ask, Business Ready story, acceptance criteria, approved architecture constraints, repository snapshots — plus the produced artefact.

**Does not receive:** the producer's chain of reasoning, self-evaluation, or earlier failed attempts.

Both halves matter. Without the original ask, the reviewer optimises the wrong objective — it reviews the artefact against itself. With the producer's reasoning, it inherits the producer's framing and independence collapses.

### The reviewer builds its own ContextPack — mandatory

**Do not hand Reviewer B the ContextPack Producer A used.**

```
            TASK CONTEXT  (shared, authoritative)
              /                    \
             ▼                      ▼
   Producer Context Builder   Reviewer Context Builder
             ▼                      ▼
        Producer A              Reviewer B
```

If they share a retrieved slice, **a bad producer retrieval becomes a shared blind spot** — the reviewer cannot notice evidence that neither of them was given, and the entire point of independent review evaporates. The most common architecture defect is missing context, not faulty reasoning, so sharing the pack blinds the review to the most likely failure.

They share what the work *is*. They independently establish what is *relevant to* it.

**Cost:** this roughly doubles retrieval work for reviewed artefacts. Tie it to risk tier, exactly like the review itself.

### Model independence

```yaml
review_requirement:
  independence:
    different_model_family: preferred     # required for T2
    different_provider: preferred
```

Not because Model B is better. Because identical model, prompt architecture and context interpretation produce **correlated blind spots**. `ModelPort` resolves an independent reviewer from the capability profile.

### Structured findings, not essays

```json
{
  "verdict": "ACCEPT | CHANGES_REQUIRED | ESCALATE",
  "findings": [
    {
      "id": "AR-001",
      "severity": "HIGH",
      "category": "data_consistency",
      "claim": "Rollback ordering is unsafe.",
      "evidence": ["refund-api supports only v2 after deployment"],
      "recommendation": "Maintain v1/v2 overlap until frontend migration completes."
    }
  ]
}
```

### Producer reconciles finding-by-finding

**The reviewer can be wrong.** Forcing blanket acceptance makes the reviewer an unaccountable authority.

```json
{
  "review_resolution": [
    {"finding": "AR-001", "resolution": "ACCEPTED",
     "change": "Added v1/v2 compatibility window."},
    {"finding": "AR-002", "resolution": "REJECTED",
     "reason": "Reviewer inferred synchronous processing; the event contract is asynchronous.",
     "evidence": "refund-events.yaml:37"}
  ]
}
```

### Policy governs unresolved findings

A producer must not be able to silently dismiss a serious finding.

```yaml
review_policy:
  version: 2
  LOW:      {unresolved: producer_may_reject_with_evidence}
  MEDIUM:   {unresolved: human_review}
  HIGH:     {unresolved: human_review}
  CRITICAL: {unresolved: block}
  rounds:
    default: 1
    T2: 2
    on_exceeded: escalate_to_human
```

**Bound the loop.** Producer → reviewer → producer → reviewer can ping-pong indefinitely. One round by default, two for T2, then a human decides.

### Where it applies — tied to risk tier, because it roughly doubles inference cost

| Always | Usually | Never |
|---|---|---|
| Solution / architecture design | Business Ready story for T2 | Lint fix |
| T2 security design | Complex ChangeSet | Dependency patch |
| Database migration plan | Acceptance-test design | Documentation |
| Strangler extraction boundary | | Formatting |
| Approved-intent change | | Boilerplate |
| Public API breaking change | | |
| Production remediation plan | | |

### Health metrics — a reviewer can fail in two directions

| Metric | Failure it detects |
|---|---|
| **Reviewer acceptance rate ≈ 100%** | Rubber stamp — cost without value |
| **Producer rejection rate high** | Either a bad reviewer or a defensive producer. Both are defects. |
| Findings later confirmed by gates or production | Whether reviews catch real things |

Same invariant as everywhere else: **LLMs reason. Policy governs.**

## UX is a capability, not an agent

When a story carries `ux_impact: true`, the Architect, Implementer and Judge each load the UI/UX skill. No sixth agent.

## Keep these as code

Gate runner · release pipeline · scaffolder · verification policy engine · RI indexers · impact classifier · reconciler. These are rules, and rules should be code.

---

# Part 8 — Testing

## The invariant

> Every acceptance criterion has executable acceptance coverage before implementation begins.
> Every changed external contract has contract coverage.
> Every critical business rule has an explicit test.

Enforceable, and not gameable by adding trivial methods. Test-first is preserved where it matters — behaviour and contracts — while unit tests follow implementation structure inside the TDD loop.

## Two loops

```
   OUTER — BDD (frozen before implementation, by an independent author)
   Gherkin scenario fails
        ▼
   ┌─────────────────────────┐
   │   INNER — TDD           │
   │   unit test fails       │
   │   → minimum code        │
   │   → passes → refactor   │
   └─────────────────────────┘
        ▼
   Gherkin scenario passes
```

Why BDD at the story level matters for an agent factory:

1. A product owner can read a Gherkin scenario and approve or reject it. They cannot review a unit test.
2. It is machine-executable, so acceptance criteria **are** the gate. No translation step, no drift.
3. Written by a different agent than the one implementing. It cannot be quietly weakened.

## Gherkin is not automatically executable

The Test Author writes step bindings and fixtures. The gate rejects:

```
undefined steps · pending or skipped steps
ambiguous step definitions · scenarios with no meaningful assertion
```

## Baseline-failure gates are scoped by story type

Running the frozen suite against the unmodified baseline proves the scenarios test something new. But a **behaviour-preserving refactor has no new scenarios that should fail**, so an unconditional gate makes refactoring stories unsatisfiable.

```yaml
when_new_behaviour:
  - acceptance_fails_on_baseline        # new scenarios MUST fail before implementation
when_bug_fix:
  - regression_test_fails_on_baseline   # the bug must be reproducible
when_refactor:
  - acceptance_passes_on_baseline       # inverse: behaviour must be unchanged
  - characterization_suite_unchanged
when_infrastructure_only:
  - (not applicable)
```

The refactor case is the inverse assertion and equally valuable: the suite must pass *before and after*, unchanged.

## Gates the agent cannot game

| Gate | Catches | Scope |
|---|---|---|
| **Diff coverage** (changed lines + branches) | Untested new code | Every PR |
| **Targeted mutation** | Tests that execute but do not assert | Diff and risk-sensitive modules |
| **Full mutation suite** | Deeper gaps | T2, or nightly |
| **Revert-and-fail** | Vacuous tests | Behaviour changes only |
| **Baseline-failure (scoped)** | Scenarios asserting nothing new | Per story type, above |
| **Acceptance tests immutable to Implementer** | Weakening the contract to fit the code | Always |

**Diff coverage, not `coverage_delta >= 0`.** Overall percentage is trivially gamed.

**Mutation is expensive.** Full-repository mutation on every PR destroys throughput. Target the diff and risk-sensitive modules; full suite nightly and for T2.

## Gate suite

```yaml
gates:
  always:
    - build
    - unit
    - types
    - acceptance
    - gherkin_bindings         # no undefined / pending / ambiguous steps
    - diff_coverage
    - sast
    - dependency_scan
    - license_scan
    - secret_scan
    - assurance_wiring         # assurance library present and configured
    - span_topology            # declared spans exist in the code
  by_story_type:
    - baseline_assertion       # scoped per the table above
  when_contracts_changed:
    - contract
  when_behaviour_changed:
    - revert_and_fail
    - mutation_targeted
  when_iac_changed:
    - iac_scan
    - policy
  at_merge_candidate:
    - full_suite_on_merged_shas
    - changeset_consistency
  tier_T2_or_scheduled:
    - mutation_full
```

`assurance_wiring` and `span_topology` are what enforce *every service the factory builds is assurance-designed*. A service that does not emit its declared telemetry does not merge.

---

# Part 9 — Skills

Skills are where reproducibility comes from. A prompt says what to do; a skill says how your organisation does it.

| Skill | Purpose |
|---|---|
| `assurance-sdk-integration` | **The most important one.** Wiring telemetry, manifest, accessors, directive receiver, declared spans. |
| `house-code-standards` | Language conventions, error handling, logging, DI, layout |
| `story-authoring` | Business Ready template, Gherkin conventions, failure scenarios |
| `test-authoring` | Test data patterns, fixtures, step bindings, meaningful assertions |
| `service-scaffold` | Golden-path template |
| `strangler-extraction` | Legacy extraction playbook |
| `ui-ux` | Front-end design intelligence |

Skills are content-hashed, pinned, and listed in the capability manifest. A skill change triggers the eval suite and must not regress `pass^k`.

## Third-party skills need a stricter boundary than dependencies

**Hashing tells you which skill you pinned. It does not tell you whether it is safe.**

A skill is more dangerous than a library. A library executes inside a sandbox with limited privilege. **A skill becomes instructions to an agent** — it shapes how the agent uses tool grants it already holds. It is a prompt-injection vector by design.

```
external skill
     ↓  quarantine
     ↓  licence review
     ↓  script and code inspection
     ↓  network and tool capability review
     ↓  PROMPT / INSTRUCTION REVIEW        ← unique to skills, and the point
     ↓
approved SkillPackage → signed + pinned → vendored
```

The instruction review asks: does this skill attempt to widen tool use, disable checks, alter output destinations, suppress errors, or instruct the agent to ignore other guidance? Those are not hypothetical failure modes; they are the natural shape of a malicious skill.

**Do not auto-track upstream.** Review one version, vendor it, own the pinned copy. An upstream update is a new review.

## UI/UX skill

**Vendor `ui-ux-pro-max`. Pin it. Do not fetch at build time.**

- `--persist` writes `design-system/MASTER.md` plus per-page overrides — a **committed, reviewable, diffable artefact**
- Search scripts are Python standard library only, no network calls — runs in a sandbox with egress blocked
- MIT licensed, so vendoring is clean; covers many stacks, not React-only

*(Published counts move between releases. Pin a version, read counts from the pinned copy.)*

**Its pre-delivery checklist is a gate specification handed to you free:**

| Checklist item | Deterministic gate |
|---|---|
| Contrast ≥ 4.5:1 | axe-core in CI |
| Visible focus states | axe-core + Playwright keyboard walk |
| `prefers-reduced-motion` respected | Playwright with media feature set |
| Responsive at 375 / 768 / 1024 / 1440 | Playwright viewport matrix + visual diff |
| No emojis as icons | Lint rule |
| Text reflows without clipping | Visual regression at narrow width and 200% zoom |
| — | Performance budgets (Lighthouse CI) |

**A taste engine, not a correctness engine.** Use it to seed the design system and inform your gates. The gates remain yours.

**Workflow:** run once per product, human approves `design-system/MASTER.md`, commit it. Agents thereafter read the committed file. Regeneration is a deliberate reviewed act.

## 21st.dev — optional external component acquisition

Component source, not a skill. Not in the automated path for v1: free tier allows two installs per day, generation consumes credits, and it pulls third-party code into your repo at build time. MCP tool manifest pinned by digest; newly advertised tools disabled pending approval. React only.

**v1:** human-assisted at design time; the chosen component enters as a reviewed, pinned dependency.
**Future:** `21st/MCP → quarantine → source inspection → licence → SCA → SBOM → tests → approved dependency`.

---

# Part 10 — Developer Surface and Workspace Model

## Three surfaces, no proprietary lock-in

The factory is the **SDLC control plane**, not a replacement for the tools people already use.

| Role | Primary interface |
|---|---|
| Business analyst / product owner | Factory chat + ALM (ADO / Jira) |
| Architect | Factory chat or web + VS Code |
| Developer | **VS Code** |
| Tester / QA | VS Code + test evidence views |
| Security | Existing security tooling + factory evidence |
| Release owner | ALM environment approvals |
| Operations | Cloud console + assurance plane |

A business analyst must never need VS Code. A security reviewer must never need a proprietary factory UI. The factory orchestrates the workflow; it does not conscript everyone into one interface.

## MCP, not a VS Code extension — for v1

**Do not build a Factory VS Code extension initially.** VS Code natively supports MCP servers as providers of tools, resources and prompts to its agent environment, and it has explicit trust boundaries for workspaces and for each MCP server.

More importantly: **an extension is VS Code-specific, which breaks the portability principle in this specification.** One MCP server serves VS Code, Claude Code, Cursor, JetBrains and anything else that speaks MCP. Build an extension only if a concrete UX need emerges that MCP structurally cannot serve — rich ChangeSet graph visualisation is the plausible candidate.

```
                    VS CODE
                       │
              Copilot / agent mode
                       │
                      MCP
                       ▼
                FACTORY MCP SERVER
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
 Repository      Story / ChangeSet   Gates
 Intelligence
                       ▼
                Factory backend
```

## The local MCP surface is read-mostly — this is a security boundary

A developer workstation is a **less controlled environment than the factory sandbox**. If a local agent can approve gates or write authoritative state, the workstation becomes an unaudited path around the entire gate model.

| Allowed from the local MCP surface | Never from the local MCP surface |
|---|---|
| `repo.find_symbol` / `find_references` / `impact_analysis` | Approve or bypass any gate |
| `repo.get_api_consumers` / `get_schema` / `get_related_tests` | Merge a PR |
| `factory.get_story` / `get_solution` / `get_changeset` | Mutate ChangeSet state or version |
| `factory.get_acceptance_tests` / `get_test_evidence` | Write to the ALM work item |
| `factory.run_gates` *(local, advisory result only)* | Deploy to any environment |
| `factory.explain_failure` | Alter approved-intent or containment contracts |
| `factory.ask_domain_expert` | Mint or read credentials |

Locally-run gate results are **advisory**. Authoritative gate results come from the factory pipeline against the exact merge candidate. A developer proving something locally is useful; it is not evidence.

The MCP server authenticates the developer and scopes tools to their role — a tester gets test authoring tools, not implementation write paths.

## The MCP tool surface is small and high-level

Expose intent, not machinery. The retrieval plumbing belongs inside Repository Intelligence.

```
EXPOSE                          DO NOT EXPOSE
factory.current_story           query_pgvector
factory.get_changeset           run_tree_sitter
factory.get_acceptance_tests    invoke_jdtls
factory.run_gates               execute_sql
factory.explain_failure         read_embedding_index
ri.find_symbol
ri.find_references
ri.impact_analysis
ri.discover_capability
ri.get_related_tests
ri.build_context
```

Low-level tools force the agent to plan retrieval, which is the Retrieval Planner's job and which it does better and deterministically. They also leak implementation detail into a surface you then cannot change without breaking every editor integration.

## Workspace enablement

Developers clone normally. Nothing replaces git.

```bash
git clone <repo-url>
cd payments-api
factory init          # or via MCP: "initialize this repository for the factory"
code .
```

```
payments-api/
├── .factory/
│   ├── project.yaml
│   ├── capabilities.yaml
│   ├── repository.yaml
│   └── skills.lock
├── .vscode/
│   └── mcp.json
├── docs/
│   ├── adr/
│   └── architecture/
└── <application code>
```

```yaml
# .factory/project.yaml
factory_version: 1
product: payments
repository: payments-api
service:
  type: assured_microservice
assurance:
  required: true
repository_intelligence:
  enabled: true
alm:
  provider: azure_devops
  project: payments-platform
```

`.factory/` is schema-validated by a gate. `skills.lock` is the pinning mechanism from Part 9.

## Multi-repo workspaces from a ChangeSet

The factory can generate a multi-root workspace containing **only** the repositories a ChangeSet affects, checked out at the exact base SHAs:

```
Refund Feature — cs-901.code-workspace
  legacy-api/
  refund-service/
  shared-contracts/
  infrastructure/
```

The developer does not hunt for and clone repositories by hand.

## Execution stays in the sandbox

```
                      FACTORY
                         │
                 ephemeral sandbox        ← agent changes and tests run HERE
                         │
                        PRs
                         ▲
                         │
VS CODE developer ───────┘
inspect · intervene · debug
```

Autonomous agent execution happens in the factory sandbox with scoped credentials and controlled egress — **not with unrestricted authority on a developer workstation**. This preserves reproducibility and keeps the credential invariant (Part 14) intact.

---

# Part 11 — Provider Plug-in Model

| Port | Adapters |
|---|---|
| `WorkItemPort` | ADO Boards, Jira, GitHub Issues, Linear |
| `ApprovalPort` | ADO environment checks, GitHub environment protection, Jira governed transitions |
| `SourcePort` | **LocalGitAdapter** (developer credentials) · **GitHubAdapter** (GitHub App) · Azure Repos, GitLab, Bitbucket service identities |
| `PipelinePort` | ADO Pipelines, GitHub Actions, GitLab CI, OCI DevOps |
| `ArtifactPort` | ACR, ECR, OCIR, Artifactory |
| `DeployPort` | AKS, EKS, OKE, ACA, Cloud Run, ECS, App Service |
| `SecretPort` | Key Vault, OCI Vault, AWS Secrets Manager |
| `IdentityPort` | Entra ID, OCI IAM, AWS IAM |
| `NotifyPort` | Teams, Slack, Email |
| `RuntimeFactsPort` | Assurance plane (sanitized) |
| **`ModelPort`** | Azure AI Foundry, OpenAI, Anthropic, Vertex, OCI Generative AI, self-hosted |
| **`FeatureFlagPort`** | LaunchDarkly, Azure App Configuration, AWS AppConfig, OpenFeature, custom |

## SourcePort — two modes

Git is always git. What differs is **whose identity performs the operation**.

```
                   SourcePort
             ┌─────────┴─────────┐
             ▼                   ▼
       LocalGitAdapter      GitHubAdapter
     developer's own        Factory GitHub App
     git credentials        service identity
             ▼                   ▼
         git clone            git clone
```

**LocalGitAdapter — build this first.** The first vertical slice works against a local checkout using the developer's existing SSH key, Git Credential Manager or GitHub CLI auth. No app registration, no infrastructure. RI indexes the local checkout.

**GitHubAdapter — needed once the factory operates repositories without a person.** A GitHub App gives repository-scoped permissions, installation against selected repositories, and installation tokens that expire in one hour. The Git operation is still ordinary:

```bash
git clone https://x-access-token:<installation-token>@github.com/org/repo.git
```

**Route it through the credential broker (Part 14).** The broker holds the App private key, mints scoped installation tokens on demand, and prefers performing the operation itself. A one-hour repository-scoped token is a reasonable rung on the credential ladder; a personal access token sitting in the platform is not.

## Repository cache and worktrees

Do not re-clone every repository for every task.

```
GitHub / ADO / GitLab
        ▼  SourcePort
  REPOSITORY CACHE (mirror, fetched incrementally)
        ▼
Repository Intelligence indexes from the cache
        ▼
per-task: git worktree at the exact base SHA → ephemeral sandbox
```

## Product manifest — onboarding several repositories

```yaml
product: legacy-payments
repositories:
  - {name: legacy-web,     url: github.com/acme/legacy-web,     role: frontend}
  - {name: legacy-api,     url: github.com/acme/legacy-api,     role: backend}
  - {name: shared-sdk,     url: github.com/acme/shared-sdk,     role: sdk}
  - {name: infrastructure, url: github.com/acme/infrastructure, role: discover}
```

`role: discover` lets RI determine the role from build manifests, dependency direction and API surface. Cross-repo relationships emerge from the graph rather than from declaration:

```
web → imports @acme/payments-sdk → calls /api/payments
  → legacy-api publishes payment.updated → worker consumes → database
```

## ModelPort — a governed selection interface, not a client wrapper

> **The provider-neutral, policy-controlled model-selection interface through which agents request capabilities rather than named models.**

PydanticAI performs the invocation. `ModelPort` adds what a client library does not: qualification, cost policy, risk policy, data residency, context budget, model-family independence, and escalation.

```yaml
model_capabilities:
  structured_output: true
  tool_calling: true
  max_context_tokens: 200000
  region: eastus2
  data_residency: US
  zero_data_retention: true
  model_snapshot_exposed: true      # can we pin an exact version?
  streaming: true
```

The orchestrator requests a **capability profile**, not a vendor:

```yaml
capability_profile: strong_reasoning_structured_us
requires:
  structured_output: true
  tool_calling: true
  min_context_tokens: 128000
  data_residency: US
  model_snapshot_exposed: true      # required for reproducible evals
```

### Reproducibility consequence — do not skip this

If models are selected by capability, **the run record must capture which model actually served each call.** Otherwise `pass^k` is not comparable across runs and your eval suite measures nothing.

```yaml
run_record:
  capability_profile: strong_reasoning_structured_us
  resolved_provider: azure-ai-foundry
  resolved_model: <exact snapshot identifier>
  resolved_at: "..."
  fallback_used: false
  context_pack_id: sha256:...          # what the model actually saw
  reviewer_provider: <resolved-provider>  # if cross-model review ran
  reviewer_model: <exact snapshot identifier>
```

**A provider or model change is a capability-manifest change** and triggers the eval suite, exactly like a prompt or skill change. Capability-based selection buys portability; it must not buy silent non-determinism.

**`ModelPort` is mandatory from Phase 0.** `FeatureFlagPort` can arrive with Phase 3.

## The qualified-model cascade

> **Use the least-expensive model empirically qualified for the task. Escalate on risk, on uncertainty, and on failed deterministic verification.**

Neither "always frontier" nor "always local." Both are unmeasured positions.

| Tier | What it is |
|---|---|
| **M0** | **No model.** Deterministic code, RI query, rule. Always try this first. |
| **M1** | Cheap local or small hosted model — classification, extraction, triage |
| **M2** | Strong self-hosted coding or agent model — high-volume implementation |
| **M3** | Frontier reasoning — architecture, security design, ambiguous judgement |
| **M4** | Independent heterogeneous review — a *different family* from whichever produced the artefact |

```
TASK
  ▼
deterministic solution exists?  ──yes──▶ M0, done
  │ no
  ▼
ModelPort selects cheapest qualified tier
  ▼
deterministic gates
  ▼
pass? ──no──▶ escalate one tier, retry (bounded)
  │ yes
  ▼
consequential output? ──yes──▶ M4 cross-model review
  │ no
  ▼
done
```

**Escalation is a qualification signal, not merely a cost event.** A high M1→M3 escalation rate for a capability means M1 was wrongly qualified for it. Track escalation rate per capability and re-qualify.

**Default to a single tier for the MVP.** Self-hosted M2 is real infrastructure — GPUs, a serving stack, operations. The architecture supports the cascade; the first working factory must not require it. Configure `tiers: [M3]` and widen once you have qualification data.

## The Model Qualification Matrix

**Models earn capabilities through your own evaluation data.** Not parameter count, not context length, not benchmark reputation, not vendor claim.

| Factory capability | Local / open | Frontier A | Frontier B |
|---|---|---|---|
| T0 implementation | *measure* | qualified | qualified |
| T1 implementation | *measure* | qualified | qualified |
| Unit-test authoring | *measure* | qualified | qualified |
| Acceptance-test authoring | conditional | qualified | qualified |
| Architecture | conditional | qualified | qualified |
| Security design | normally no | qualified | qualified |
| Strangler boundary | conditional | qualified | qualified |
| PR semantic review | conditional | qualified | qualified |

Qualification uses the metrics already defined in Part 14: `pass^k` · acceptance-test success · first-pass gate rate · human edit distance · repair attempts · review finding rate · production failure rate · latency · cost per merged story.

Routing rule: **cheapest qualified model wins, unless policy requires diversity or escalation.**

```yaml
qualification:
  capability: t1_implementation
  model: <provider>/<exact-snapshot>
  qualified_at: 2026-08-01
  expires_at: 2026-11-01          # qualification DECAYS
  evidence:
    trials: 40
    pass_pow_k: 0.91
    k: 5
    human_edit_distance_p50: 0.06
    cost_per_merged_story_usd: 1.20
```

**Qualification must expire.** Providers change snapshots, deprecate models and adjust serving stacks — often silently. A qualification with no expiry means routing on stale evidence. Re-qualify on a cadence and on any resolved-model change.

## Reference implementation strategy — frontier judgement, cheap volume

Once M2 is qualified, this is the high-ROI arrangement:

```
Business Ready + domain evidence
        ▼
FRONTIER ARCHITECT (M3)
        ▼
FRONTIER INDEPENDENT REVIEW (M4, different family)
        ▼
producer reconciliation → GATE 2
        ▼
OPEN / SELF-HOSTED CODER (M2)
        ▼
deterministic tests
        ▼
DIFFERENT OPEN MODEL PEER REVIEW (M2', different family)
        ▼
original coder repairs → deterministic gates
        ▼
PR → FRONTIER PR REVIEW (M3/M4)
        ▼
structured required changes → ORIGINAL CODER repairs
        ▼
gates → re-review if material → policy / human → MERGE
```

Frontier judgement where judgement is scarce; cheap high-volume coding where volume is the cost; model-family diversity at every review; deterministic verification throughout.

**Bound every semantic loop:**

```yaml
review_loop:
  max_cycles: 3
  same_finding_twice: escalate_to_human
```

No infinite model ping-pong. The same failure-signature rule as the Implementer repair loop.

## Adapter capability declaration

```yaml
adapter: github-issues
version: 1.3.0
capabilities:
  work_item_states: true
  custom_fields: partial              # labels only
  hierarchy: false
  webhook_on_transition: true
approval_capabilities:
  verified_identity: true
  separation_of_duties: false         # label can be applied by the author
  immutable_audit: false
```

Missing capability produces an **explicit degraded mode**, never a silent one. T2 cannot run on an adapter that cannot prove separation of duties.

**Rules:**

1. All provider-specific logic lives in adapters. No exceptions.
2. Workflow, agents, gates, story schema and skills are provider-neutral.
3. **The story schema is yours.** Never let a vendor field model leak into the core.
4. **Build the second adapter early** — an abstraction with one implementation is a wrapper.

---

# Part 12 — Release Identity, Environments and Promotion

## Two artefacts, not one

v2.1 contained a contradiction: the ReleaseManifest was declared immutable and promoted unchanged, yet contained per-environment configuration. Both cannot be true.

```
             RELEASE BUNDLE
           immutable + signed
                   │
        same through every environment
                   │
       ┌───────────┼───────────┐
       ▼           ▼           ▼
      DEV       PREPROD       PROD
       │           │           │
       ▼           ▼           ▼
 DEPLOYMENT   DEPLOYMENT   DEPLOYMENT
   BINDING      BINDING      BINDING
  (signed)     (signed)     (signed)
```

```yaml
release_bundle:
  release_id: rel_881
  changeset: cs_881
  changeset_version: 7
  work_item: 4711

  artifacts:
    backend_image:   sha256:...
    frontend_bundle: sha256:...
    typescript_sdk:  sha256:...

  infrastructure:
    terraform_module: sha256:...

  database:
    migration_bundle: sha256:...

  assurance:
    code_model_snapshot:  sha256:...
    approved_intent:      sha256:...
    containment_contract: sha256:...

  supply_chain:
    sbom:       sha256:...
    provenance: sha256:...
    signature:  ...
```

```yaml
deployment_binding:
  release_id: rel_881
  environment: production

  config_digest: sha256:...
  secret_version_refs: [kv://prod/payments-api#v14]   # references, never values
  infrastructure_parameters_digest: sha256:...
  feature_flag_state_digest: sha256:...
  deployment_policy_version: 12
  approved_by: ...
  signature: ...
```

**Promote the same ReleaseBundle. Bind it to an independently identified, separately signed environment configuration.**

### DeploymentBinding changes need their own gate

Production configuration is part of what determines behaviour. If a binding can change without approval, changing config becomes the way to bypass the entire gate model.

```yaml
deployment_binding_policy:
  production:
    requires_approval: true
    approval_tier: T2_if_touches_security_sensitive_config
    change_creates: work_item
```

### Verification at every hop

```
CI build → all artifacts + CodeModelSnapshot + SBOM + provenance
   → ReleaseBundle assembled and signed
      → registry
         → DEV      ← bundle signature + every artifact digest verified
         → PRE-PROD ← verified again, plus binding signature
         → PROD     ← verified again, plus binding signature
```

If you rebuild between environments, you tested one thing and shipped another. One ChangeSet produces one ReleaseBundle.

## Dev

Auto-deploy on merge, no gate. Ephemeral per-PR environments where supported. Acceptance, integration and contract tests. Synthetic data only. Failure returns the story to `IMPLEMENTING`.

## Pre-prod — risk-based

Production-like configuration. Masked or synthetic data. Never production data.

**Every substantive change:** acceptance · integration · contract · security scans · performance smoke against the story's numbers · deployment health · **assurance verification**.

**Risk-driven or scheduled:** 2–4 hour soak · full DAST · large load test · chaos exercise · full mutation suite · rollback rehearsal.

Soak and chaos on every ordinary change destroys throughput. Tie them to T2, stateful-path changes, and a nightly schedule.

**Assurance verification** — the reason pre-prod exists:

- Do declared spans actually appear in telemetry?
- Do capability probes pass in this environment?
- Does observed behaviour match the approved-intent manifest for this `code_model_snapshot`?
- Is the containment contract honoured under induced failure?

A service that cannot be assured does not reach production.

## Prod

Canary 5% → 25% → 100%. Auto-rollback on SLO breach, error-rate spike or latency regression. Feature flag as a faster second kill switch. Assurance plane watching from the first canary pod.

---

# Part 13 — Legacy Onboarding

**Assume legacy systems are not instrumented to this standard.** Assessment and augmentation come before any strangling. Do not begin by modifying the monolith with the full assurance SDK.

## Step 1 — Instrumentation Coverage Assessment

```
LEGACY APPLICATION
       ├── static repository analysis        ← immediate, zero risk
       ├── database / schema analysis        ← immediate
       ├── IaC and configuration analysis    ← immediate
       ├── existing logs                     ← immediate
       ├── platform / network telemetry      ← immediate
       └── zero-code OTel where the runtime allows
                    ↓  initial runtime picture
                    ↓  targeted code instrumentation
                    ↓  richer dependency map
```

**Zero-code OpenTelemetry** currently exists for Java, .NET, JavaScript, Python, Go and PHP. For a JVM monolith the agent captures inbound requests, outbound HTTP, database calls and many frameworks with no source change.

It does **not** capture arbitrary custom business logic — OTel says so explicitly. Zero-code covers framework and library boundaries. Business behaviour needs code-level instrumentation later.

## Step 2 — Coverage report

```yaml
assessment: legacy-billing-monolith
static_coverage:
  source_indexed: 94%
  build_reproducible: false          # ← blocker
  sbom_generatable: true
  db_schema_mapped: true
runtime_coverage:
  otel_zero_code_available: true
  inbound_http: AVAILABLE
  outbound_http: AVAILABLE
  db_calls: AVAILABLE
  message_queue: NOT_ASSESSABLE      # proprietary client
  business_transactions: UNAVAILABLE # needs code-level work
  batch_jobs: UNAVAILABLE
test_coverage:
  existing_suite: 11%
  runnable_in_ci: false              # ← blocker

verdict: NOT_READY
blockers:
  - "Build is not reproducible; cannot bind runtime evidence to source"
  - "No CI-runnable test suite; characterization tests have no home"
recommended_augmentation:
  - {story: "Make build reproducible; emit SBOM + CodeModelSnapshot", tier: T1}
  - {story: "Stand up CI harness able to run existing tests", tier: T1}
  - {story: "Deploy OTel agent, no source change", tier: T1}
  - {story: "Instrument message queue boundary", tier: T1}
```

## Step 3 — Go / no-go

| Blocker | Why it stops everything |
|---|---|
| Build not reproducible | Cannot bind runtime evidence to source. Build-derived intent is meaningless. |
| No CI-runnable tests | Characterization tests have nowhere to live. |
| No observability possible | Shadow comparison against an unobservable system is theatre. |

Refusing to start is the correct output in those cases.

## Step 4 — Augmentation runs through the factory

Augmentation items become ordinary T1 stories with normal gates. Ideal early proving ground: low risk, high value, exercises the whole pipeline on real work.

## Step 5 onward — strangle

**Dependency intelligence = static + dynamic.** Runtime traces show only what executed; cold paths, quarterly jobs, error handlers and admin functions never appear.

**Characterization tests classify, they do not canonise:**

```
observed legacy behaviour → classify
    ├── intentional   → preserve exactly
    ├── unknown       → investigate, then decide
    └── known defect  → decide deliberately whether to keep parity
```

**Shadow with side effects isolated — mandatory:**

```
live request
   ├── legacy    → real side effects
   └── candidate → side effects INTERCEPTED → compare outputs
```

No payments, no email, no production database mutation, no external business events, no inventory consumption, no duplicate transactions.

**Cutover gates:** characterization tests pass · shadow match ≥ threshold over an agreed duration · latency and error budgets held · rollback proven · assurance verification passed.

---

# Part 14 — Production Hardening

## Traceability — the full chain

```
work_item
   → changeset (version N)
      → PRs (exact merge SHAs)
         → build → CodeModelSnapshot + SBOM + provenance
            → ReleaseBundle (signed)
               → DeploymentBinding per environment (signed)
                  → deployed artifacts
                     → runtime span attribute
```

When the assurance plane detects anomalous production behaviour, it can name the story that caused it and retrieve the exact code model that artifact was built from.

## Credentials — the invariant

> **Anything reachable by a general-purpose, model-controlled shell must be considered accessible to the model.**

Environment variables are **not** a boundary. An agent with shell access runs `env`, reads `/proc/self/environ`, inspects child processes or files. v2.1's suggestion that env-var injection keeps a secret from the model was wrong.

**Priority ladder:**

```
1. Brokered operation           BEST — the broker holds the credential and performs the action
2. Workload identity            BEST — no credential material exists to leak
3. Purpose-built credential helper    (git / docker credential helpers, out-of-process)
4. Extremely scoped ephemeral token   LAST RESORT
```

```
Agent sandbox
     │  "publish artifact X"
     ▼
Credential Broker          ← holds credentials outside the sandbox
     ▼
Cloud / registry / ALM
```

If an ephemeral credential must enter a task environment:

- Short enough lifetime that exfiltration has limited value
- Single-use where the provider supports it
- Scoped to one operation on one resource
- Revoked at task teardown
- **Use audited independently**, so misuse is detectable even if the credential leaks
- Never long-lived, never static, never broadly scoped

## Other factory security

- **Repository content is untrusted input.** READMEs, comments, tests, issue text, PR comments and logs can carry prompt injection. Agent authority comes only from its signed capability manifest, the policy engine and the sandbox — never from text it read.
- Ephemeral sandbox per task, egress allowlisted to package registries.
- Output is always a pull request. Never a direct push.

## Agents are versioned capabilities

Prompts, model pins **and resolved model identifiers**, skills, tool grants and RI extractor versions are content-hashed in a signed capability manifest. Any change runs the eval suite and must not regress `pass^k`. Store per suite: trials, per-run success rate, `pass^k`, confidence interval, failure classes. Five runs is not statistical evidence.

## Cost control

Budget per story per tier. Circuit breaker at workflow level. **Cost per merged story is the headline metric.**

## Metrics

**Delivery — the DORA five:** change lead time · deployment frequency · failed deployment recovery time · change fail rate · deployment rework rate.

**Factory-specific:**

| Metric | Why |
|---|---|
| First-pass gate rate | Implementer quality |
| Repair attempts per story | Where tokens burn |
| **Human edit distance on merged PRs** | The honest quality signal |
| Judge override rate | Whether the Judge adds value |
| Escalation rate | Where agents hit their limits |
| Cost per merged story | Economic viability |
| Approval volume per person | Rubber-stamping risk |
| Reconciler divergence rate | Integration health |
| ChangeSet re-approval rate | Design churn |
| **Partial-merge frequency** | Multi-repo coordination health |
| **Reviewer acceptance rate** | ≈100% means rubber stamp — cost without value |
| **Producer rejection rate** | High means a bad reviewer or a defensive producer |
| **Context pack expansions per run** | Whether retrieval finds the right slice first time |
| Autonomous change failure rate | Whether to expand autonomy |

## Kill switches

Global factory stop · per-agent disable · budget breaker · autonomy dial (shadow → suggest → auto-PR → auto-merge narrow).

---

# Part 15 — Technology Selections

## Selection principle

**Most of this system is not AI work.** Repository Intelligence is parsers and a database. The workflow is a state machine and an outbox. The gates are CI. Reaching for an AI framework to solve those is how the stack stops being lean.

Three rules:

1. **Boring and mature over new and clever** for everything deterministic.
2. **Own the schemas, borrow the extractors.** Third-party tools produce inputs; the formats are yours.
3. **If you are writing a parser or a retry loop, you have drifted.** Both are solved.

## The agent layer — PydanticAI, not LangGraph

**Decision: PydanticAI for agents. Postgres state machine for workflow. Temporal only when the workflow demonstrably outgrows it.**

LangGraph is the strongest general-purpose framework for stateful agent workflows, and if the architecture were different it would be the right answer. It is not the right answer *here*, for a specific reason:

> **This specification already made the decisions LangGraph exists to make for you.**

Part 1 puts the business lifecycle in the ALM work item and execution state in Postgres with CAS transitions, an inbox/outbox and a reconciler. LangGraph's headline capability — durable graph state with checkpointed pause and resume — would be a *second* state store for a problem already solved, which is exactly the drift risk this document warns about elsewhere.

There is a second, sharper reason. **The graph you would draw is the wrong graph.** The genuinely graph-shaped thing is the gate flow, but it spans days, is advanced by ALM webhooks and human approvals, and is not owned by the runtime. Meanwhile the agents themselves are not graph-shaped at all:

| Agent | Actual shape |
|---|---|
| Domain Expert | Multi-turn conversation, linear |
| Architect | One structured output + bounded 1–2 round review |
| Test Author | One structured output |
| Implementer | Bounded loop: write → test → repair, max 3 attempts |
| Judge | One structured output |

The only loop is `while attempts < 3` with failure-signature tracking. That is a dozen lines of Python, not a reason to adopt a graph runtime and absorb its ramp cost.

**Why PydanticAI specifically** — it maps onto requirements already in this document rather than adding new concepts:

| Requirement in this spec | PydanticAI provides |
|---|---|
| Schema validation of LLM output (Part 8) | Automatic validation against Pydantic models — the framework's core premise |
| Budget per story, circuit breaker (Part 14) | Usage Limits: caps on request tokens, response tokens, total tokens **and tool calls** |
| `ModelPort` (Part 11) | Model-agnostic by design |
| Cross-Model Review (Part 7) | Per-agent model selection is trivial |
| Durable workflow later | Documented Temporal, DBOS and Prefect integrations |

It is a standard Python library with an API stability commitment, and it runs anywhere — no runtime to operate.

**Reconsider LangGraph if** any of these become true: you abandon ALM-as-authority and want the workflow engine to own business state; you need dynamic supervisor routing with handoffs you cannot enumerate in advance; checkpoint replay for time-travel debugging is worth its cost; or the team already runs it in production. Familiarity beats theoretical fit more often than architects admit.

**LangChain proper has no place here.** Its strength is breadth of third-party integrations. Your data sources are a Postgres graph, git and your ALM — each with a first-party SDK. Adding an abstraction over clients you already have is the opposite of lean.

## Repository Intelligence — evaluate before you build

This is where the most time is wasted. Before writing a parser, evaluate the existing local-first code-intelligence tools; several already produce call graphs, blast-radius impact and transitively-affected tests, exposed over CLI and MCP.

**The extraction split that works:**

| Need | Tool | Why |
|---|---|---|
| Symbol extraction, all languages | `tree-sitter` + language pack | Fast, error-tolerant, 40+ grammars |
| Precise references and definitions | `multilspy` | Wraps real language servers — `textDocument/definition`, `references`, `documentSymbol` |
| SQL and DDL | `sqlglot` | Multi-dialect, gives an AST |
| Terraform | `python-hcl2` | |
| OpenAPI | `prance` | |
| Protobuf | `grpcio-tools` | |

**Verify any call graph against a polyglot repository before trusting it.** Name-only indexes mis-wire cross-language calls — a Python `sorted()` linked to a Swift `sorted` — and a wrong call graph silently corrupts impact classification, which corrupts evidence invalidation, which is a security control.

**Storage: Postgres with recursive CTEs, `networkx` for in-memory algorithms. No graph database on day one.** The snapshot format and the port abstraction are expensive to change; the storage engine is not.

**Own `CodeModelSnapshot` regardless of which extractor you use.** These projects are young. Your build-derived intent format cannot be someone else's roadmap.

## Full stack

| Concern | Selection |
|---|---|
| Agents | `pydantic-ai` |
| API | `fastapi` + `pydantic` v2 |
| State, evidence graph | Postgres · `sqlalchemy` · `alembic` · `psycopg` |
| Durable workflow (later) | Temporal — only when Postgres + outbox demonstrably fails |
| Model routing, budgets | PydanticAI native; add `litellm` for centralized cost caps and fallback chains |
| MCP server | official `mcp` SDK (FastMCP) |
| Sandbox | `docker` SDK; hosted alternatives if you prefer not to operate it |
| Git operations | **subprocess `git`** — more reliable than library bindings for worktrees |
| GitHub | `githubkit` (async, typed) or `PyGithub` — both support App installation tokens |
| Azure DevOps | `azure-devops` (Microsoft official) |
| Jira | `atlassian-python-api` |
| BDD | `pytest-bdd` — the pytest ecosystem beats `behave` for tooling |
| Diff coverage | `diff-cover` — your gate, off the shelf |
| Mutation testing | `mutmut` or `cosmic-ray` |
| Contract testing | `schemathesis` (from OpenAPI) · `pact-python` (consumer-driven) |
| SBOM | `syft` binary + `cyclonedx-python-lib` |
| Signing | `sigstore-python` — cosign-compatible |
| Telemetry | `opentelemetry-python` — the invariant. Logfire, Langfuse or LangSmith are **replaceable products, not architecture** |
| Policy | **Plain Pydantic models and Python functions** |

## On AI observability products

The architectural invariant is: **OTel-compatible tracing, agent and model evaluation, datasets and experiments, cost and latency measurement.**

Langfuse, LangSmith and Logfire each satisfy that today. **None is a load-bearing boundary.** Pick one, keep the OTel layer underneath portable, and do not let an eval product's data model become your qualification schema — that schema is yours and it feeds the Model Qualification Matrix.

## On the policy engine

Resist OPA initially. The verification policy, impact classification, T0 predicates and evidence-invalidation table are all typed decision tables. As Pydantic models plus Python functions they are unit-testable, debuggable, type-checked and versioned with the code that uses them.

Adopt OPA only when policy genuinely needs to be authored by people who do not ship this codebase.

## What no library provides — this is the product

```
CodeModelSnapshot format + build-pipeline binding
ChangeSet + merge state machine + evidence invalidation table
Business Ready / Solution Ready schemas
Impact classifier + security-sensitive module registry
Verification policy
Cross-Model Review capability
ReleaseBundle / DeploymentBinding
Adapter capability manifests + fail-closed logic
Reconciler + conflict policy
Assurance SDK
```

Every one is a schema and a decision table. None needs a framework. **This list is the entire moat.** Everything above it is commodity.

## Phase 0 dependency set

```
pydantic-ai · fastapi · pydantic · sqlalchemy · alembic · psycopg
tree-sitter · tree-sitter-language-pack · multilspy
sqlglot · python-hcl2 · prance
mcp · docker · githubkit · azure-devops
pytest · pytest-bdd · diff-cover
opentelemetry-api · opentelemetry-sdk
```

Under twenty packages. **If Phase 0 needs materially more than this, scope has crept.**

## The experiment to run before estimating Phase 0

Install a local code-intelligence tool, point it at your largest repository, build its index, and ask for the blast radius of a function you know well. Check the answer by hand.

That single experiment tells you whether Repository Intelligence is a two-week integration or a two-month build. It is the largest unknown in the Phase 0 estimate, and it is answerable in an afternoon.

---

# Part 16 — Build Order

## Phase 0 — Contracts and foundation

**Scope discipline is the biggest delivery risk in this plan.** Repository Intelligence can absorb two years. Build only what the first vertical slice needs.

**Phase 0 RI — build:**

```
repo and service catalog · symbol / reference index
OpenAPI and event contract extraction · DB schema map
IaC ownership map · test inventory · git and build identity
security-sensitive module registry
CodeModelSnapshot format and generation      ← the format matters most
```

**Defer:** deep call graph · coverage-to-symbol mapping · runtime correlation · advanced graph traversal · historical architecture inference.

**You do not need a graph database on day one.** Postgres with good indexes gets the first version surprisingly far. The **snapshot format and the port abstractions** are expensive to change; storage is not.

**Also in Phase 0:** story schemas · state machine with CAS transitions · inbox/outbox/reconciler · ChangeSet schema including merge-state machine · adapter ports including `ApprovalPort`, `RuntimeFactsPort`, **`ModelPort`** and **`SourcePort` (LocalGitAdapter only)** · impact classifier · **Context Builder with recorded context packs** · **Factory MCP server, read-mostly surface** · `factory init` and `.factory/` schema · workflow engine and event log · ephemeral sandbox and **credential broker boundary** · minimal assurance-SDK contract.

*Deterministic retrieval only in Phase 0.* Embeddings and semantic code search are a Phase 2+ addition — deterministic symbol and contract retrieval carries the first slice, and standing up an embedding store early is a common way to spend two months without improving answer quality.

*Usable output: structured, gated stories. Value before any agent exists.*

## Phase 1 — Legacy assessment (parallel with Phase 2, if you have a monolith)

Instrumentation Coverage Assessment · zero-code OTel · coverage report and go/no-go · augmentation stories.

*Needs only Phase 0 RI. Delivers standalone value.*

## Phase 2 — Greenfield vertical slice

```
one provider · one greenfield TypeScript or Python service · one repository
   → conversation → Business Ready → G1 → Solution Ready → G2
   → frozen acceptance tests → implementation → deterministic gates
   → Judge → verification policy → PR → DEV
```

**RI and ChangeSet present in minimal form from the start**, even with one repo. Retrofitting is expensive; carrying them from day one is nearly free.

Developer surface: `git clone` → `factory init` → `code .` → agent mode against the Factory MCP server. No extension.

**Cross-Model Review** arrives here for architecture artefacts only, then widens by tier.

**Autonomy: suggest only.** Measure human edit distance for several weeks before going further.

## Phase 3 — Production delivery

ReleaseBundle + DeploymentBinding · pre-prod and prod · signing, SBOM, provenance, verify at every hop · `FeatureFlagPort` · canary and auto-rollback · assurance verification · risk-based suites.

## Phase 4 — Multi-repo ChangeSets

Cross-repo ordering · contract overlap · impact-based evidence invalidation · merge-candidate revalidation · merge queues · **partial-merge handling**.

*Do this before adding more agents. It is where multi-repo factories actually break.*

## Phase 5 — Provider proof

Second ALM or cloud adapter, **`GitHubAdapter` with a Factory GitHub App**, and a second `ModelPort` provider. Flushes out leaked assumptions while cheap to fix.

*The second `ModelPort` provider is also what makes Cross-Model Review possible with genuine model-family independence.*

## Phase 6 — Strangler mode

Static + dynamic dependency intelligence · characterization classification · shadow harness with side-effect isolation · cutover gates.

## Phase 7 — Controlled autonomy

T0 auto-merge first, per the constrained change classes. Expand only on measured evidence.

---

# The Test That Matters

> Can a person describe a change in conversation, approve it three or four times, and have working, tested, observable, assured code running in production — with the whole path auditable from the conversation to the running container, and the exact code model that container was built from?

Everything here serves that. Anything that does not is optional.

---

# Change Log — v2.1 → v2.2

| # | Change | Reason |
|---|---|---|
| 1 | **ReleaseBundle (immutable, promoted) split from DeploymentBinding (per-environment, separately signed)** | v2.1 claimed the manifest was promoted unchanged while containing per-environment config — a direct contradiction |
| 2 | **`config_digest` removed from CodeModelSnapshot; `build_config_digest` used for build-time defaults only** | The snapshot describes what the build contains; runtime config is deployment identity |
| 3 | **DeploymentBinding changes require their own approval** | Otherwise changing production config bypasses the entire gate model |
| 4 | **Credential invariant rewritten: env vars are not a boundary** | v2.1's env-var suggestion was a security defect. A model-controlled shell runs `env`. |
| 5 | **Judge produces an assessment; a deterministic verification policy produces the decision. DoD says "verification policy decision: PASS"** | v2.1 said "the Judge is not the gate" then made Judge verdict a Definition-of-Done item |
| 6 | **Evidence invalidation is impact-based, not surface-based; security-sensitive module registry added to RI** | A change with no surface change can delete an authorization check |
| 7 | **Risk tier derived from impact; T0 defined by constrained change classes with predicates** | `AUTHORIZATION_ENABLED=false` is a config change; an auth library major is a dependency bump |
| 8 | **`ModelPort` added and mandatory from Phase 0, with resolved-model recording** | The plan abstracted every provider except the LLM. Capability-based selection must not buy silent non-determinism. |
| 9 | **`FeatureFlagPort` added (Phase 3)** | Feature flags are load-bearing in the rollback model |
| 10 | **Baseline-failure gates scoped by story type, including the refactor inverse** | An unconditional gate makes behaviour-preserving refactors unsatisfiable |
| 11 | **Third-party skills get a supply-chain boundary with instruction review** | A skill becomes instructions to an agent — more dangerous than a library, which merely runs sandboxed |
| 12 | **ChangeSet merge-state machine with PARTIALLY_MERGED and a declared resolution policy** | Multi-repo merges are not atomic. A partial merge must never equal a release. |
| 13 | **Impact classifier and T0 classification fail closed** | Unknown must never mean safe |

---

# Change Log — v2.2 → v2.3

No architectural boundary changed. Three capabilities added, one integration decision recorded.

| # | Change | Reason |
|---|---|---|
| 1 | **Part 10 added — Developer Surface and Workspace Model** | The spec described the factory but never how a person works with it |
| 2 | **MCP-first; no VS Code extension in v1** | An extension is VS Code-specific and breaks the portability principle. One MCP server serves VS Code, Claude Code, Cursor and JetBrains alike. |
| 3 | **Local MCP surface is read-mostly, with an explicit deny list** | A workstation is less controlled than the sandbox; a local agent that can approve or merge is an unaudited path around the gate model |
| 4 | **Locally-run gates are advisory, never evidence** | Authoritative results come from the pipeline against the exact merge candidate |
| 5 | **Retrieval and context architecture added to Part 2** | "Embeddings discover, structured code intelligence verifies" — deterministic-first applied to retrieval |
| 6 | **Context packs content-addressed and recorded in the run record** | If the pack varied and was not recorded, `pass^k` measures nothing — same reasoning as recording the resolved model |
| 7 | **Capability slice, with a mandatory `unknown` list** | Fail-closed applied to capability discovery; a slice that hides what it could not find yields a confident, incomplete extraction plan |
| 8 | **Cross-Model Review capability added; Judge reframed as a specialization of it** | Generalises independent review to all high-value semantic outputs without adding five reviewer agents |
| 9 | **Review loop bounded; reviewer acceptance and producer rejection rates tracked** | Ping-pong is a real failure mode; a reviewer that accepts everything is cost without value |
| 10 | **`SourcePort` split into LocalGitAdapter and GitHubAdapter (GitHub App, via the broker)** | Git is always git; what differs is whose identity operates it. A one-hour repo-scoped token is a defensible rung on the credential ladder; a personal token in the platform is not. |
| 11 | **Repository cache/mirror and per-task worktrees made explicit** | Re-cloning every repository per task does not scale |
| 12 | **Product manifest with `role: discover`** | Multi-repo onboarding; roles emerge from the graph rather than from declaration |
| 13 | **Embeddings deferred to Phase 2+** | Deterministic retrieval carries the first slice |

---

# Change Log — v2.3 → v2.4

No architectural boundary changed.

| # | Change | Reason |
|---|---|---|
| 1 | **Part 15 added — Technology Selections** | The spec specified behaviour but never named a stack; v2.0's stack was removed as non-lean and never replaced |
| 2 | **PydanticAI selected for the agent layer; LangGraph explicitly declined** | Part 1 already puts business state in the ALM and execution state in Postgres. LangGraph's durable-checkpoint value would be a second state store for a solved problem. The agents are also not graph-shaped — the only loop is a bounded repair counter. |
| 3 | **LangChain excluded** | Its strength is integration breadth; every data source here has a first-party SDK |
| 4 | **Policy engine is plain Pydantic, not OPA, until proven otherwise** | Typed decision tables are more testable in Python than in Rego, and version with the code that uses them |
| 5 | **Repository Intelligence: evaluate existing extractors before building** | Largest time sink in Phase 0, and mature local-first tools already produce call graphs and impact analysis |
| 6 | **Cross-language call-graph verification called out as mandatory** | A wrong call graph silently corrupts impact classification, which is a security control |
| 7 | **Conditions for reconsidering LangGraph recorded** | So the decision can be revisited on evidence rather than re-argued |
| 8 | **Phase 0 dependency set capped at ~20 packages** | A concrete scope-creep tripwire |

---

# Change Log — v2.4 → v2.5

No architectural boundary changed. Six additions and two refinements.

| # | Change | Reason |
|---|---|---|
| 1 | **Retrieval Planner added to Part 2** | Exposing AST, lexical and semantic retrieval as three tools makes the agent plan retrieval; the planner does it deterministically and better |
| 2 | **Context Builder named as a component distinct from RI** | RI is persistent system knowledge; the Context Builder is a task-specific compiler. Conflating them is how repositories end up in prompts. |
| 3 | **TaskContext separated from ContextPack** | Distinguishes "the model reasoned badly" from "the Context Builder never supplied the evidence" — different defects, different fixes |
| 4 | **Independent reviewers build their own ContextPack (mandatory)** | Sharing the producer's pack makes a bad producer retrieval a shared blind spot, defeating the purpose of independent review |
| 5 | **ModelPort reframed as a governed selection interface** | It adds qualification, cost, risk, residency, family independence and escalation — none of which a client library provides |
| 6 | **Qualified-model cascade M0–M4 added** | "Cheapest empirically qualified model, escalate on risk and failed verification." M0 (no model) first, always. |
| 7 | **Model Qualification Matrix with expiry** | Models earn capabilities through your eval data, not vendor claims. Qualification decays because providers change snapshots silently. |
| 8 | **Reference frontier/open/frontier strategy, with bounded review loops** | Frontier judgement plus cheap high-volume coding plus family diversity. `max_cycles: 3`, same finding twice escalates. |
| 9 | **MCP tool surface constrained to high-level intent** | Low-level tools leak implementation detail into a surface you then cannot change |
| 10 | **AI observability products declared non-architectural** | Langfuse, LangSmith and Logfire are replaceable; the OTel invariant is not |
| 11 | **Retrieval miss rate added as a metric** | Directly measures Context Builder quality |

---

**Document version 2.5 — CLOSED.**
