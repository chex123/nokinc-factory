# Complete factory delivery plan

**Decision date:** 12 September 2026. **Status:** partially implemented; not
release-ready. Current evidence: [delivery ledger](DELIVERY-LEDGER.md).

Read the [verified readiness report](READINESS-2026-09-12.md) first. This is the
execution contract for two broad authorizations, with resumable checkpoints,
not a promise of two short runs or immediate commercial readiness. Each pass
contains independently testable work packages. Stop at real access, approval,
budget or safety blockers; do not stop merely to request another micro-prompt.

## 1. Confirmed product decisions

| Decision | Agreed direction |
| --- | --- |
| Product | Multi-tenant commercial SaaS from its first operational release; not merely an internal single-org tool |
| Architecture | Lean software-factory platform, deterministic policy and workflow; AI proposes, never grants itself authority |
| Agentic quality standard | Doer → another-model reviewer → doer reconciliation for every workflow run or generated; at least two configurable reviews, grounded criteria and hard limits; see [ARP-1](AGENT-REVIEW-POLICY.md) |
| Manufactured mobile apps | Optional Expo/React Native Maestro capability; target-owned tests, IDE-independent local feedback, authoritative isolated CI; deterministic UI plus real integration/release lanes; see [mobile testing](MOBILE-TESTING.md) |
| Clouds | Azure, **Oracle Cloud Infrastructure**, and AWS supported target deployments; no forced customer cloud |
| Pilot coverage | Existing NOK-Apps repositories; payments; governed claims; a genuinely empty synthetic greenfield repository |
| Identity/commercial basics | OIDC login, tenant isolation, metered quotas, manual invoicing initially; no mandatory payment processor |
| Human gates | Routine work one consolidated consequential approval; sensitive work pre-build approval and two-person production authorization; errors/uncertainty/irreversible actions stop |
| Available people | One now: ordinary work and sandbox tests can proceed; sensitive production waits for a second distinct person |
| Simulation | Local disposable tests and Podman approved; read-only GitHub approved; synthetic live-model and named disposable external tests allowed subject to scoped access and budget |
| Spend | **$20 total test ceiling**, not per provider or per retry; no recurring spend authority |
| Persistence | Team-shared on-demand skill plus concise repository memory |
| Publication | No repository commit/push/merge or production deployment without explicit authorization |

Regions/data residency, cloud sandbox IDs, identity issuer, qualified models,
tenant scale/SLOs, support/legal obligations and additional provider access remain
unset. Use interfaces and synthetic fixtures until resolved; do not fabricate
approved values. Existing authenticated cloud defaults are not permission to use
those resources.

The [v2.5 specification](factory-spec.md) remains the architectural baseline.
The user has approved changing its gate policy, expanding commercial/cloud scope,
and making the [bounded doer/reviewer policy](AGENT-REVIEW-POLICY.md) standard.
ARP-1 explicitly replaces the old one-default/two-T2 review counts and selective
artifact exclusions; local reducer/store/coordinator enforcement exists, but
generated workflows and real providers are not qualified. Pass A must ratify
the versioned amendments and independent tests before changing control behavior.
Do not silently rewrite the CLOSED contract or existing frozen acceptance tests.

## 2. Lean target architecture

- **One modular control-plane application:** FastAPI + Pydantic, Postgres with
  SQLAlchemy/Alembic, and a worker from the same codebase/image. Postgres owns
  execution/inbox/outbox/leases; the ALM remains business-lifecycle authority.
  No LangGraph, microservice proliferation, mandatory Redis or Temporal.
- **One small web interface** for nontechnical users: tenant onboarding,
  conversation/clarification, backlog, work progress, approvals, evidence and
  usage. React/TypeScript is a proposed reusable choice, not a separate platform.
  CLI and authenticated read-mostly MCP use the same application services.
- **Tenant isolation throughout:** authenticated tenant context, database RLS
  plus application authorization, job/lease/evidence/cache/object prefixes,
  repository allowlists, per-tenant secrets and budgets. Worker credentials must
  not bypass isolation inadvertently. Test every direct-object-reference path.
- **Trusted execution broker:** GitHub App/workload identity operates outside
  model-controlled shells. Ephemeral per-task sandboxes have CPU/memory/time/
  egress limits, no host socket, no ambient tenant/cloud credentials and no
  trusted caches from PR code. Customer/BYOC runners use scoped identities.
- **Provider-neutral core, declared adapters:** ALM/source/approval/pipeline,
  models, artifacts/deploy/secrets/flags/identity/runtime facts. Shared contract
  suites prove parity; unsupported capability is explicit and policy-blocking.
- **Portable release:** OCI-format images and signed evidence, Postgres, object
  storage and OTel. Azure/OCI/AWS are deployment profiles, not three duplicated
  orchestrators. Avoid Kubernetes unless a required capability demands it.
  A control-plane installation can use one chosen region/provider; this is not
  an unnecessary three-cloud active-active design.
- **Governed AI:** PydanticAI invocation behind `ModelPort`; exact resolved model,
  family, residency, context digest and cost recorded. Begin with qualified
  hosted models; different vendor names or Azure/OpenAI endpoints do not prove
  different model families. Deterministic work never requires model calls.
- **Shared quality/review contract:** apply [ARP-1](AGENT-REVIEW-POLICY.md) to
  stories, C4-based designs, tests, code, analysis and generated customer-agent
  outputs. Minimum two validated review passes; recommended maximum three;
  immutable criteria, evidence-backed findings and no style-driven repair loops.
  Budget/counter enforcement spans economical and flagship stages. Neither role
  can approve an action, amend its own contract or review its own code changes.

Repeatable means identical input/evidence/policy identities are replayable and
decisions are auditable. It does not mean a stochastic LLM reproduces identical
text. Hash and retain what each run actually saw, proposed and executed.

## 3. Minimal consequential human decisions

| Class | Nonproduction work | Consequential authorization |
| --- | --- | --- |
| Qualified narrow T0 | Pre-approved class and bounds; all mandatory machine checks | Initially one PR/consolidated approval. Opt-in automatic handling only after qualification and bounded policy |
| Ordinary T1 | Clarification, draft design, frozen tests, implementation, review and sandbox validation within tenant policy/budget | One consolidated approval of complete current evidence before the permitted merge/release consequence |
| Sensitive T2 | Drafting allowed; solution/security/rollback approval required before implementation | Two distinct authorized humans approve the exact production decision; missing quorum blocks |
| Destructive/irreversible, policy or tenancy boundary changes | Explicit risk owner and scoped plan | T2 floor, plus any legally/provider-required control; never self-approved by an agent |
| Unknown impact, missing evidence, failed test, outage, budget exhaustion | Pause/block with actionable evidence | Never auto-pass; retries bounded; same signature twice escalates |

No separate routine G3 pause: preproduction verification is deterministic. Keep
story/solution/release evidence even when a human pause is removed. Do not hide
work by removing the stage itself. Changes to approved content invalidate its
approval. Two accounts belonging to one person are not a valid two-person quorum.

The consolidated decision must bind tenant, work item, ChangeSet version,
candidate SHAs, gate/review/context digests, release artifacts, environment
binding, approver subjects, policy version and validity window. Evaluate
revocation and target drift immediately before each authorized consequence.

**Provider reality:** required GitHub PR/environment reviews remain enforced.
One logical approval is not a license to weaken existing branch rules or reuse
an approval for a different digest. If a provider cannot consolidate the exact
candidate/release decision, show the additional requirement or block that mode.
Resolve this in the policy amendment before promising a one-click experience.
Payments currently requires two PR approvals even for governed development
merges. With one person, those provider merges can also block Pass A: use an
authorized synthetic sandbox, or wait for legitimate quorum. Do not bypass them.

## 4. Requirements-to-work-package map

No requirement disappears because an old checklist called it post-MVP. Launch
scope and long-term qualification are reported separately.

| Specification / requested feature | Present | Completion packages |
| --- | --- | --- |
| Part 1: lifecycle, approval, dedupe, outbox, reconciliation | Schemas + optimistic adapter | A01–A04 |
| Part 2: RI, snapshots, retrieval, TaskContext/ContextPack | Issue snapshot only | A05, B03, B06 |
| Part 3: versioned ChangeSets, invalidation, exact candidates, partial merge | Invalidation table only, with defects | A01, A07, B02 |
| Part 4: risk policy and minimal human gates | States, no enforcing tier/verification service | A00, A02, A06 |
| Parts 5–6: validated BusinessReady/SolutionReady | Partial schemas | A01, A05–A06 |
| Part 7: five roles, independent retrieval/review, bounded reconciliation | One agent builder + CI review | A05–A07 |
| Part 8: meaningful BDD/TDD, frozen tests, non-gameable complete gates | Partial tests/CI; red/green lane defect | A01–A02, A07–A08 |
| Part 9: approved/pinned skills, UI/UX/accessibility | Instructions/templates only | A05, B04 |
| Part 10: business/developer interfaces, MCP, init | CLI stubs | A06, A09, B04 |
| Part 11: provider ports, brokered source, model qualification | Few protocols/one ALM adapter | A03–A05, B01, B05 |
| Part 12: signed bundle/binding, same-artifact promotion, canary/rollback | Identity schemas only | B01–B03 |
| Part 13: legacy readiness, characterization, safe shadow/cutover | Missing | B06–B07 |
| Part 14: trace, sandbox, evals, budgets, metrics, kill switches | Scattered primitives | A03–A08, B03, B05, B08 |
| Part 15: lean stack and capability discipline | Partial dependency declarations | All packages; no additional orchestration framework |
| Part 16: provider proof and controlled autonomy | Missing | B05–B09; evidence-earned expansion cannot be fabricated |
| New: SaaS tenancy, OIDC/RBAC, onboarding/admin, metering/invoicing | Missing | A03–A04, A09, B04, B08 |
| New: universal bounded doer/reviewer cycles and C4-based QualityContract | Local contracts/store/coordinator tested; full workflow/provider integration missing | A00, A04–A07, B05, B07 |
| New: mobile E2E and visual/regression evidence | Local Maestro adapter; native execution/CI qualification pending | A05, A07, B04, B07 |
| New: Azure + Oracle Cloud Infrastructure + AWS | Missing | B01, B07 |
| MVP: real refunds + SDK + infra + sentence-to-span evidence | Scaffold | A08, B02, B07 |
| Pilot brownfield and genuinely new applications | Not factory-onboarded | A09, B06–B07 |

## 5. Execution rules for both passes

1. Inventory the current tree/PRs, preserve user edits and pin the baseline.
   Work on approved branches/worktrees; never quietly promote unmerged work.
2. Keep a durable ledger per package: requirement, input/source/tool digests,
   tests, outputs, actual commands/exit codes, reviewer disposition, spend,
   blockers and the exact next action. Resume at the earliest incomplete item.
3. Author the failing independent behavior/contract tests first. Existing frozen
   suites cannot change in implementation tasks. New frozen suites require a
   separately authorized Test Author artifact/PR and approval path.
4. Implement the smallest vertical change. Run focused red→green, unchanged
   acceptance, types/lint, changed-line AND changed-branch coverage, security
   gates and targeted mutation/revert checks as applicable.
5. Apply [ARP-1](AGENT-REVIEW-POLICY.md) at each agentic artifact boundary: doer,
  independent reviewer, doer reconciliation, and at least two completed valid
  review passes before acceptance. Share authoritative TaskContext/QualityContract
  but retrieve packs independently; no producer reasoning. Evidence, not taste,
  determines material findings. Missing reviewer is not approval or rejection;
  T2 requires a genuinely independent qualified model family.
6. Verify exact merge candidates in trusted CI. Local preflight is advisory.
   Required gates unavailable/skipped/not applicable have distinct, enforced
   meanings; PR labels or issue prose never grant authority.
7. Bound repair/review attempts and costs. Same failure twice → stop that repair
  loop and report; at most three implementation repairs. ARP-1 recommends two
  minimum and three maximum review passes, configurable only by bounded policy.
  Minimums never override early safety stops; repeated advisory suggestions do
  not count as unresolved failures. Persist counters and aggregate budgets across
  restarts/model changes. A final unreviewed repair or exhausted cap cannot PASS.
8. At a blocked package, finish independent safe work, persist the blocker and
   next action, and ask one consolidated question only if it cannot be inferred.

### Pass A — secure foundation and complete tenant-scoped single-repo loop

#### A00 — Baseline and scope amendment

- Reconcile PR #7 with preserved local tests and independently reviewed source
  defects; retain main/PR/working-tree distinctions. Do not simply merge the
  branch because local coverage is now green.
- Version the gate/SaaS amendment, launch capability matrix and threat model.
  Confirm regions, identity, sandbox allowlists and performance/cost envelopes.
- Ratify ARP-1, its QualityContract and independent conformance cases, including
  universal artifact scope, configurable review counts and unchanged human gates.
- Exit: approved requirements and executable policy contracts; every launch
  requirement has an owner package and measurable test.

#### A01 — Repair primitive correctness using the audit reproducers

- Fix per-file impact union and semantic cosmetic checks; strengthen readiness,
  authorization windows, immutable snapshots and child digest validation.
- Harden raw Git capture: no helpers/text conversion/config surprises, stable
  HEAD/index/worktree read, paths/modes/symlinks/submodules explicit, no mutation.
- Exit: all relevant audit probes green as real regressions, current 246 tests
  preserved, additional adversarial/time/race tests pass; no untested signature
  or identity string is presented as authorization evidence.

#### A02 — Trusted gates, approval evidence and frozen-contract lanes

- Extract reusable deterministic gate/review logic from replicated inline YAML.
  Test it locally; distribute version-pinned trusted controls per target language.
- Separate PR-code execution from secrets and privileged status publishing.
  Validate triggering workflow identity, immutable diff/base/head/candidate,
  complete TaskContext, approved model identity and final SHA before status.
- Replace label-only trust with task purpose, frozen-suite digest, permitted
  author role and changed-path policy; revalidate on label/base/content changes.
- Separate red-contract lane from green-implementation lane: old baseline green,
  new assertion fails for the intended behavior, bindings/collection valid;
  refactors pass before/after; infra gets its real applicable test policy.
  A tests-only revision must not trigger a deploy of unimplemented behavior.
- Implement provider approval evidence retrieval, protected-ref/run binding,
  identity/quorum/expiry/revocation checks and comprehensive remote verifier.
- Exit: four workflow probes plus adversarial workflow/label/ref/history tests
  green. Authorized sandbox provider checks prove the controls, not just mocks.

#### A03 — Tenant identity, authorization and trusted credential boundary

- OIDC issuer/audience/nonce/session handling, tenant memberships and roles;
  canonical human subjects across external accounts; tenant admin audit trail.
- GitHub App installation/repository scoping, credential broker, no static PAT
  in model shells. Webhook signature verification and replay protection.
- Tenant-specific secret references, data-egress/residency policy, quotas and
  a global/per-tenant/per-agent stop. Policy edits themselves require authority.
- Exit: forged/cross-tenant requests, worker bypasses, repository substitution,
  secret exfiltration attempts and alias-based double approvals all fail closed.

#### A04 — Durable execution and evidence

- Postgres migrations: tenant keys/RLS, workflows/tasks/leases/attempts,
  inbox/outbox, event log, approval/evidence references and usage reservations.
- Atomic local transitions, dedupe receipts and idempotency keys; reconcile ALM
  business position without treating it as authorization. No duplicate side
  effects on crash, timeout, redelivery or successful-write/lost-acknowledgement.
- Evidence store has content verification, restricted mutation and an immutable
  audit export/retention contract. Audit access is itself tenant-scoped.
- Persist review identities, producer lineage, open finding/resolution ledgers,
  completed reviews versus invocation attempts, and atomic child/parent budget
  reservations. No reset through replays, model swaps or artifact amendments.
- Exit: real Postgres concurrency/crash/restart/replay/isolation simulations,
  migration upgrade tests, deterministic event replay and basic restore pass.

#### A05 — Minimum RI, Context Builder, skills and ModelPort

- Exact-revision repository/service catalogue, verified symbols/references,
  OpenAPI/events/test inventory and sensitivity registry using mature extractors.
- Full authoritative TaskContext distinct from per-role content-addressed
  ContextPack; bounded retrieval, explicit unknowns, independent reviewer packs.
- Versioned QualityContract binds criteria/evidence thresholds and applicable C4
  design/interfaces/invariants. Pin role/risk/model eligibility; a rewritten
  prompt, stale report or subjective score cannot substitute for verification.
- Role-specific signed capability manifest: approved prompts, skills, model and
  extractor versions, tools, residency and token/cost limits. No auto-tracking
  third-party skills or instructions that widen authority.
- Qualified producer/reviewer adapters with structured output, recorded exact
  identity/family, availability classification, bounded retry and budget breaker.
- Qualify the minimum genuinely independent reviewer family **here**, before
  applicable high-risk review/publication exits in Pass A. Missing provider keys
  block that qualification; another same-family agent is not a substitute.
- Exit: polyglot symbol ambiguity/unknown coverage/context drift and injection
  tests; deterministic fakes first, then bounded synthetic real-provider evals.

#### A06 — Application services and five-role workflow

- Domain Expert asks and preserves answers; rejects guessed requirements.
  Architect produces validated design/rollback/observability; independent Test
  Author freezes executable tests before Implementer TDD. Judge is reusable
  cross-model review, not another authority service.
- Implement ARP-1 once for all agentic roles and workflow generators: full first
  review, evidence-backed doer fixes, closure/regression review, finite limits,
  explicit acceptance/block/escalation and read-only reviewers. Economical and
  flagship doer/reviewer stages share counters/budgets and unchanged contracts.
- Wire clarification → ready/design → permitted build → gates/review → PR →
  approved consequence → verified dev, with pause/resume/status/trace/error paths.
- Make `chat`, `gate`, `status`, `trace` real. An approval command requests an
  authenticated approval flow; it never mints approval from a CLI boolean.
- Produce approved-intent and containment manifests, reviewed SDK integration
  (telemetry, accessors and authenticated directive receiver), and explicit
  capability probes. Test unauthorized/replayed directives and leader/quorum/
  lease constraints; tracing alone is not assurance enforcement.
- Exit: a tenant-scoped conversation completes a single-repo dev story in the
  simulator and stops correctly on unknowns, unauthorized actions and outages.
  ARP-1 conformance must reject single-pass success, advisory-driven loops,
  stale/omitted findings, unreviewed final edits and generated-runtime bypasses.

#### A07 — Sandboxed implementation and complete gate execution

- Exact-base worktrees, constrained tools, cache provenance, resource/egress
  limits, broker-only writes, bounded repair and machine-readable failures.
- Implement the [manufactured-app testing boundary](MOBILE-TESTING.md): target
  repositories own their tests; factory policy schedules/enforces them. Add
  optional Maestro workers for Expo/mobile: macOS/iOS and Linux/Android, typed
  test inventory and toolchain manifest, build/install identity verification,
  current JUnit evidence, protected flows/fixtures/baselines and no missing-test
  PASS. Local CLI/IDE results remain advisory; no mandatory SaaS test vendor.
- Separate mock UI from real-backend and production-package lanes; pin all build,
  device, data, OTA/config and baseline identities. No secret in Expo public
  variables, production mock bypass or unbounded rerun-to-green. Simulator builds
  cannot be presented as the same shipping IPA/AAB or as real integration proof.
- Run architecture/contract checks against the approved C4-based slice and
  behavioral criteria. Consequential tool operations require policy/broker
  authority before execution, never a doer/reviewer consensus after the fact.
- Executing Python/TypeScript/HCL toolchains: build, types, lint, unit, BDD,
  contracts, diff/branch coverage, scans, targeted mutation/revert, baseline,
  assurance wiring/span topology and exact-candidate verification.
- Full mutation for T2 or scheduled runs is a separate required gate with
  resource/time budgets and retained evidence; targeted mutation cannot replace
  it. Validate meaningful assertions and reject missing/skipped bindings.
- Validated applicability rules; no no-tests PASS, HCL echo PASS or automatic
  dependency-update fixes. Upgrade vulnerable SDK toolchain with tests first.
- Exit: real subprocess/container gates for every supported language plus
  timeout, tamper, missing runner, frozen fixture and sandbox escape tests.

#### A08 — Real single-repo payments demonstration

- Build fictional orders/charges/refunds with persistence, ownership validation,
  exact money arithmetic, duplicate ≤24h/window ≤90d rules, idempotency and >500
  manual review. Confirm boundary inclusivity/currency semantics in its story.
- No real financial provider or customer data. Independent frozen cases cover
  49.99 success, 900.00 review/no payout, nonduplicate/expired/nonowner rejection,
  concurrency/retry, negative amounts and exact thresholds.
- Actual endpoint actions emit declared spans/metrics and immutable run/build
  identity; query receipt, not the service's own in-memory coverage assertion.
- Exit: whole factory-run single-repo story to running dev app with PR and
  sentence→artifact→observed-business-span trace. Direct wrapper emission alone
  does not satisfy this exit.

#### A09 — First usable tenant/developer surface and rehearsal

- OIDC onboarding, approved repository registration, safe idempotent init that
  preserves brownfield controls, backlog/refinement and status/evidence views.
- Authenticated, role/tenant-scoped read-mostly MCP; no approval/merge/deploy/
  secret tools on a developer workstation. Local gate results remain advisory.
- Minimal product admin/usage/approval interface and CLI/API parity.
- Exit: two simulated tenants run isolated jobs, one complete payments dev loop,
  crash-resume and kill-switch rehearsals. Produce an evidence bundle and list
  remaining Pass B requirements. **Not commercial GA or three-cloud delivery.**

### Pass B — complete commercial delivery, portability and qualification

#### B01 — Three-cloud adapter parity

- One versioned Deploy/Artifact/Secret/Identity/FeatureFlag contract and fixtures.
  Golden paths: Azure Container Apps + ACR; AWS ECS/Fargate + ECR; OCI Container
  Instances + OCIR where capabilities suffice. Use each provider's secret store,
  workload identity, health/ingress/TLS and network controls without leaking them
  into business policy. Managed/existing Postgres and object storage are inputs.
- Validate canary, private connectivity and workload-identity capabilities
  before selecting a runtime. If OCI Container Instances cannot meet a required
  feature, explicitly qualify an alternative profile; do not pretend equivalence.
- Terraform/OpenTofu licensing/distribution decision, pinned providers, state
  isolation/locking, drift detection, destroy plan and resource TTL/tagging.
- Exit: shared conformance tests AND a real create/deploy/health/rollback/cleanup
  rehearsal in each explicitly authorized cloud. Never count mocks as cloud proof.

#### B02 — Production multi-repo ChangeSets

- Versioned graph, exact bases/candidates, contract overlap, ordered merge/deploy/
  rollback, affected-evidence invalidation and protected provider merge operation.
- Durable `PARTIALLY_MERGED`, declared forward/revert policy, timeout escalation;
  partial merge never creates an eligible release. New repos/contracts invalidate
  approval and ordering evidence as specified.
- Implement typed refund SDK with real tests and meaningful infra; execute
  infra→service→SDK and an adverse partial-merge recovery scenario.
- Exit: three real disposable repositories, failure at every boundary, no
  duplicate merges or incompatible releases, auditable recovery after restart.

#### B03 — Build once, sign, promote, verify and recover

- Generate CodeModelSnapshot in the build itself; bundle exact image/SDK/IaC/
  migration digests, SBOM, provenance and signatures. Separately sign each
  DeploymentBinding with config/secret-version/flag references, never values.
- Verify every hop; environment-only changes receive their own policy decision.
  Production canary/rollback/fix-forward/flag kill switch with runtime identity
  and observed assurance/SLO evidence. Never rebuild between environments.
- Bind approved-intent and containment manifests to the bundle and exact build
  snapshot. In preproduction, verify observed behavior against intent, execute
  environment capability probes and induce failures to prove containment rules;
  an agent cannot access raw production evidence or override those controls.
- Exit: tampered/stale/signature/config substitution rejected; induced error and
  latency failures trigger rehearsed safe rollback; data survives migration and
  rollback scenarios; trace names the exact released source and configuration.

#### B04 — Commercial service and UX completion

- Tenant onboarding/offboarding, membership lifecycle, repository grants,
  approval queue, searchable timeline/evidence export, pause/cancel/resume,
  usage allocation and admin/support access with audited impersonation controls.
- Durable metering/reservations/quotas and manual-invoice export, not mandatory
  card collection. Retention/deletion/export/backup access policy; license,
  notices, terms/privacy/DPA and abuse/reporting/operator procedures.
- Reviewed UX skill/design system, keyboard/contrast/reduced-motion/viewport
  tests, accessibility and frontend performance budgets. Avoid building a
  general-purpose ALM replacement; link the user's existing boards.
- Mobile visual comparison uses version-qualified Maestro assertions or a
  declared Percy/Applitools-compatible image adapter, not mere screenshot upload.
  Baselines/thresholds/masks require independent approval, fixed device/locale/
  theme/text-size coverage and restricted artifacts. Native accessibility and
  realistic animation/permission behavior remain separate requirements.
- Exit: real OIDC two-tenant browser journeys; IDOR/CSRF/session/role-revocation,
  usage-race, quota and offboarding tests; zero unreviewed redistribution terms.

#### B05 — Portability proof and model qualification

- Extend A05's independent-family qualification and add a second ALM adapter (proposed
  Azure DevOps) early enough to expose GitHub assumptions. Real conformance runs
  require configured test-provider access; document unavailable capabilities.
- Capability qualification expiry, evaluation datasets, pass^k with trials and
  confidence intervals, cost/latency/repair/human-edit-distance metrics, retrieval
  miss rate and review quality. No claim of quality from five cherry-picked runs.
- Qualify review precision, escaped defects, false positives and closure behavior
  under ARP-1. More passes alone do not establish quality or justify higher spend.
- M0 deterministic routing always; hosted producer/reviewer first. Optional
  cheaper/self-hosted tiers route only after empirical qualification. A tenant's
  model choice cannot bypass required residency or family independence.
- Exit: recorded resolved models/context and current qualification evidence,
  bounded outages/escalation/budget tests, and real second-provider proof.

#### B06 — Brownfield and legacy qualification

- Inventory authorized NOK-Apps and claims repos at exact revisions. Discover
  stack/build/dependencies/contracts/data/telemetry/ownership without overwriting
  control files or exporting private material contrary to tenant policy.
- Reproducible-build/test/observability assessment with explicit unknowns; issue
  ordinary augmentation stories for gaps. Static + sanitized runtime facts;
  characterize intentional/unknown/defective legacy behavior separately.
- Complete required schema/IaC ownership, code-model and context extraction;
  deeper graphs or prose RAG only where an identified requirement needs them.
- Exit: evidence-backed readiness assessment and a non-destructive real change
  in each approved existing pilot; no inaccessible-repo guessing.

#### B07 — Full simulation/pilot matrix and safe extraction

- Execute the matrix below against actual local/provider systems as appropriate.
  Prove greenfield from empty repo and brownfield without lost tests/config.
- For an approved mobile target, execute [mobile exit criteria](MOBILE-TESTING.md):
  real iOS/Android flows, injected UI/visual defect, unchanged-test repair,
  real-backend assertions, production-package/OTA identity and adverse runner/
  evidence scenarios. No Expo target or device access means BLOCKED, not tested.
- Claims: preserve no autonomous approve/deny, synthetic claims, auditable human
  queue, tenant-isolated retrieval and safe model/provider failure handling.
- Legacy extraction: side-effect-isolated shadow, no duplicate business action,
  explicit match/error/latency budgets, approved overlap/cutover and proven return.
- Exit: complete evidence per required cell. A blocked app/cloud is not a
  qualified release. Never claim claims' existing Azure deployment was produced
  by this factory.

#### B08 — Production operations and commercial release

- Restore drill, tenant export/recovery, queue/worker failure, signing/key
  rotation/revocation, dependency/patch policy, observability/alerts, on-call
  playbook, incident communication, resource cleanup and cost guardrails.
- Load/noisy-neighbor/fairness/performance regression tests at the accepted
  capacity envelope; long jobs asynchronous. Adopt numerical SLOs/RPO/RTO before
  testing; do not invent a PASS while those requirements remain unspecified.
- Security review and independent high-risk approval; no unresolved critical or
  high findings. Distribute versioned installation/upgrade/rollback and support
  documentation with immutable release/evidence artifacts.
- Exit: all commercial launch requirements green, organizational/provider/legal
  prerequisites satisfied, TWO actual humans for this sensitive SaaS release.

#### B09 — Evidence-earned autonomy and optional expansions

- Operate at the approved autonomy level until measured failure/edit/review
  metrics justify expansion; activate only constrained T0 classes first.
- Temporal, GPU serving, embeddings/RAG, a graph database and additional runtime
  languages are not automatic launch dependencies. Where the full capability
  contract explicitly requires one, keep it OPEN until qualified or the user
  explicitly changes scope. A convenience deferral is not “all features done.”
- Exit: honest capability/support matrix and ongoing qualification record. Real
  longitudinal reliability and human-behavior evidence cannot be manufactured by
  two implementation runs; broad autonomy remains disabled until earned.

## 6. Required simulation matrix

| Level | Cases | Evidence / exit |
| --- | --- | --- |
| L0 deterministic | R01–R10; readiness, risk, immutable digest, time/identity/signature, parsers, quotas | Unit/property/mutation tests, no model/network dependency |
| L0/L3 agentic review | ARP-1 minimum/caps, doer repair, objective criteria, advisory/no-progress handling, lineage/replays, parent budgets and generated workflows | Rule tests with fakes, then qualified real-model runs; two completed passes never replace current evidence or authority |
| L1 actual local components | Postgres transactions/RLS/restarts; Git races/worktrees; sandbox tools; all language gates; OTel receiver | Real binaries/containers, exact revisions, cleanup receipts |
| L2 provider governance | Synthetic GitHub issue→test PR→implementation PR; stale heads/labels, malicious workflow, approval/ref/quorum errors, duplicate webhooks | Real scoped provider runs/status IDs and human approval evidence; no fake approvers |
| L3 real AI | Vague ask refuses to guess; complete ask; independent family; injected issue text; provider outage/rate limit; cost exhaustion | Exact model/context/prompt/tool manifest and usage, retained structured outputs, $20 aggregate cap |
| L4 applications | Payments rules/threshold/concurrency; SDK contracts; real empty greenfield; NOK-Apps/claims non-destructive brownfield | Actual endpoint/browser/DB assertions and observed business traces |
| L4 mobile (when applicable) | Android/iOS Maestro, mock and real API lanes, negative/permission/relaunch, approved visual baselines and injected defects | Actual native runner/build/device/flow/report identities; no simulator-to-store or screenshot-to-visual-PASS substitution |
| L5 release/three clouds | Same signed artifact on Azure/OCI/AWS; config drift, signature tamper, canary/SLO breach, rollback, migration/partial merge | Provider resource/run IDs, digest verification, health/rollback results, destroy receipt |
| L6 SaaS/security/operations | At least two tenants; cross-tenant API/worker/cache/storage/secret/retrieval denial; noisy neighbor, key rotation, restore, retention/deletion | Independent security evidence, accepted performance/RTO/RPO envelope, recovery proof |

Scenario outcomes are `PASS`, `FAIL`, `BLOCKED`, or explicitly policy-approved
`NOT_APPLICABLE`. Mock/simulation/provider evidence levels must be visible. A
missing runner/model/account never becomes PASS. Capture failures as well as
successes, and bind every result to the tested commit/image/config/policy.

## 7. External prerequisites — separate from code work

- Authorized NOK-Apps inventory/SSO and explicit disposable repo destinations.
- OIDC issuer, tenant placement/residency, secrets retention and permitted model
  egress; qualified producer plus second family with securely provisioned access.
- Azure subscription/resource group, OCI tenancy/compartment and AWS account,
  approved regions, scoped workload identities, DNS/cert and registry policy.
- Budget allocation/TTL/cleanup policy across providers. Stop before the $20
  ceiling; ask to change scope or budget rather than silently spending more.
- Second independent human for sensitive production, including factory SaaS
  control-plane release. GitHub accounts are not interchangeable with people.
- GitHub plan/ruleset/environment capabilities; administrator changes through
  reviewed approved operations, not bootstrap's blanket commit/push path.
- Commercial ownership/license/terms/DPA/support policy and numeric production
  SLO/capacity/RPO/RTO requirements. Manual invoicing is accepted; legal decisions
  and promised service levels must still be made by the owner.

## 8. Completion claims and two restart prompts

**Pass A complete** means the tenant-isolated single-repo dev workflow is
demonstrated with real gates, approvals where required, provider/model evidence
and observed business telemetry. It is a controlled pilot, not commercial GA.

**Commercial release complete** means every mandatory Pass A/B launch item and
required matrix cell passes on actual authorized systems; security, budget,
license, recovery and human-quorum prerequisites are satisfied. Mark remaining
optional/longitudinal qualification separately. Do not advertise unsupported
languages/providers or autonomous maturity.

Suggested next authorization (not a shell command):

> Execute Pass A of the saved delivery plan using the factory-readiness skill.
> Preserve user changes and frozen tests; work TDD through all unblocked packages,
> maintain the evidence ledger, and stop only at the listed consequential gates.
> No repository publication, production action or spend beyond $20 without approval.

Then:

> Execute Pass B from the same ledger. Complete the commercial, multi-repository,
> three-cloud and brownfield/greenfield simulation requirements. Keep unavailable
> capabilities blocked, retain evidence and cleanup receipts, and do not declare
> production ready until every mandatory exit and independent approval is met.

If interrupted, either authorization resumes from the ledger; it is not a reason
to rediscover the entire codebase, repeat past decisions or ask for each file edit.
