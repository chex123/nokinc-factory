# Implementation checkpoint — 13 September 2026

**Overall: partial implementation, NOT deployable as the complete factory.**
This checkpoint records actual work after the [audit](READINESS-2026-09-12.md).
See the [delivery ledger](DELIVERY-LEDGER.md) for remaining packages and blockers.

## Subsequent verification addendum — 20 September 2026

The authorized canonical `Factory-Task` redesign supersedes the older task
reference blocker recorded below. The offline privileged workflow probes now
pass **53/53**, and the factory `gates.yml` baseline lane uses the structured
pytest inventory/recorder plus `verify-tests baseline`; this is local workflow
wiring, not proof of trusted hosted-runner provenance or model-family
independence.

The latest complete local run passed **2,271 tests** with zero failures, errors
or skips, including 31 integration-directory cases against disposable
PostgreSQL, and Ruff, strict mypy and whitespace checks passed. The complete
run and evidence hashes are recorded in [DELIVERY-LEDGER.md](DELIVERY-LEDGER.md).
The three demo target workflow copies remain language-specific and unchanged;
target propagation requires separately versioned adapters and qualification.

## Implemented and exercised

- **Windows capture-test correction:** native sharing denial must be followed by
  an actual inside-file read; successful substitutions still must be rejected and
  external bytes must never reach a read stream. No production security check or
  frozen acceptance contract was weakened.
- **PostgreSQL review persistence:** immutable registered seeds/parent budgets,
  tenant-scoped FORCE RLS, nonowner worker guards, atomic parent-first CAS and
  reservations, append-only history, global invocation/execution claims, actual
  usage settlement, and audit-tip validation. Owner operations remain separate.
- **Bounded coordinator:** durable reservation before execution, original-doer
  repair, minimum two valid reviews, no repair for optional suggestions,
  cooperative pause/resume, and no automatic resubmission of interrupted calls.
  Post-reservation authorization, time and artifact checks can persist a stop.
  Oversized/invalid execution results never erase a stop or discard trusted usage.
- **Advisory inspection:** `status --review-session` and `trace <work-item>
  --review-session` read an explicit archive. Views verify internal replay/digests,
  omit raw model prose and mark live artifact/release authorization **false**.
  No argument means no operational backend; it does not invent an empty live
  queue. Regular-file, byte/attempt, JSON and arithmetic boundaries are enforced.
- **Stage-0 review guards, partially repaired:** tested head/base, immutable diff
  retrieval, missing task/rules/model errors, task drift, unsupported binary
  evidence and provider-error sanitization have regression coverage. This unit
  remains **blocked** by the task-reference finding below.

The coordinator's executor/observer/authorizer are trusted injected ports, not
proof of real model identity, OIDC login, independent retrieval, sandboxed writes
or production artifact verification. The PostgreSQL grant is not something a
public caller may manufacture. These boundaries still require their application
and provider adapters before actual users can safely operate the full factory.

## TDD and independent review record

| Unit | Red evidence | Verified progression | Review disposition |
| --- | --- | --- | --- |
| Windows fixture | Existing full-run WinError 32 failure | 16 focused/native/frozen tests passed | Two bounded local reviews; no security weakening |
| Durable store | Missing imports, then nine replay/CAS/clock cases and two inherited-grant failures | 25 focused cases passed, including 18 real PostgreSQL cases | Third review closed the final inherited audit-update finding |
| Coordinator | Missing application; seven drift/output/stop failures | 226 review-unit cases and 24 database integration cases passed at that checkpoint | Second code review closed within the explicit adapter boundary |
| Inspection CLI | Nine missing-function cases, four UTC overflow cases, two numeric overflow cases | 25 CLI/compatibility tests passed before additional deny-path coverage | Third review closed both archive error-path findings |
| Stage-0 guards | Nine original unsafe cases; ten new context/envelope cases; two nonlocal-reference failures | **25 pass / 1 fail** in the current 26-case workflow suite | Third review still found a material issue; no fourth autonomous repair cycle |

These are local code reviews, **not verified different-model-family qualification**.
Quotas intermittently blocked delegated reviews; unavailable invocations were not
counted as reviews. Review findings were reproduced before corrections, not
dismissed to obtain a green result.

## Combined evidence

Completed run `nokinc-delivery-verified-4c10760b94d54af590d8e79ab7d45ee2`:

- **2,201 passed, 0 failed, 0 errors, 0 skipped; exit 0**, 451.99 seconds.
- Real PostgreSQL integration was configured, not skipped or substituted with
  SQLite. The SQLite dialect rejection test does not simulate PostgreSQL.
- Ruff, strict mypy (53 source files) and Git whitespace validation passed.
- Tracked changed-line coverage versus cached `origin/main`: **95%**. Untracked
  modules are not counted by Git diff-cover. Whole Python statement/branch
  coverage was **92%**; some modules and platform-specific branches still need
  additional qualification. Do not claim every module exceeds 90%.
- JUnit SHA-256:
  `3fcf6dc21421d55b28c1a2fb23a4e367951ca4822b4af069b78ca3e4d983613d`.
- Coverage JSON SHA-256:
  `f83ffc0f0ff0e7b07802805bc73c0fbcbd89ea15994b68e110c7c469a2662411`.

The final fresh-schema run supersedes those totals:
`nokinc-delivery-final-d586301c9cca4019b54fb0c176a38c08`.

- **2,211 passed, 0 failed, 0 errors, 0 skipped; exit 0**, 436.45 seconds.
- The saved JUnit contains **31 integration-directory cases** and **10 frozen
  acceptance cases**. Thirty integration cases exercise PostgreSQL; one verifies
  that a non-PostgreSQL dialect is rejected. No database tests were skipped.
- Whole Python combined statement/branch coverage: **92.56% (93% rounded)**.
  PostgreSQL administration/schema/store: **98% / 96% / 94%**; archive reader:
  **100%**; coordinator: **93%**. These are coverage measures, not proof that the
  whole factory or every supported platform is qualified.
- JUnit SHA-256:
  `a4adce00ea3c580f51cc5a592925ccaf3226be34fa233f68aa0381716a223c23`.
- Coverage JSON SHA-256:
  `f39991ce2dcae75ab096bb6aed882dd3679b4fd06da6906a825ac033e433991b`.
- The owned container `nokinc-review-tests-20260913`, identified by
  `nokinc.test=review-store`, was stopped successfully (exit 0). A post-cleanup
  query succeeded with no matching container. Other containers/images were not
  targeted. Temporary database URL environment entries were cleared in the
  cleanup shell; no secret values were logged or saved to this document.

The separate JavaScript workflow suite remains **25 passed / 1 failed**. The
green Python result does not clear that independent release blocker.

## Open Stage-0 blocker — repair budget exhausted

Input `Closes https://github.com/other/project/issues/2?x=:#2` still passes the
current bare-reference heuristic and can resolve to local issue #2. The latest
workflow suite explicitly reproduces this as one failure. Other false-success
guards passing do not close this finding.

**Stop:** no additional source repair or approval after the third review. No
workflow changes have been pushed or activated remotely. Before this unit resumes,
use an explicitly authorized redesign with one canonical task-reference contract
(repository identity plus issue ID), not another incremental punctuation regex.
Freeze its positive/negative identity cases first; reset neither execution costs
nor failed-attempt history silently. An improved but known-failing workflow is
not a release candidate.

The demo target workflow copies are unchanged. Do not propagate the factory
workflow while the identity finding, trusted-runner/secret controls, genuine
model qualification and full ARP-1 integration remain unresolved.

## What has not been demonstrated

- Full conversation → approved design → independently frozen tests → implemented
  application → verified PR → signed artifact → deployment → business telemetry.
- Public authenticated tenant/API/UI, complete ALM inbox/outbox and approved
  live provider execution. `chat` and `gate` still have no implementation.
- Real model-family qualification, real Expo Android/iOS runs, full visual gates,
  or Azure/Oracle Cloud/AWS release/rollback rehearsals.
- Complete payment behavior, typed refund SDK and real infrastructure deployment.
- Commercial readiness, production human quorum, retention/restore/security
  qualification, or production SLA/RPO/RTO evidence.

No commit, push, merge or production deployment occurred. The two original user
test files and all three frozen acceptance files retain their recorded hashes;
HEAD remains `08e9d7d5bffbc63896fd308bfeb9ab0d3c93da12`. Paid model/cloud usage
remains **$0** of the authorized $20 ceiling.
