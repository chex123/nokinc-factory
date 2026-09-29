# Delivery execution ledger

**Started:** 12 September 2026. **Overall:** implementation in progress; NOT a
commercial-ready release. This ledger supplements, rather than rewrites, the
[historical audit](READINESS-2026-09-12.md) and [delivery plan](DELIVERY-PLAN.md).

## 29 September authorization update

- The user explicitly superseded the previous five-turn extension and requested
  uncapped production testing. The target is
  `FACTORY_CHAT_MODEL_TURN_LIMIT=unlimited`; this removes the application-level
  tenant turn ceiling and does not create a provider-spend ceiling.
- The live ECS task remains at revision 18 with limit 2 until the source change
  is reviewed, merged, and deployed through the protected `pilot-deploy` path.
  No provider call or production deployment is implied by this ledger update.

## 27 September continuation

- AWS CLI was reauthenticated as IAM user `chexudeze` (account
  `441186133046`); no root identity was adopted. The live Factory remains ECS
  revision 18 on digest
  `sha256:551a903d61735f253e0c4c0387bfa0380577070d2abca9047b09dc68ceedb513`,
  ECR scan `COMPLETE` with zero findings, and `FACTORY_CHAT_MODEL_TURN_LIMIT=2`.
- CloudWatch inspection of the two old 502 windows found only Uvicorn access
  lines and no `ERROR` entries. Local service changes now log only
  `failure_stage`, exception class, and an allowlisted diagnostic code (for
  example `HTTP_AUTHORIZATION`); provider bodies, exception text, prompts, and
  tokens remain excluded. Focused deployment/provider diagnostic tests:
  **20 passed**. These diagnostics are not in the live image yet.
- The user authorized **five additional chat turns** (ten provider calls). The
  local deployment workflow can set cumulative limit 7 while preserving the
  two historical reservations. The live task remains at limit 2; no new model
  calls were made. The service counts turns, not dollars, so the authorization
  has no hard USD ceiling.
- Added `scripts/deploy_pilot.py`, which requires an immutable ECR repository
  with scan-on-push, rejects tag reuse, builds linux/amd64, waits for a scan of
  the exact digest, blocks CRITICAL/HIGH/UNDEFINED findings, pins the image in
  the ECS task definition, requires circuit-breaker rollback, and verifies the
  final stable rollout. Mocked gate tests: **6 passed**. The direct
  `pytest.exe` invocation cannot import the non-installed `scripts` package on
  this Windows setup; `python -m pytest` works and is now used by CI. Strict
  mypy and Ruff passed. The workflow/skill remain local and unpushed.
- Added manual main-only `.github/workflows/deploy-pilot.yml`, with pinned
  actions, tests, protected `pilot-deploy` environment, GitHub OIDC, and the
  scan-gated script. Created IAM OIDC provider and
  `pilot-factory-github-deploy`, trust restricted to the exact repository,
  environment, and STS audience. IAM simulation allows only the target ECR
  repo, Factory service/task family, and existing task/execution roles; an
  unrelated ECR repo, ECS service, and Admin role are denied. The role ARN is
  stored as repository variable `AWS_PILOT_DEPLOY_ROLE_ARN`.
- Configured GitHub environment `pilot-deploy` with reviewer `triplexapps`,
  self-review prevention, protected-branch policy, and administrator bypass
  disabled. This verifies account-level protection only; distinct human
  ownership of `triplexapps` is not verified. The IAM user remains in the
  Administrators group and can bypass the workflow through direct AWS APIs;
  reducing that identity's privileges is intentionally not performed.
- Approval for Factory gates remains disconnected: the existing organization
  App cannot access personal repo `chex123/nokinc-factory`; there is no
  canonical issue binding, dispatch adapter, run/status verifier, or
  Environment-review verifier. Runtime/e2e asks remain fail-closed because no
  isolated worker is connected.
- Two earlier full-suite attempts were interrupted at 1,689 passed / 40
  skipped. The final isolated run completed: **2,393 passed, 41 skipped** in
  417.57 seconds. Frozen acceptance passed **10/10**; Ruff passed; strict mypy
  passed for 83 source files. No Git commit, push, branch, or workflow dispatch
  was made.

## Current verification checkpoint — 26 September 2026

- Factory on Windows/Python 3.12.10: full suite **2,378 passed, 41 skipped**;
  frozen acceptance **10 passed**; Ruff and strict mypy (**82 source files**)
  passed. The full-suite skips include environment-dependent PostgreSQL cases;
  local success is not hosted-runner provenance.
- Backend on the disposable PostgreSQL test database: full suite **342 passed,
  30 skipped across 50 passed / 2 skipped files**. The test-only container was
  stopped and removed; persistent backend development volumes were untouched.
  After that full run, the production Dockerfile was hardened with
  `NODE_ENV=production` and `ENABLE_DEV_SEED=false`; its focused image-contract
  tests pass. The production image builds locally from Node `v20.20.2`, pinned
  to the linux/amd64 manifest digest, and runs as UID 1000 (`node`) with the
  production defaults verified. It has not been pushed, ECR-scanned, or
  deployed. The Blnk E2E was not rerun after the final dependency upgrades.
- Factory production: ECS revision 18 is PRIMARY and rollout `COMPLETED`, with
  one desired/running task and circuit-breaker rollback enabled. Its image is
  `pilot-chat-multirepo-backend-allowlist-20260927-01`, digest
  `sha256:551a903d61735f253e0c4c0387bfa0380577070d2abca9047b09dc68ceedb513`;
  ECR scan by digest is complete with zero findings. Tenant `00001` and the
  durable two-turn cap remain unchanged.
- The authenticated live repository endpoint returns SDK, frontend, and private
  backend metadata; the refreshed chat session is ready and displays all three.
  Traces for both earlier live requests contain `INTAKE` and
  `CHAT_TURN_RESERVED`, with no `CHAT_TURN_RECORDED`. Both returned 502. The
  turn cap is exhausted; no provider call was retried.
- All eight approval Environments were read-verified with their exact reviewer,
  self-review prevention, and protected-branch policy. No workflow was
  dispatched. The organization-owned App does not cover the personal approval
  repository; the Factory API still lacks issue binding, dispatch, run/status,
  and Environment-review verification.
- Runtime/e2e requests remain fail-closed because an isolated worker is absent.
  Full SDLC orchestration and provider-backed approvals are not connected.
  ECR/ECS changes were made, but no Git commit, push, branch, or PR was made.

**20 September continuation:** the user authorized continued completion, including
the explicit task-identity redesign requested after the bounded review stop.
The revised `Factory-Task` contract replaces prose guessing; **53 workflow
simulations pass**. Review 1 found a lone-CR duplicate-marker case; two tests
failed before its correction, and review 2 closed this specific redesign.
Historical attempt/results below are retained. No commits, pushes, propagation
to targets or production operations occurred. Wider A02 controls remain open.

**Current verified checkpoint (20 September):** the latest complete run shows
**2,320 tests passed, 0 failures/errors, 1 environment-dependent skip**, including
31 integration cases, 10 frozen acceptance cases and the new service/workflow,
release, RI, inbox/outbox, MCP, model and toolchain tests. The current workflow simulations are **54 passed
/ zero failures**. Ruff and strict mypy pass (72 source files), and Git whitespace
validation passes. The run used a disposable PostgreSQL 16 owner/worker pair;
that container was removed after verification. This is local evidence, not signed
pipeline provenance.

The new baseline gate remains **pending independent review** because the last
delegated review/execution request hit the monthly quota. Local green tests do
not waive that requirement or establish production readiness. The selected
evidence report predates no current Python source/test/config changes: file
modification times precede its recorded run window. This is local verification,
not signed pipeline provenance. No verification container remains running.

**Continuation checkpoint (20 September, after the saved factory run):** the
exact [39-item production checklist](PRODUCTION-CHECKLIST-39.md) is now tracked
in-repository. No item is complete end to end yet: **23 partial, 7 not started,
9 blocked**. New local evidence includes the authenticated FastAPI boundary and
tenant-scoped durable workflow store, bounded `run-gate` execution, repository
intelligence, Ed25519 release/binding signatures, and the payments vertical slice.
The payments target now has durable PostgreSQL orders/refunds, a Terraform local
deployment manifest, SDK contract tests, and a simultaneous Podman Compose smoke
that passed health, order retrieval, refund issuance, idempotent replay and all
four declared assurance spans. The fresh factory regression after these additions
is now complete. These are component/local-MVP results, not a production release
claim. Release work now also includes deterministic CodeModelSnapshot generation
from RI/build identities, signed ReleaseBundle/DeploymentBinding builders and
promotion substitution checks. A fresh full factory suite after these latest
additions is now complete.

**Historical checkpoint:** 13 September 2026. The combined regression run had
completed with **2,211 passed and no failures/errors/skips** (436.45 seconds,
93% rounded combined statement/branch coverage); the separate Stage-0
workflow suite is **25 passed / 1 failed**. The work is neither full-plan
complete nor release/deployment-ready. A development checkpoint commit is a
separate decision; no commit, staging, push or deployment was performed by this
readiness check.

**Resumed implementation:** the Windows fixture failure is corrected. PostgreSQL
review persistence, bounded coordination and advisory inspection are included in
the complete run, including 31 integration-directory and 10 frozen acceptance
cases. These results qualify the tested local components, not the whole factory.
The owned PostgreSQL test container was stopped and its absence verified.
Paid provider/cloud usage remains zero.
The [current implementation checkpoint](IMPLEMENTATION-CHECKPOINT-2026-09-13.md)
records the coordinator, advisory inspection and blocked third-review finding.

## Authorization and baseline

- User authorized full plan implementation and continuing through routine time
  notices. Existing no-commit/push/merge and no-production constraints remain.
- ARP-1 and the approved scope are implementation requirements. Existing frozen
  acceptance suites and remote human protections remain unchanged. New tests in
  this implementation are unit/integration tests, not independently merged
  acceptance contracts. Do not claim that missing provider approval is fulfilled.
- Factory HEAD: `08e9d7d5bffbc63896fd308bfeb9ab0d3c93da12` on
  `feat/factory-preflight-core`; no checkout/reset/staging performed.
- Baseline: 246 tests passed (60.05 s); 10 readiness probes failed; review probes
  have four unsafe-case failures. All are actual observed results before edits.
- User candidate-test SHA-256:
  `8ce82ddecd0bfeab9f93b6c34744cd9ab743ebe5d9bda07fa4f21c504c5c0a0b`.
- User TaskContext-test SHA-256:
  `7ff04e14219d81836961669288601d2a6662b4e2c607c7a1cc3f3c0e9b4cd525`.
- Frozen suite hashes: impact `ae38a8f022f41266116b59dab9e42adb0a894e47a95da521bbe6351ffce02c05`,
  states `03c9574edf6785854899e0314bdc84b91146d239ad631cde7d3c1a7663275f43`,
  toolchain `7f0709a1fae7b164da4ff925769e26185a155ad15d319610f8c308b254cac254`.

## Work packages

| Package | State | Evidence / next action |
| --- | --- | --- |
| A00 baseline and amendment | Baseline recorded; provider qualification blocked | Implement ARP-1 as explicit local policy; do not alter CLOSED spec or frozen suites |
| A01 primitive correctness | Local combined regression green; qualification incomplete | Windows failure corrected; impact/readiness/authorization and raw Git capture included in 2,261 passing tests; cross-platform and final readiness evidence remain |
| A02 trusted delivery controls | Task identity closed; structured baseline workflow locally wired, review pending | 54 workflow simulations pass with two identity reviews complete. Baseline/candidate evidence policy, native pytest recorder, verify-tests CLI, worktree baseline execution and control-plane guards pass local tests; trusted runner provenance, target propagation, independent review and real approvals remain open |
| A03 tenant identity/broker | Partial local boundary | HMAC tenant principal, role checks, AWS Secrets Manager-backed GitHub App broker and production fail-closed runtime configuration exist; OIDC, ECS task identity and credential-broker deployment remain |
| A04 execution/evidence | Review/workflow persistence locally verified; broader workflow pending | PostgreSQL RLS/CAS review store plus tenant-scoped workflow intake/events, inbox/webhook dedupe and expiring outbox claims pass real database tests; scheduler and reconciler remain |
| A05 quality/model/context | Partial local contracts | Repository catalogue/symbol/test/search adapter, provider-neutral ModelPort/qualification policy and Ed25519 release identity helpers exist; ContextPacks, qualified live models and trusted provenance remain |
| A06 reusable agentic orchestration | Durable coordinator and authenticated intake locally verified | Doer/reviewer executor seam, persisted intake, bounded repairs and fail-closed CLI/API seams exist; Architect/Test Author/Implementer/live model/auth integration remain |
| A07 gates/sandbox | Partial deterministic gate runner | No-shell bounded subprocess runner, YAML toolchain loader and `factory run-gate` pass Python acceptance, TypeScript build/unit/types and target HCL build/types; unsupported HCL unit remains `NOT_AVAILABLE`; sandbox/resource/egress enforcement remains |
| A07 mobile extension | Locally verified adapter only | Historical mobile checkpoint: 69 new tests, 315 total pass; native app/emulator/authoritative CI/visual adapter unqualified |
| A08 payments vertical demonstration | Local vertical slice verified; factory production proof remains open | Durable PostgreSQL payments, SDK, Terraform profile, Compose startup, HTTP refund/idempotency and Jaeger span smoke pass; factory-generated delivery and external gates remain |
| A09 surfaces | Authenticated API plus read-only MCP and advisory CLI surfaces | Tenant-scoped intake/status/trace/gate API, tenant-bound read-only MCP tools and fail-closed `chat`/`gate`/`run-gate` exist; product UI and live operational backend remain |
| B01–B09 commercial/three-cloud qualification | Pending / external prerequisites missing | Full plan remains required; absent evidence is not PASS |

## Consequential external blockers

- No authorized model credentials or independently qualified reviewer family
  available for real model evaluation. Coding subagent reviews are not proof of
  different-family qualification.
- One actual human: sensitive production and provider-required two-human merges
  remain blocked. Approval evidence cannot be manufactured.
- NOK-Apps private inventory, cloud sandbox account/subscription/compartment and
  regions, OIDC issuer, residency and numerical SLO/RPO/RTO decisions not supplied.
- $20 total paid simulation cap; current paid model/cloud usage **$0**. No
  provider writes, subscriptions or cloud resources created by this execution.

## Verification and resumption

### Verified gate checkpoint — latest current run

- Full saved JUnit: **2,320 passed, 0 failed/errors, 1 environment-dependent
  skip**, duration 478.059 seconds. The JUnit total is 2,321 including the skip.
  It includes 31 integration-directory cases, 10 frozen acceptance cases and
  the authenticated service, workflow persistence, inbox/outbox, release-signing,
  repository intelligence, MCP, model and toolchain additions. Saved evidence:
  [JUnit](../artifacts/verification-final-current/junit.xml) and
  [coverage JSON](../artifacts/verification-final-current/coverage.json).
- Current workflow simulations: **54 passed**, exit 0. Ruff and strict mypy (72
  source files) exit 0; Git whitespace validation exit 0. No files are staged or
  committed.
- Python statement coverage: **92.86%**. Cross-platform and unexecuted
  production behavior remain qualification gaps; this is not a claim of complete
  factory coverage.
- JUnit SHA-256:
  `3a7cae5c123329f96cd7091b277cd1cf14cdc162126482758b1ce1cf8103baf0`;
  coverage JSON SHA-256:
  `7c3255ef6acf0eaa19fa4228f33f68f39dad9b78001e844dac3d58dc3a3580f7`.
- The two original user test files and three frozen acceptance source files still
  match every recorded baseline hash. HEAD remains
  `08e9d7d5bffbc63896fd308bfeb9ab0d3c93da12`; the index has no staged changes.
- A subsequent shared-terminal invocation was interrupted after 1,616 passing
  cases. That attempt is **not** the complete run above. Its owned container was
  cleaned up; successful Podman queries show no remaining containers with this
  run's verification label or the earlier release-check label.

#### New baseline-evidence boundary

The [policy](../src/nokinc_factory/policy/baseline.py) requires a green original
baseline, exact declared new assertion failures, unchanged refactor behavior,
complete test inventories, and matching source/suite/runner/environment pins.
Collection/setup/teardown errors, skipped cases, stale runs and missing evidence
cannot satisfy expected-red proof. Infrastructure-only baseline evidence is
explicitly `NOT_APPLICABLE`, not `PASS`.

The [pytest recorder](../src/nokinc_factory/adapters/pytest_probe.py) exercises
actual pytest phases. It runs in an externally isolated worker and does not
authenticate binding values, sign evidence, sandbox candidate code or verify
that the supplied SHA/digests describe the worker. Those are still pipeline
responsibilities. Its records contain IDs/outcomes, not raw exceptions/test data.

The CLI now supports `factory verify-tests baseline --contract <json> --before
<json> --run <json>` and `factory verify-tests candidate --contract <json>
--candidate-sha <sha> --run <json>`. Exit codes are 0 PASS, 1 FAIL, 2 unavailable/
invalid, and 3 NOT_APPLICABLE. Results explicitly carry `authorizes_merge=false`.
The recorder is invoked with `python -m nokinc_factory.adapters.pytest_probe
--binding <json> --report <new-json> -- <pytest-arguments>` using the target's
approved toolchain. It never silently overwrites an existing report.

TDD evidence: missing models/command first; 30 policy cases passed after
implementation; real subprocess simulations proved old-green/new-red/candidate-
green and rejected collection/runtime/setup/skipped/empty-suite outcomes. CLI
failure contracts then passed after wiring. Native hook coverage was exercised
in-process as well as through subprocesses. The factory [baseline workflow](../.github/workflows/gates.yml)
now invokes inventory collection, the phase-aware recorder and
`verify-tests baseline`. **Independent review is unavailable, not passed.** The
workflow still lacks authenticated runner provenance and has not been propagated
to the target repositories; those controls remain mandatory. The target payment
vertical also passed its complete local gates, real PostgreSQL persistence tests,
and a simultaneous Podman Compose health/order/refund/idempotency/OTel smoke.

Next: complete the bounded independent review and trusted runner integration,
then the missing authenticated lifecycle/provider interfaces. Do not advertise
the full application as complete from these component results. Model access,
cloud sandbox/region decisions and the high-risk human quorum remain external
blockers; no paid model/cloud calls occurred in this checkpoint.

### Final fresh-schema verification — 13 September 2026

- Full Python suite: **2,211 passed, zero failed/errors/skipped**, exit 0;
  436.45 seconds; combined statement/branch coverage 92.56% (93% rounded).
- Saved JUnit confirms 31 integration-directory cases (including one dialect
  rejection test) and 10 frozen acceptance cases. Real PostgreSQL was configured.
- Ruff, strict mypy (53 source files), and Git whitespace checks passed on the
  production source used by the final run. No production Python changes were
  made after those checks; additional tests passed in the final run.
- Exact final report identities and cleanup receipt are recorded in the
  [implementation checkpoint](IMPLEMENTATION-CHECKPOINT-2026-09-13.md).
- Stage-0 workflow suite: **25 passed / 1 failed**. Its canonical task-reference
  redesign still awaits explicit authorization after the third review stop.
  The terminal-completion notification does not authorize that redesign.
- No commits, pushes, merges or deployments. The disposable PostgreSQL container
  was stopped and no matching container remained; no other resources targeted.

### Resumed Windows and persistence work — 13 September 2026

- Windows fix is test-only: catch only the attempted swap's native sharing
  denial, then require an actual inside-file read and zero external read handles.
  Successful substitutions still must be rejected. No frozen assertion changed.
  Focused native/acceptance suite: 16 passed; source-aware mypy clean (43 files).
- Added [single-transition verification](../src/nokinc_factory/policy/review_persistence.py)
  and [PostgreSQL store](../src/nokinc_factory/adapters/postgres_review_store.py),
  with administrator-only immutable parent budgets/seed registration, FORCE RLS,
  parent-first locking, atomic snapshot/event/accounting and actual-use settlement.
- TDD: missing-module failures first; then 8 real PostgreSQL integration cases
  passed. Review 1 exposed nine regressions (including a pure provider-ID case),
  all reproduced red then fixed. Review 2 exposed inherited event-update access;
  two real database tests failed before the narrowly scoped guard correction.
  Review 3 found no remaining material blocker in the local-store scope.
- Final focused persistence result: **25 passed** (18 PostgreSQL / 7 pure).
  Broader review-core compatibility run: **203 passed** before the final guard
  addition. These code reviews are not verified different-model-family evidence.
- Dependencies installed are existing declared phase1 dependencies: SQLAlchemy
  and psycopg. Tests use only an owned resource-limited localhost PostgreSQL 16
  container with distinct owner/nonowner worker; no credentials are printed.
- Trust remains explicit: the store does not authenticate OIDC users, qualify
  models or prove real provider receipts. The service must supply trusted grants;
  model shells never receive its database connection. Unknown commit results
  require reload/reconciliation, not repeated provider execution.

### Earlier failed combined checkpoint — 13 September 2026

The completed recovery run used the configured factory virtual environment,
Git Bash on PATH, bytecode writes disabled and pytest plugin autoload disabled.
It executed source/test discovery with a fresh JUnit report and no pytest cache.
It did not modify machine Git configuration, rerun failed cases until green or
suppress the failure.

| Check | Actual result |
| --- | --- |
| Combined pytest | **2,132 cases; 2,131 passed; 1 failed; 0 errors; 0 skipped**; 405.65 seconds; exit 1 |
| Ruff over source/tests | PASS; exit 0 |
| Strict mypy over source | PASS; 42 source files; exit 0 |
| Existing user test files | Both SHA-256 hashes match the recorded baseline |
| Frozen acceptance source files | All three SHA-256 hashes match the recorded baseline |
| Repository HEAD | Still `08e9d7d5bffbc63896fd308bfeb9ab0d3c93da12` |
| End-to-end factory / native mobile / three-cloud deployment | Not demonstrated; component test success is not release qualification |

The failing case is
[test_swapped_ancestor_cannot_supply_external_leaf_bytes](../tests/unit/test_git_capture_io_integrity.py#L12-L43).
Its injected ancestor rename encountered Windows `WinError 32` (sharing violation);
the exception message did not match the test's expected error pattern. This is
a real failing suite result. It is not by itself proof that external file bytes
were read, and it must not be concealed by dropping the integrity assertion.

The run retained a temporary JUnit report with ID
`recovery-checkpoint-5e099cdf4e214a4b99d73d30e1891672`; its root summary and terminal
exit report agree. The enclosing PowerShell command ultimately exited zero after
printing hashes; **that does not override pytest's exit 1**. Do not report shell
success as test success.

At that checkpoint the Windows test, store and coordinator were outstanding.
The resumed sections and linked implementation checkpoint supersede those
statuses. Full lifecycle, trusted approval execution and provider qualification
are still code/integration gaps, not merely missing deployment credentials.

### Earlier mobile checkpoint — 12 September 2026

Mobile extension TDD: initial import failures in both new test modules; first
adapter run 46 tests passed, strict mypy passed; five lint line-length issues
corrected. CLI contract then demonstrated four failures before wiring and two
real subprocess plumbing tests passed. Review pass 1 found unsupported XML
structure, substituted artifact directories and traversal bounds; eight new
regressions failed before correction. Review pass 2 found no remaining material
blockers in the explicitly local/advisory scope. These reviews are not verified
different-model-family qualification.

Final mobile run: **315 tests passed in 64.90 s**, including 69 new tests;
**10 acceptance passed unchanged**; Ruff and strict mypy passed (27 source files).
Combined statement/branch coverage: Maestro adapter 95%, input hashing 93%,
report parsing 98%, mobile models 100%, CLI 92%. Diff coverage vs cached
`origin/main`: 97%; untracked new modules are excluded by Git diff-cover and
therefore assessed separately above. Coverage output used temporary files, not
the repository's prior report.

Actual Windows CLI smoke returned `NOT_AVAILABLE`, advisory scope, zero tests,
exit 2, no artifact/device/provider operation. All five preserved user/frozen
test hashes still match the baseline. No native Android/iOS or paid provider
tests occurred; platform qualification stays BLOCKED. No commits/pushes.

See [mobile testing](MOBILE-TESTING.md) for corrected tool assumptions, ownership,
real integration/release lanes and remaining authoritative enforcement work.

Record red→green test output, lint/types, simulation scope, reviewer findings and
current artifact identifiers as each unit completes. Update the earliest pending
package rather than rediscovering the repository. Never carry a PASS across an
unreviewed material change. Finish independent safe work while external cells
remain blocked; do not report the whole plan completed by counting local tests.
