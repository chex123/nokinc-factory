# Production checklist — 39 items

This is the exact 39-item checklist recovered from the 20 September conversation
history. It is a delivery tracker, not a claim that local tests equal production
readiness. `PARTIAL` means a bounded local component exists; the production item
is not complete. `BLOCKED` means an external prerequisite is missing.

## Software Remaining

| # | Item | Status | Current evidence / remaining work |
|---:|---|---|---|
| 1 | Complete the requirements-to-deployment factory workflow | PARTIAL | The live browser conversation routes Business Analyst, Architect and Code Analyst turns; change requests, human Gate 1, implementation, CI, approval and deployment orchestration remain. |
| 2 | Connect real AI agents and flagship/economical review | PARTIAL | Four synthetic live calls returned configured model/family identities; two-model role runtime and fake-provider tests exist, but accuracy qualification, durable jobs and other SDLC roles remain. |
| 3 | Ground agents in repository intelligence and approved design | PARTIAL | ECS revision 18 supports up to four allowlisted repositories per question; live metadata includes SDK, frontend, and private backend, with repository-qualified citations and durable selection audit. Full RI/C4 enforcement remains. |
| 4 | Finish usable `chat`, `gate`, `status`, and `trace` commands | PARTIAL | Revision 18 serves multi-repository chat; authenticated status/repository/trace reads work. Both old turns have `INTAKE` and `CHAT_TURN_RESERVED`, no `CHAT_TURN_RECORDED`, and returned 502. Uncapped production testing is now authorized, but `FACTORY_CHAT_MODEL_TURN_LIMIT=unlimited` is not deployed. Gate provider remains unavailable. |
| 5 | Build login and company accounts | PARTIAL | Cognito OIDC authorization-code/PKCE login, signed callback/session and TOTP for one admin-created tenant/operator user are live; lifecycle, account removal and broader tenant validation remain. |
| 6 | Complete company isolation | PARTIAL | Tenant-scoped RLS exists for workflow/review stores; repositories, artifacts, credentials, model context and reports need complete isolation. |
| 7 | Secure credentials and GitHub access | PARTIAL | Live repository metadata includes SDK, frontend, and private backend; bounded App-token/source access for backend was verified. Common credential patterns are blocked before model egress. Full secret/PII scanning, webhook verification and governed writes remain. |
| 8 | Finish durable job processing | PARTIAL | Durable intake/events, tenant-scoped inbox deduplication, idempotent outbox enqueue, expiring worker claims and holder-bound acknowledgement exist; scheduler and reconciler remain. |
| 9 | Build isolated execution workers | PARTIAL | Bounded no-shell toolchain runner exists; sandbox, egress, filesystem and resource isolation remain. |
| 10 | Enforce real human approvals | PARTIAL | All eight gate/reviewer Environments are protected, and the separate `pilot-deploy` environment has `triplexapps`, self-review prevention, protected-branch-only and no admin bypass. No gate run was dispatched; the organization App does not cover the personal approval repo, and API dispatch/run/review verification remains `APPROVAL_PROVIDER_NOT_CONFIGURED`. Distinct human ownership of reviewer accounts is unverified. |
| 11 | Connect test enforcement to trusted CI | PARTIAL | Structured baseline workflow, worktree execution and control-plane guard are locally tested; authenticated runner provenance, propagation and independent review remain. |
| 12 | Complete application quality gates | PARTIAL | `factory run-gate` now executes Python, TypeScript and HCL declarations with target-local environments, bounded no-shell execution and honest `NOT_AVAILABLE`; security, license, mutation and performance gates remain. |
| 13 | Build practical user interfaces | PARTIAL | An authenticated multi-repository chat is live on revision 18 with SDK, frontend, and backend choices. Approval queue, runtime progress and evidence browser remain. |
| 14 | Create trustworthy release packages | PARTIAL | Build-derived CodeModelSnapshot, ReleaseBundle/DeploymentBinding builders, Ed25519 signatures and promotion substitution checks exist; reproducible build, SBOM, provenance generation and artifact publishing remain. |
| 15 | Implement deployment and recovery | PARTIAL | Local Compose/Terraform deployment profile exists; environment promotion, canary, health policy and exercised rollback remain. |
| 16 | Implement Azure, OCI and AWS profiles | PARTIAL | A live AWS ECS/Fargate, RDS, HTTPS ALB and Cognito pilot is deployed; Azure/OCI, state-managed infrastructure, promotion and recovery rehearsals remain. |
| 17 | Complete coordinated multi-repository changes | PARTIAL | SDK/backend/infra contracts and deployment order exist; durable ChangeSet merge, compatibility and partial-success recovery remain. |
| 18 | Finish runtime assurance | PARTIAL | Payments HTTP/OTel/Jaeger smoke passes all four declared spans; general target identity, intent and failure assurance remain. |
| 19 | Complete commercial operations | NOT_STARTED | Metering, quotas, cost reporting, invoicing, onboarding/offboarding, export/deletion and retention remain. |
| 20 | Complete operational packaging | PARTIAL | Revision 18 uses digest-pinned Chainguard dev/runtime images; its ECR scan is complete with zero findings. A local scan-gated deployment script/workflow, OIDC role, repo variable, and protected no-admin-bypass environment are configured/tested but not on `main` or executed. The admin IAM user can still bypass the workflow. Production monitoring, alerts and recovery runbooks remain. |
| 21 | Complete demonstration applications | PARTIAL | Payments behavior, SDK, Terraform profile and Compose smoke pass locally. Deterministic Postgres demo seeds now replay safely and survive Postgres/app restarts with a named volume; full factory production remains open. |
| 22 | Implement existing-application onboarding | NOT_STARTED | No governed brownfield inventory or safe migration lane exists. |
| 23 | Finish mobile testing when supported | PARTIAL | Advisory Maestro adapter and contracts exist; native builds/devices, visual comparison and release enforcement remain. |

## Proof Still Needed

| # | Item | Status | Current evidence / remaining work |
|---:|---|---|---|
| 24 | Independent review of unfinished components | PARTIAL | A local different-family reviewer flow rejects unsupported claims and passes fake-provider tests; real-provider review and independent assessment of unfinished components remain. |
| 25 | Close platform and coverage gaps | PARTIAL | Windows/local Linux-container paths are exercised; full supported Windows/Linux/macOS matrix and production branches remain. |
| 26 | Exercise real providers | PARTIAL | Four synthetic identity probes passed. Both earlier turns were reserved and returned 502 with no recorded result; CloudWatch has only access lines. Five additional turns are authorized but not enabled on the live task; local safe diagnostic logging is not deployed. Runtime qualification has not run. |
| 27 | Demonstrate complete application delivery | NOT_STARTED | No genuine factory-generated application has completed request-to-release delivery. |
| 28 | Prove security and failure recovery | PARTIAL | RLS, replay, drift, budget, control-plane and crash-boundary tests exist; complete tenant/security/outage/worker recovery matrix remains. |
| 29 | Measure quality and performance | NOT_STARTED | Synthetic connection latency was recorded, but no model accuracy, realistic load, concurrency or actual cost qualification has run. |
| 30 | Rehearse cloud releases and recovery | NOT_STARTED | No authorized cloud deployment or backup/restore rehearsal. |
| 31 | Run real mobile qualification | NOT_STARTED | No Android/iOS target, device/emulator or release-package run. |
| 32 | Complete the release process | PARTIAL | Signed evidence and protected local gates exist; actual release candidate approval, hosted CI and versioned release remain. |

## Decisions And Access

| # | Item | Status | Current evidence / remaining decision |
|---:|---|---|---|
| 33 | Model access and independent reviewer family | PARTIAL | Four synthetic identity probes and live IAM decisions pass for configured pairs. Both chat attempts returned 502; five further turns are authorized but not applied. Quality, retention, actual billing, and model-family independence remain open. |
| 34 | Repository access for pilots | PARTIAL | Live authenticated metadata lists SDK, frontend, and private backend; bounded source access was verified for backend. Guarded change delivery remains. |
| 35 | Cloud destinations | PARTIAL | AWS `us-east-1` ECS/Fargate, RDS, ALB, and Cognito pilot are live. Azure/OCI destinations and shared Terraform state/import remain absent. |
| 36 | Security and commercial policies | BLOCKED | OIDC, data locations, model sharing, retention, licensing, privacy, DPA and customer terms are undecided. |
| 37 | Service targets | BLOCKED | User/job volumes, latency, uptime, backup and recovery targets are not supplied. |
| 38 | People and responsibility | BLOCKED | Verify `triplexapps` and `chex123` are controlled by distinct people and name release/support/incident owners. Environment username separation alone does not prove human separation. |
| 39 | Spending and publication authority | PARTIAL | Prior estimated $20 allowances and the five-turn extension are superseded. On 29 September the user explicitly authorized uncapped production testing. The app has no hard dollar ceiling; actual billing is unverified. The live task remains at two turns until an image with `FACTORY_CHAT_MODEL_TURN_LIMIT=unlimited` passes the protected deployment. |

**Current conclusion:** no production checklist item is complete end to end yet.
Several local foundations are now proven, but the checklist requires integrated,
trusted, externally authorized production evidence. See
[DELIVERY-LEDGER.md](DELIVERY-LEDGER.md) for dated evidence and constraints.
