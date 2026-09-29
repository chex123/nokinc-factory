# Chat and Agent Roles

The signed-in browser destination is `/chat`; `/v1/status` and the GitHub
repository endpoints remain JSON APIs. The chat page sends same-origin requests
using the HttpOnly Cognito cookie and never reads or stores the ID token.

## Per-Turn Flow

1. The browser submits a message, selected mode, up to four allowlisted
   repositories, work-item ID and at most six previous user/assistant exchanges.
   The singular `repository` API field remains accepted for older clients.
2. The deterministic router selects Business Analyst, Architect or Code Analyst.
   Explicit selection wins. Automatic mode uses a small keyword rule. A
   repository-grounded role requires at least one selected repository, and
   every selected repository is checked against the allowlist before intake.
   Duplicate selections and any out-of-allowlist repository are rejected.
3. Business Analyst elicits requirements and asks at most three questions; it
   cannot produce architecture. Architect and Code Analyst read a separate
   bounded source context for each selected repository. Multi-repository source
   text is capped at 80 KB total. Citations are verified against the exact
   repository, path, branch and lines, then reviewed by a different model family.
   None of these roles can write code or approve a gate.
4. Each model-backed turn runs synchronously: one doer call followed by one
   reviewer call. A reviewer refusal withholds the answer and asks for
   clarification.
5. The workflow trace stores selected repository identities, model/role identity,
   content digests, provider run IDs and timings. It does not store the raw chat
   transcript, prompts, model claims or source quotes. Conversation text is held
   in browser `sessionStorage` for the current tab and sent as untrusted context
   on the next turn. Older single-repository session entries are read as a
   one-element selection.
6. Before any provider call, the service appends a tenant-scoped reservation for
   exactly two model invocations. The pilot task's durable budget is two chat
   turns total; once used, later requests are rejected before model invocation.

## Where Roles Live

| Role or component | File | Responsibility |
|---|---|---|
| Browser entry and asset serving | `src/nokinc_factory/application/web.py` and `application/static/` | Serves the same-origin conversation UI. |
| HTTP, login, thread binding and audit | `src/nokinc_factory/application/service.py` | Authenticates requests, checks tenant/creator/repository scope, reuses work items and records redacted turn events. |
| Automatic delegation | `src/nokinc_factory/application/chat_roles.py` | Applies deterministic keyword routing; it is not a model supervisor. |
| Business Analyst | `src/nokinc_factory/application/business_analyst.py` | Structured elicitation and independent review; never designs. |
| Architect / Code Analyst | `src/nokinc_factory/application/grounded_repository_discussion.py` | Two source-grounded discussion profiles with exact citation checks and different-family review. |
| Source evidence | `src/nokinc_factory/adapters/github_repository_reader.py` | Reads bounded GitHub source from each allowlisted repository. |
| Provider adapters and runtime assembly | `src/nokinc_factory/adapters/model_providers.py` and `src/nokinc_factory/application/runtime.py` | Resolves the configured OpenAI, Google and Bedrock routes lazily. |
| Original CLI Domain Expert | `src/nokinc_factory/agents/domain_expert.py` | Separate PydanticAI CLI implementation; the browser path uses the provider-neutral reviewed service above. |

There are no continuously running agents. The runtime builds reusable, stateless
model ports; each submitted turn invokes them and returns. The HTTP process is
not a durable job worker.

## Explicitly Not Connected

The browser chat does not yet create Business Ready issues, advance Gate 1, run
the Architect only after human approval, author frozen tests, implement code,
run candidate code in an isolated worker, create pull requests, deploy or
rollback. It currently provides source evidence only; no runtime-test worker is
connected to chat, so runtime behavior must not be inferred from source alone.
Explicit run/e2e/simulation requests fail with
`RUNTIME_EVIDENCE_NOT_CONFIGURED` before intake or model-budget reservation. The
approval endpoint remains `APPROVAL_PROVIDER_NOT_CONFIGURED`.
There is no OpenTelemetry exporter or server-side transcript replay. The
different-family connection probes establish route availability and returned
identity, not model accuracy, statistical quality or production fitness.

## Current Verification Boundary

The pilot chat UI is live on ECS revision 18 using
`pilot-chat-multirepo-backend-allowlist-20260927-01` (digest
`sha256:551a903d61735f253e0c4c0387bfa0380577070d2abca9047b09dc68ceedb513`).
The service is steady at one desired/running task with the deployment circuit
breaker and rollback enabled. The image uses digest-pinned Chainguard Python
dev/runtime stages, runs as UID 65532, and its ECR scan for this exact digest is
complete with zero findings. Revision 18 enforces
`FACTORY_CHAT_MODEL_TENANT_ID=00001` and the durable two-turn cap.

The deployed allowlist contains `NOK-Apps/flur-sdk`, `NOK-Apps/flur-frontend`,
and `NOK-Apps/flur-backend`. An authenticated live repository request returned
all three with default-branch and visibility metadata; the backend is private
and not archived. A clean browser reload showed a ready session and the three
repository options. The existing backend App-token/source-read check was
bounded; no model call was made to re-test source grounding because the live
turn allowance is exhausted.

Both authorized live turns were reserved: one Business Analyst request and one
frontend Code Analyst request. Authenticated trace reads confirm that each has
`INTAKE` and `CHAT_TURN_RESERVED`, with no `CHAT_TURN_RECORDED`; both requests
returned 502 without an assistant response. CloudWatch contains generic
Uvicorn 502 access lines only, with no provider exception detail. The live
two-turn cap is exhausted, so the current task rejects further model calls.
PR #28 merged explicit `unlimited` support into `main`, and post-merge gates
passed. The live ECS task remains at revision 18 with limit 2 until the
protected image deployment completes. No additional provider call has been
made; actual billing is unverified and `unlimited` has no hard dollar ceiling.

On 27 September, CloudWatch filters around both failures returned only those
access lines and no `ERROR` records. Mainline code now records a redacted
failure stage, exception class, and allowlisted provider diagnostic code; it
does not record exception messages, provider bodies, prompts, or credentials.
These diagnostics are not in the live image yet. Turn-count limits do not
enforce a dollar ceiling.

The `gate-approval.yml` workflow is active in `chex123/nokinc-factory`. All
eight `gate-1..4` / `triplexapps` and `chex123` GitHub Environments have exactly
their expected reviewer, prevent-self-review enabled, and a protected-branch
deployment policy. No approval run was dispatched. The organization-owned App
is not installed on this personal-owner approval repository, and the Factory
API still has no dispatcher, ALM issue binding, workflow-run verifier, or
Environment-review verifier. Approval remains fail-closed with
`APPROVAL_PROVIDER_NOT_CONFIGURED`.

A separate `pilot-deploy` GitHub Environment now requires `chex123` as its sole
reviewer, with `triplexapps` initiating the workflow. Self-review prevention,
protected-branch-only deployment, and disabled administrator bypass remain in
force. The OIDC workflow, IAM role, and role-ARN repository variable are on
`main`; no image deployment has run yet. The active IAM user still has
administrator-group permissions and can bypass this workflow through direct
AWS APIs.

No isolated runtime worker or complete SDLC orchestrator is connected. Explicit
runtime/e2e/simulation requests still fail with
`RUNTIME_EVIDENCE_NOT_CONFIGURED`; do not infer runtime behavior from source
evidence. Do not send credentials in chat.