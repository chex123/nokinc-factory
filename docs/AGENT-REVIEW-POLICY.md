# Agent review policy — bounded doer/reviewer cycles

**Policy ID:** ARP-1. **Decision date:** 12 September 2026.
**Status:** user-required direction with recommended defaults. Local policy,
PostgreSQL storage and bounded coordination are implemented and tested; live
model qualification, generated workflows and complete service integration remain
open. See the [delivery ledger](DELIVERY-LEDGER.md); no production claim is made.

## 1. Standard and scope

Every agentic workflow the factory **runs or generates** uses a doer, another
model as reviewer, and evidence-backed reconciliation by the doer. A reusable
review capability serves all roles; do not create a new agent subsystem per role.

A review unit is a bounded semantic artifact or milestone: a story, design,
independent test suite, implementation, release plan, analysis or customer-agent
output. It is not each token/tool call, and review reports do not recursively
create review-of-review workflows. Define units before execution; splitting,
handoffs or new versions cannot reset parent limits.

Purely deterministic work stays M0: use code and tests, not artificial model
conversations. If a model produces documentation, a dependency change or another
previously excluded artifact, its semantic output still receives this policy.

Generated workflows must enforce review at their own semantic publication and
consequential-action boundaries. Reviewing their template does not review future
outputs. Stream progress/status separately; hold substantive final output until
review completes. A runtime unable to enforce this is unsupported, not compliant.

Review proposals **before** irreversible actions. Doers work on drafts or in
isolated sandboxes; a trusted broker executes only after deterministic policy and
required human authority permit it. Post-action verification never repairs a
missing authorization or justifies repeating a side effect.

### Relationship to the frozen specification

ARP-1 is the scoped delivery amendment to [specification v2.5](factory-spec.md):
it replaces Part 7's one-default/two-T2 rounds and artifact exclusions with a
minimum of two reviews for every agentic review unit. It also makes Part 11's
three-cycle ceiling a recommended default, configurable only through bounded,
authorized policy. It preserves the three implementation-repair ceiling, repeated
failure stop, frozen tests, evidence binding, model independence and human gates.

The CLOSED specification and deployed controls have not been edited. A00 must
ratify the amendment and its independent contracts; A05–A07 implement it before
any compliance claim. This document does not authorize bypassing current controls.

## 2. Ground both roles in a QualityContract

Pin the following before the first doer run. Neither role may move the goalposts.

| Contract element | Required grounding |
| --- | --- |
| Objective and bounds | Original request, business value, included/excluded behavior, affected artifacts and permitted tools |
| Design reference | Approved C4 views where applicable, interfaces/schemas, dependency rules, security and business invariants |
| Criterion ledger | Stable criterion IDs, mandatory/advisory status, applicability, observable pass condition and required evidence |
| Verification | Frozen acceptance/contracts, deterministic gates and source/configuration identities; assertions must prove behavior |
| Performance/quality | Agreed numerical latency/load/resource targets where applicable; otherwise an explicit justified no-change/not-applicable decision |
| Policy | Tenant, risk tier, residency/egress, independence, severity rules, freshness, authorizations and finite execution budgets |

C4 is the architectural reference, **not the sole correctness oracle or a
candidate implementation**. Reuse context/container views; include component and
code-level detail only for the affected slice. Requirements and frozen tests
remain authoritative for behavior. Test Authors may see baseline source/design,
not the candidate implementation before their independent tests are frozen.

Version and hash the contract. Design amendments require the existing appropriate
authority, new identity and dependent-evidence invalidation—not unilateral edits
to make a candidate pass. Do not invent architecture for BusinessReady before the
Architect runs, or demand C4 diagrams for a non-design analysis task.

### Common quality dimensions

1. **Correctness/completeness:** every mandatory criterion has relevant evidence;
   negative, boundary and failure cases are covered, not merely a happy path.
2. **Architecture/scope:** respect approved boundaries, dependencies and contracts;
   no unnecessary abstractions or unrelated refactors.
3. **Security/privacy:** identity, tenant/data isolation, least privilege, inputs,
   secrets, logging and authorized effects meet declared cross-cutting invariants.
4. **Tests/evidence:** real assertions, frozen contracts intact, correct baseline,
   current candidate and truthful distinction between mocked and live evidence.
5. **Performance/reliability:** measure against agreed budgets; no unmeasured
   “optimization” or speculative complexity justified only by reviewer taste.
6. **Operability/maintainability:** required telemetry, recovery, clear ownership,
   and approved conventions; factual analysis uses cited sources and uncertainty.

For each dimension use `PASS`, `FAIL`, `UNKNOWN`, or policy-approved
`NOT_APPLICABLE`. Qualitative criteria need observable predicates, not an invented
score. An average “9/10” cannot compensate for a failed security or correctness
criterion. Missing required facts cause clarification, not a guessed threshold.

## 3. Meaningful passes, not endless refinement

1. **Doer:** produce the bounded artifact and run applicable deterministic checks.
2. **Review 1 — contract audit:** inspect all mandatory criteria, scope, risks and
   evidence; report substantive findings together, not piecemeal style advice.
3. **Doer:** fix accepted findings minimally or provide a concise factual rebuttal.
   Rerun affected gates and record new artifact/evidence digests.
4. **Review 2 — closure and regression:** independently check the current artifact,
   verify resolutions, inspect changed/adjacent risks and confirm criterion
   coverage. Do not relitigate preferences already settled without new evidence.
5. **Policy:** accept only when all exit conditions hold; otherwise use the
   remaining pre-authorized capacity for another repair/review or stop.

If Review 1 finds no defects, do not invent an edit: Review 2 challenges evidence
and checks regressions/omissions against the same artifact. Two clean reports can
have identical conclusions; they must be genuine distinct review invocations.

| Parameter | Recommended default / invariant |
| --- | --- |
| `min_review_passes` | **2**; configurable upward, never below 2 for an agentic unit |
| `max_review_passes` | **3**; finite configured value, at least the minimum, within an authorized organization ceiling |
| `max_implementation_repairs` | **3**; separate from reviews and subject to the earlier repeated-failure stop |
| `same_unresolved_failure_limit` | **2** occurrences of a normalized unresolved blocker/failure; then halt/escalate |
| Call/time/token/cost limits | Required finite per-unit and parent-workflow ceilings, reserved before execution; no unlimited default |
| Additional tiers/reviewers | Allowed only by the planned route and remaining budget; no counter reset on model change |

Two passes are an **acceptance minimum**, not an instruction to continue through
a security stop, outage or exhausted budget. They mean two completed valid
reviews across the artifact's version lineage, not two clean votes on old content.
The latest required review must accept the exact final artifact/current contract
and evidence. A tenant may additionally require multiple distinct reviewers of
the final digest; that is separate from review-pass count and human quorum.

Cached/replayed reports cannot increment passes. Malformed, missing, incomplete
or unavailable reviews do not count as completed, but still consume invocation,
elapsed-time and spend allowances. Outstanding findings remain open even if the
next report omits them. Carry evidence forward only through deterministic impact
and freshness validation; never reuse a prior acceptance blindly.

A repair after the last permitted review leaves an **unreviewed, ineligible**
artifact even if a repair allowance remains. Plan capacity accordingly; exhausting
limits returns `BLOCKED`/`ESCALATE`, never automatic acceptance. Persist counters
and budget reservations transactionally across concurrent children/restarts;
contract changes, new run IDs and reparenting do not refill them automatically.

## 4. Independence and the flagship/economical hierarchy

- Resolve a reviewer model different from the doer; independent family is
  recommended for all work and mandatory for T2. A different endpoint or prompt
  is not proof of a different model/family. Missing qualification is unavailable.
- Track every contributing producer in the artifact lineage. A reviewer is
  read-only: if it edits, it becomes a producer and cannot independently review
  its own contribution. Handoffs/risk changes revalidate reviewer eligibility.
- Share the same authoritative TaskContext/QualityContract; retrieve ContextPacks
  independently. Do not share private producer reasoning or self-assessment.
  Closure review can use factual findings, changes and rebuttal evidence.
- Two passes do not require two reviewer models beyond the independent reviewer,
  unless the tenant's risk policy demands it. Fresh inspections and distinct
  purposes matter more than multiplying model calls.
- Flagship architect/reviewer establish the narrow design; qualified economical
  doer/reviewer pairs implement it. Within each unit, findings return to that
  unit's original doer; a flagship reviewer reports findings, not replacement
  patches. If the doer cannot complete repairs within limits, halt/escalate.
  A predeclared or separately authorized flagship integration unit may have a
  flagship doer and independent reviewer. It is not an automatic repair-loop
  reset: preserve all contributing producer lineage and parent limits. Do not
  replace a unit's doer automatically to evade failed repairs or review findings.
- Qualification depends on measured quality/cost/residency, not GPU memory size.
  Keep work on the cheapest qualified tier; escalation cannot bypass an exhausted
  budget, repeated-failure stop or unavailable independent reviewer.
- Enforce residency/egress before retrieval/model calls. Model review never
  grants tools, bypasses frozen tests or substitutes for required human approval.

## 5. Finding contract and anti-nitpick rules

A finding includes a stable fingerprint, criterion/invariant ID, severity,
affected artifact digest/location, triggering scenario, precise evidence,
observable consequence, smallest recommended correction and verification step.
Distinguish proven defects from evidence gaps; a confidence number is not proof.
Use deterministic tests/queries for claims they can establish.

| Finding | Treatment |
| --- | --- |
| Failed mandatory criterion, any severity | Blocks acceptance until corrected or resolved through authorized contract change; `LOW` cannot waive a requirement |
| Substantiated CRITICAL issue | Block; follow existing security escalation; no agent risk acceptance |
| Substantiated HIGH/MEDIUM issue | Repair within limits; unresolved/disputed material findings escalate with evidence |
| Missing mandatory evidence or material uncertainty | `UNKNOWN`/`CONTEXT_GAP`; retrieve within budget or stop, never invent a code defect |
| Serious newly discovered risk beyond the contract | `CONTRACT_GAP`; stop for authorized clarification/amendment, not a silent new requirement or dismissal |
| Nonmandatory style/preference/optional improvement | Advisory; no forced edit, no extra repair cycle, optional separately scoped backlog item |

- Reviewers do not demand renamed variables, alternate patterns, new frameworks
  or speculative speedups merely because they prefer them. A declared convention
  violated by the artifact is different: cite its rule and relevant evidence.
- No finding quota. “No material findings” is valid. Report all material findings
  within the response budget; if inspection/output is incomplete, say so and
  block completeness rather than silently truncate or claim PASS.
- Doer resolutions are `FIXED`, `REJECTED_WITH_EVIDENCE`, or
  `DEFERRED_ADVISORY`. A doer cannot self-close a material dispute; policy and the
  appropriate independent authority adjudicate it. Fixed tests are rerun.
- Deduplicate by underlying criterion, affected behavior and cause, not wording,
  line number or reviewer identity. Fingerprints persist across paraphrases and
  model swaps. Duplicate delivery is not another failure occurrence.
- Repeated unresolved blockers/real failures trigger the two-occurrence stop;
  repeating an advisory suggestion or an already resolved finding does not.
  Reopen resolved findings only with changed artifacts or genuinely new evidence.
- Later passes may still discover real correctness/security defects. The rule
  against moving goalposts must never suppress them; use the contract-gap path.
- No endless perfection loop: once the minimum reviews and all mandatory exits
  are satisfied, accept the scoped result. Optional enhancements are separate work.

## 6. Acceptance and escalation

`PASS` requires the configured minimum completed reviews, eligible independent
model(s), latest acceptance of the final artifact, every mandatory criterion/gate
with current evidence, no unresolved blocker, consistent contract/context and
available budget for any remaining authorized action. Bind tenant, work item,
artifact, source/config, policy, model, gate and proposed action identities.

`PASS` here means **eligible for the next governed stage**, not permission to
merge, release or act. The broker revalidates current target/digest, freshness,
revocation and any human quorum immediately before a consequence.

Stop on repeated failure, no material progress, exhausted capacity, conflicting
requirements, missing independent reviewer/evidence, or forbidden scope/tool
use. Return the smallest blocker/evidence packet and a next action; do not start
another autonomous reviewer to judge the reviewer indefinitely. Human adjudication
does not silently replace minimum model reviews or mandatory machine gates.

## 7. Implementation and qualification criteria

These are **required future conformance scenarios**, not tests already passing:

| Scenario | Required result |
| --- | --- |
| One clean review; minimum is two | Not eligible yet |
| Two genuine clean invocations; unchanged artifact | Eligible without a gratuitous edit |
| Review finds defect, doer repairs, second review validates final evidence | Eligible if all other controls pass |
| Same report redelivered or cached response reused | No extra completed pass |
| Malformed/unavailable review; retry cap reached | No completion credit; stop within consumed budget |
| Repair after final permitted review or artifact changes after acceptance | Ineligible; stale review cannot authorize new content |
| Mandatory criterion labeled LOW | Still blocking |
| Optional style suggestion repeats | Nonblocking; no forced repair/escalation loop |
| Real unresolved blocker repeats under new wording/model/run ID | Halt on second occurrence; counter survives restart |
| Serious late defect outside current criteria | Contract-gap escalation, not suppression or scope creep |
| C4/contract changes or upstream artifact invalidates downstream evidence | New version/authority and current dependent verification |
| Reviewer edits, producer changes, T2 independence absent | Revalidate lineage eligibility; block self-review/unqualified independence |
| Concurrent children/model escalation exceed parent allowance | Atomic reservation denies work; no multiplying budgets |
| Frozen test needs correction | Separate authorized Test Author change; Implementer cannot weaken it |
| Generated agent streams unreviewed conclusions or executes before authority | Block publication/action; template review is insufficient |
| Replayed approval/current target changed after review | Broker refuses consequence despite model acceptance |

A00 owns amendment-specific contracts; A04 owns durable counters/reservations;
A05 owns quality/context/model identities; A06 owns the shared review state
machine and generator contract; A07 owns gates/broker conformance; B05/B07 qualify
real models and generated workflows. Use fake reviewers to test rules, then real
qualified models and provider systems before runtime readiness claims.

Measure confirmed-finding precision, escaped defects, false positives, reopened
findings, human overrides, first-pass gate rate, cycle counts and cost/latency per
accepted artifact. Compare policy/model versions with recorded evaluation trials.
More passes are a policy choice, **not a statistical guarantee of correctness**.
