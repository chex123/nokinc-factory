# Factory readiness audit — 12 September 2026

## Verdict

**NOT READY for an end-to-end application pilot or commercial production.**

There is a useful, tested foundation, not a completed software factory. The
current source has no working conversation-to-deployment path. Green baseline
tests demonstrate existing contracts, not completeness of the product.

Call the product an **AI-assisted software-factory platform / SDLC control
plane**. Its ports and SDK can form an extensible framework, but a multi-tenant
service with workflow, authorization and release responsibility is a platform.

Use the [delivery plan](DELIVERY-PLAN.md) for the approved expanded scope,
remaining work, two execution passes and objective release criteria. That plan
does not claim the entire product can be built or qualified in two chat turns.

## Scope and evidence limits

- Reviewed all local implementation modules, tests, 12 workflows, bootstrap and
  verification scripts, manifests, instruction files and specifications across
  the factory, payments, TypeScript SDK and Terraform folders. Generated caches,
  installed dependencies and duplicate historical patches were not treated as
  source-of-truth implementation.
- Read the recoverable 36-turn factory session
  `273e4c15-5787-4e6c-8443-3b4cb3f2a6c2` from 25–26 August. Its stored title does
  not confirm the exact requested session name. Many pasted attachments and
  parts of assistant responses are absent. History establishes intent, not
  present runtime correctness.
- Queried current GitHub metadata and failing CI logs read-only. No fetch,
  checkout, repository commit/push, issue/PR creation, workflow dispatch, model
  API call, or cloud deployment was performed.
- Inspected future pilot repository metadata/manifests, not their entire
  external codebases. No NOK-Apps private inventory was available to the active
  identity. An empty accessible list is **not** proof the organization is empty.
- New artifacts are this report, the execution plan, an on-demand skill and
  opt-in simulation probes. Existing application/control code and frozen tests
  were not changed. Local installs/builds produced ordinary generated outputs.

## Exact starting point

| Component | Audited local state | Current remote evidence |
| --- | --- | --- |
| Factory | `feat/factory-preflight-core`, `08e9d7d5bffbc63896fd308bfeb9ab0d3c93da12`; two pre-existing modified unit-test files | `main` is `94fb336b50ad5c94fe97d165244c076a49fdb1e3`; PRs #1 and #2 merged; [PR #7](https://github.com/chex123/nokinc-factory/pull/7) open |
| Payments | `fix/dual-codeowners`, `7879ee8bd7f4f3ddd6f9d8c533e9a8c151d438f7`; initially clean | `main` is `2eff38b39ce7be836a7c4c78b6de2e4a2eb6eff3`; governance PR #2 merged |
| SDK / infra | Source folders, not initialized Git repositories | No coordinated-repository delivery proof |
| Workspace wrapper | Not a Git repository | Factory origin is [chex123/nokinc-factory](https://github.com/chex123/nokinc-factory) |

The pre-existing edits are in [candidate tests](../tests/unit/test_preflight_candidate.py)
and [TaskContext tests](../tests/unit/test_task_context_loader.py). Preserve them.

### What happened after the earlier session

The GitHub Issues work was subsequently merged. Preflight Slice A was committed
and pushed but remains an open PR. Its [failed CI run](https://github.com/chex123/nokinc-factory/actions/runs/32971938633)
passed 228 tests and then failed changed-line coverage: **89%, required 90%**.
The present local tree includes additional, uncommitted tests: **246 pass and
97% diff coverage**. Local results do not update that remote PR's failed evidence.

## Executed verification

| Check | Observed result | What it proves / does not prove |
| --- | --- | --- |
| Factory baseline | **246 passed**; fresh coverage run 57.80 s | Existing unit/frozen contracts pass; no full factory scenario exists |
| Factory acceptance subset | **10 passed** | State, impact and toolchain contracts, not delivery |
| Factory Ruff / strict mypy / compilation | **PASS**; mypy 23 source files | Static quality of implemented source |
| Current local diff coverage | **97%**, 7 missing of 288 changed source lines vs cached `origin/main` | Includes user's existing test edits; not remote/candidate release evidence |
| Payments baseline / acceptance | **6 passed / 4 passed** | Assurance-wrapper behavior; no refund business tests |
| Payments Ruff / strict mypy / compilation | **PASS**; mypy 3 source files | Used its own existing virtual environment |
| SDK locked install / build | **PASS / PASS** | `npm ci --ignore-scripts`, TypeScript builds |
| SDK `npm test` | **FAIL**, exit 1: no test files | CI currently hides this with `--passWithNoTests` |
| SDK dependency audit | **FAIL**: 5 affected packages (3 moderate, 1 high, 1 critical) | Development-tool advisories; not evidence of an exposed production exploit |
| Terraform format / initialize / validate | **PASS** | Only a built-in `terraform_data` placeholder; no cloud apply or deployment |
| Real GitHub TaskContext load | **PASS** for factory issue #5 | Provider identity and owned content digest resolved through existing loader |
| Added core readiness probes | **10 FAILED**, exit 1 | Reproduced missing functionality/security properties, not intentionally suppressed failures |
| Actual review-workflow JavaScript with provider doubles | **1 passed, 4 FAILED**, exit 1 | Positive control passes; unsafe scenarios reproduce false success without GitHub/model writes |
| Runtime telemetry simulation | **PASS**, four spans received in real Jaeger | Direct synthetic wrapper emission only; NOT refund execution or factory orchestration |
| Refund API smoke | **404** for both 49.99 and 900.00 requests | Endpoint and manual-review demonstration are unimplemented |
| Live model / three-cloud release / tenancy tests | **NOT RUN / unavailable** | No usable model credentials, named cloud sandboxes or relevant implementation |

Pylance reported informational hints, not blocking diagnostics. Installed Python
environment audits also flagged the old `pip` toolchain; editable local packages
are not auditable on PyPI. This is separate from auditing a reproducibly resolved
production dependency set. Do not upgrade dependencies blindly during an audit.

### Reproducible commands and important environment distinction

Run the factory's `ruff check src tests`, `mypy --strict src`, `pytest -q` and
`pytest tests/acceptance -q` using its existing virtual environment. Use the
payments virtual environment for payments, not the interpreter selected for the
wrapper workspace. The latter selects factory dependencies and initially caused
missing FastAPI/OTel/stub collection/type errors. No application fix was needed.

On Windows, Git Bash must precede WSL Bash on the process PATH for the shell
fixtures. Node was 24.14.0, npm 11.9.0, Terraform 1.15.5, Python 3.12.10. These
local versions are not a reproducible production toolchain lock.

Opt-in reproducers:

- [Core probes](../.github/skills/factory-readiness/scripts/readiness_probes.py)
  run explicitly with pytest; not included in the pre-existing baseline suite.
- [Workflow probes](../.github/skills/factory-readiness/scripts/workflow_probes.mjs)
  run with Node's built-in test runner against the actual workflow script.
- [Telemetry smoke](../.github/skills/factory-readiness/scripts/telemetry_smoke.py)
  has separate `emit` and `verify` modes. Export submission is not indexed receipt.

Final probe SHA-256 identities:

- Core: `7dbe00c5b6e7f27f7039cced8e64c5db76f709c243dda9dcf1a8dff86bfd3f00`.
- Workflow: `7b8a183d3d5047ad222c065998a0d2fe7eed235c2982766d3a537c235e96f610`.
- Telemetry: `9eb7870af59d8b4401c60c0bea4edc2451def07b526434df25300d5666179718`.

Planning review added explicit assurance/containment ownership, full mutation,
Pass-A independent-family qualification, and stronger positive/negative probes.
This review is not a production-qualified cross-family semantic review.

### Runtime evidence

An owned, resource-limited Podman Jaeger container exposed random loopback ports.
ASGI requests returned health 200, initial coverage incomplete and refunds 404.
Undeclared spans were rejected. All four declared spans were then emitted
directly with synthetic data and independently queried from the receiver.

Final synthetic work item: `AUDIT-SYNTHETIC-CFEA0E14-FINAL`.

- Received: `refund.audit`, `refund.check_duplicate`, `refund.issue`, `refund.validate`.
- Each received span's `work_item.id` matched the test run.
- Trace IDs: `1ef6ff2eed32dadf58a111811236566f`,
  `4244940f84ae25d0229f876f3ccf59b9`,
  `d42dd009fe4ae17248bd50d9ab50a66d`,
  `e300b052fb47b4897b8044804338797f`.
- Local image ID: `43cfb436ca366f69ba8c59344565b417369d0eb53ed6388127ba238ea08a4625`.
- Initial immediate search returned zero because receipt/indexing was not yet
  visible. The corrected reusable smoke separates emission from receiver
  verification; both final modes were executed. No sleep/retry loop hid this.
- The disposable container was stopped/removed. No existing containers, volumes
  or images were removed. No paid model/cloud usage was initiated.

## Where the implementation is today

| Area | Status | Evidence / gap |
| --- | --- | --- |
| Business/solution/state/identity schemas | Partial | [Domain schemas](../src/nokinc_factory/domain/story.py) exist; semantic readiness, immutability and provenance enforcement are incomplete |
| GitHub Issues integration | Implemented foundation | [Adapter](../src/nokinc_factory/adapters/github_issues.py) with strict parsing and conservative capabilities; optimistic updates, not durable atomic workflow |
| Preflight + task capture | Partial, unmerged slice | [Git capture](../src/nokinc_factory/adapters/git_candidate.py) and [issue loader](../src/nokinc_factory/adapters/github_task_context.py); no executing gates/reviewer/repair pipeline |
| Deterministic impact | Partial, defects reproduced | [Classifier](../src/nokinc_factory/policy/impact.py) can incorrectly emit cosmetic-only evidence |
| Approval/security controls | Partial | Models + Stage-0 workflows, not verified approval service or credential broker |
| CLI / usable workflow | Missing | All four [handlers](../src/nokinc_factory/cli.py#L22-L50) are stubs |
| Agent roles | One builder only | [Domain Expert](../src/nokinc_factory/agents/domain_expert.py); no Architect/Test Author/Implementer/Judge orchestration |
| Durable workflow / event log | Missing | No Postgres execution state, inbox/outbox, leases or reconciler |
| RI / retrieval / MCP / onboarding | Missing | No indexers, ContextPacks, read-mostly MCP server or `factory init` |
| Gates / independent review | Partial | CI exists; [toolchain port](../src/nokinc_factory/ports/toolchain.py) has no runner; important gates absent or false-green |
| ChangeSets / release / rollback | Schemas or absent | No coordinated merging, artifact builds/signing/promotion, deploy adapters, recovery or end-to-end trace |
| SaaS product | Missing | No tenant identity/isolation, admin/product UI, quotas, metering, audit operations or commercial lifecycle |
| Demo | Scaffold | Health + assurance endpoints, SDK health wrapper, inert Terraform data resource |

Do not assign a feature-completion percentage from LOC, test counts or schema
counts. The usable vertical workflow is not yet complete; most of the newly
requested SaaS and multi-cloud scope has no implementation.

## Prioritized findings

### A. Reproduced failures — repair before integrating the foundation

| ID | Failure | Existing source | Required regression |
| --- | --- | --- | --- |
| R01 | Ordinary code plus a cosmetic file becomes COSMETIC only, in either order | [impact aggregation](../src/nokinc_factory/policy/impact.py#L235-L274) | Union per-file impacts; retain affected test invalidation |
| R02 | An executable statement after a docstring is treated as cosmetic | [cosmetic detection](../src/nokinc_factory/policy/impact.py#L189-L194) | AST-backed or conservatively noncosmetic classification |
| R03 | Empty approval reference exits a protected lifecycle state | [transition check](../src/nokinc_factory/domain/states.py#L109-L122) | Reject empty IDs; execution separately requires verified bound evidence |
| R04 | Future-issued authorization and exact expiry are accepted | [revalidation](../src/nokinc_factory/domain/authorization.py#L176-L188) | Enforce issued-at <= now < expires-at and staleness |
| R05 | BusinessReady accepts unresolved blocking questions | [readiness model](../src/nokinc_factory/domain/story.py#L54-L91) | Refuse unresolved required facts before readiness |
| R06 | TaskContext body can mutate while retaining its old digest | [models](../src/nokinc_factory/domain/preflight.py#L51-L88) | Deep immutability/validated rebuild; consumer-side verification |
| R07 | Configured Git text conversion executes and hides a real changed patch | [capture commands](../src/nokinc_factory/adapters/git_candidate.py#L94-L110) | Disable helper/config-driven execution; preserve raw reviewed bytes |
| R08 | Review grants success to head B after gates tested head A | [review SHA selection](../.github/workflows/cross-model-review.yml#L35-L49) | Bind gate run, immutable diff and status to identical validated candidate |
| R09 | Missing task, unresolved context gap, or unexpected resolved model still accepts | [review retrieval/policy](../.github/workflows/cross-model-review.yml#L94-L134) | Require complete authoritative TaskContext, model qualification and independent family |
| R10 | Status command returns not-built exit 2 | [CLI](../src/nokinc_factory/cli.py#L40-L50) | Implement the usable application layer, not just parser tests |

These map to 14 failing probe cases; R01 and R04 are parametrized and R09 has
three separate workflow cases. They remain intentionally visible failures until
implementation, not `xfail` or omitted launch criteria.

### B. Static/review findings — treat as production blockers, validate in Pass A

1. **Untrusted execution and secrets:** a same-repository PR can change its
   workflow before merge. A repository-level model secret and mutable required
   job names do not create a trusted execution boundary. Separate the signed,
   base-trusted verifier/credential broker from PR code; pin required issuers.
2. **Approval evidence:** the approval workflow validates digest syntax, not
   the approved content, identity history, expiry, revocation or exact trusted
   workflow/ref. Authorization signatures are fields, not verified signatures.
3. **Frozen-test bypass and red-first deadlock:** unlabeled PRs escape the path
   restrictions; label changes do not retrigger checks. Meanwhile a legitimate
   tests-first PR is required both to fail on baseline and pass the same new
   acceptance behavior. Any pytest failure, including collection failure, is
   treated as valid red evidence. Red-contract and green-implementation lanes
   need separate, authoritative policies—not skips controlled by PR text.
4. **Durability:** concurrent lifecycle writers, multi-label crash windows,
   duplicated create operations and lost acknowledgements lack durable
   deduplication/outbox/reconciliation. Detection is not transactionality.
5. **Snapshot correctness:** capture reads HEAD/index/files separately; internal
   races, symlink/mode/submodule handling, child hashes and immutable evidence
   validation need dedicated tests. Advisory capture is not a merge candidate.
6. **Incomplete gates:** SDK permits no tests; HCL checks echo success. Mutation,
   meaningful BDD binding checks, SAST, license/contract/IaC policies and exact
   merged-candidate gates are not implemented as governed capabilities.
7. **Packaging/reproducibility:** PydanticAI is development-only; the advertised
   runtime install does not contain the agent. Payments loads its span contract
   relative to working directory. Python dependencies/actions are not fully
   locked; the SDK does have a lockfile, but its dev tools need reviewed upgrades.
8. **Operational/commercial:** no tenant security, durable audit, backups,
   restore drill, SLO qualification, supply-chain release evidence, budget
   breaker or production recovery. Public source without a license is not a
   commercial redistribution grant.

## Actual GitHub controls and administrative work

- Factory and payments enforce strict required checks, stale-review dismissal,
  codeowner review, last-push review and administrator branch protection. Current
  required PR approval counts are **1 / 2**, respectively.
- Factory required check issuers are GitHub Actions app ID `15368` for three
  checks; `cross-model-review` has **null app ID**. An app identity alone does
  not bind a check to an immutable trusted workflow implementation.
- Factory has **zero environments**. Payments has **gate-1 through gate-4**;
  each has a required reviewer and `prevent_self_review=true`, but also
  **`can_admins_bypass=true` and no deployment-branch restriction**.
- The user confirms only one actual person is available. Multiple accounts do
  not establish two distinct humans. High-risk production remains blocked.
- [Verification script](../scripts/verify.sh#L253-L268) does not validate all
  environment properties; a script PASS would not close these findings.
- Do not rerun [bootstrap](../scripts/bootstrap.sh#L273-L279) blindly: it stages,
  commits and pushes current trees. Governance repair needs reviewed changes,
  explicit administrator action and fresh remote verification.
- Payments' [project metadata](../../nokinc-demo-payments/.factory/project.yaml)
  names `nokinc/nokinc-demo-payments`, not its actual `chex123` repository.

## Pilot applications and blocked validations

1. **NOK-Apps:** obtain organization/SSO authorization and repository inventory;
   brownfield suitability cannot be judged from an inaccessible list.
2. **Payments:** good synthetic first demonstration; not implemented yet. Prove
   duplicate/window/ownership/idempotency rules and >500 manual review with
   real endpoint, database, SDK and observed business traces.
3. **[Governed claims](https://github.com/chex123/governed-claims-agent):** existing
   React/TypeScript + FastAPI application with Azure deployment history, not a
   blank greenfield project. Azure Search/Blob/identity coupling needs discovery.
   Declared constraints prohibit autonomous claim approval/denial; synthetic
   data and controlled human decisions remain mandatory. Inspect that full
   codebase and rights before onboarding or modification.
4. **True greenfield:** use an approved empty disposable repository in addition
   to these existing projects. A functioning, already-deployed claims app does
   not prove from-zero creation.

No local model key was present for OpenAI or an independent second provider.
Azure and Oracle Cloud CLIs are available; AWS CLI was not found. Credentials
alone would still not authorize a cloud destination. No subscription/account/
compartment or region was designated; the **$20 total cap** is not a guarantee
that three-cloud live qualification fits that amount. Record blocked cells,
never convert them to PASS or silently increase spending.
