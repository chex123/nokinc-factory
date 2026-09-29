# AWS and model setup gates

## Completed locally

- Organization-owned GitHub App created: `NOK-Apps Factory Pilot`.
- App ID: `5020848`.
- Installation ID: `163491501`.
- Live allowlist and repository metadata now include `NOK-Apps/flur-sdk`,
  `NOK-Apps/flur-frontend`, and private `NOK-Apps/flur-backend`.
- GitHub App private key stored in AWS Secrets Manager in `us-east-1`.
- Local PEM removed after upload.
- Nonsecret routing is recorded in `config/pilot.yaml`.
- AWS Secrets Manager-backed GitHub App broker is implemented and tested.
- Repository-scoped short-lived installation-token transport is implemented and tested.
- Runtime loads `pilot.yaml`, cross-checks App/installation/secret identifiers,
  and limits GitHub access to its configured repository allowlist. The live
  repository metadata endpoint returns all three pilot repositories; it does
  not write repository content.
- Cognito managed login now uses authorization code + PKCE, state/nonce checks,
	signed ID-token validation, host-only secure session cookies, same-origin checks
	for cookie-authenticated writes, and `/auth/login`, `/auth/callback`, and
	`/auth/logout` routes.
- Terraform now configures the Cognito OAuth callback at
	`https://factory.nokinc.com/auth/callback`, the logout return path, and a
	region-unique managed Cognito login domain prefix.
- AWS CLI v2.37.1 is installed for the current Windows user.
- Native OpenAI, Google Gemini, and Bedrock ModelPort adapters and bounded synthetic probes are implemented.
- The local same-origin `/chat` page now supports Business Analyst, Architect
	and Code Analyst turns; automatic delegation is deterministic keyword routing,
	not an always-running supervisor. The UI keeps the bounded transcript in the
	current browser tab's `sessionStorage` and reuses one tenant/user work item.
- Each model-backed turn is durably reserved in the tenant-scoped Postgres event
	stream before the two provider calls. The currently authorized E2E pilot cap
	is two turns total (`FACTORY_CHAT_MODEL_TURN_LIMIT=2`); reservations survive
	task restarts and later requests fail closed before model invocation.
- The server trace stores only per-turn content digests, model identities/run
	IDs, context digest and timings. Raw prompts, transcript text, source claims and
	quotes are not persisted in the workflow event.
- Four live synthetic provider connection probes returned the exact configured
	model/family identities. The first estimated `$20` synthetic-test allowance
	was consumed. A separate estimated `$20` allowance covered two live chat
	turns; both were durably reserved and returned 502 without a recorded result.
	On 27 September the user authorized five additional turns (ten provider
	calls). On 29 September this was superseded by explicit authorization for
	uncapped production testing. The target is
	`FACTORY_CHAT_MODEL_TURN_LIMIT=unlimited`; the live task still has limit 2
	until the reviewed change is deployed through the protected workflow. There
	is no hard dollar ceiling. Actual billing and model quality remain unverified.
- Local runtime assembly now loads the exact architecture and coding model pairs
	from `pilot.yaml`, enforces distinct doer/reviewer families, caps output at
	1,536/512 tokens, and routes repository discussions through one doer plus one
	independent reviewer. Fake-provider end-to-end tests pass; live provider
	qualification remains pending.
- The source reader retrieves a bounded, content-addressed GitHub snapshot and
	blocks common credential patterns in file contents before model transmission.
	The live allowlist now includes the private backend repository; the pattern
	scanner is not a complete secret or PII detector.

## User gates before broader live testing

### Gate A — AWS runtime placement

Choose the initial AWS runtime:

- recommended: ECS/Fargate in `us-east-1`;
- one control-plane VPC and private subnets;
- NAT or approved egress path for GitHub/model APIs;
- RDS PostgreSQL or an approved existing PostgreSQL service;
- CloudWatch logs/metrics and CloudTrail enabled.

Selected: new VPC, ECS/Fargate in `us-east-1`, and private RDS PostgreSQL. The
pilot ALB now serves `https://factory.nokinc.com` using certificate
`arn:aws:acm:us-east-1:441186133046:certificate/4e34808a-2db1-4aab-a245-2e851e304857`.
Hostinger DNS has the ACM validation CNAME and a `factory` CNAME to
`pilot-factory-529645038.us-east-1.elb.amazonaws.com`; the existing apex and
`www` records were left unchanged. The ALB has TLS 1.3/1.2 on port 443 and
redirects port 80 to HTTPS. Public checks returned 301 for HTTP health, 200 for
HTTPS health, and 302 from `/auth/login` to Cognito.

### Gate B — IAM task role

Terraform creates the ECS task role. Its current inline policy is scoped to the
pilot's named secrets and approved Nova Pro model. Minimum intended permissions:

- `secretsmanager:GetSecretValue` only for approved secret ARNs;
- `kms:Decrypt` only for the KMS key encrypting those secrets, if customer-managed;
- `bedrock:InvokeModel` only for approved Bedrock model ARNs;
- CloudWatch log/metric write permissions;
- no IAM administration, no unrestricted Secrets Manager access, no repository credentials in environment variables.

The live task role initially used model secret ARNs without Secrets Manager's
generated suffix, which caused `implicitDeny`. The three model-secret entries
were corrected to their exact ARNs; IAM policy simulation now returns `allowed`
for the three configured `GetSecretValue` resources and Nova Pro `InvokeModel`.
This manual role update is outside the partial Terraform state; the ECS service
must not be reconciled with an un-targeted Terraform apply.

### Gate C — Exact model/provider identities

Use provider-native exact API IDs, not internal labels: `gpt-6-luna`,
`gemini-3.8-flash`, and `amazon.nova-pro-v1:0`. The factory cannot
qualify aliases.

For each model provide:

- exact API model ID;
- provider and runtime region;
- API route: OpenAI native, Google Vertex AI, or AWS Bedrock;
- context/token limits;
- price ceiling;
- data-retention/training setting;
- qualification expiry date.

Recommended runtime routing:

- OpenAI models: OpenAI native API from ECS, credential in Secrets Manager;
- Gemini: the selected Google native API route;
- Amazon Nova Pro: AWS Bedrock using the ECS task role.

AWS cannot natively run arbitrary OpenAI or Gemini model IDs through Bedrock.

### Gate D — Google model credential path

You selected the native Google API route rather than Vertex federation. Create the
Google API credential under the Google provider account, store it in AWS Secrets
Manager as `models/google/coding-reviewer`, and never place it in ECS environment
variables or the repository.

If you later switch to Vertex AI, use AWS-to-GCP workload identity federation; do
not store a long-lived Google service-account key in AWS Secrets Manager.

### Gate E — Provider secrets

Store only these references in AWS Secrets Manager:

- `github/apps/nokinc-factory-pilot/private-key` — already populated;
- `models/openai/coding` — stored;
- `models/openai/architecture-business` — stored;
- `models/google/coding-reviewer` — stored.

No API key or private key belongs in this repository, chat, ECS environment variables, or model context.

### Gate F — Human and spend authority

Before additional live chat calls or wider production use:

- name a second human reviewer for production-sensitive actions;
- The protected `pilot-deploy` environment requires only `chex123`; `triplexapps` initiates the run. This is one reviewer in addition to the initiator, not two additional reviewers; it does not change T2 branch-protection approval rules or the separate gate-approval workflow.
- deploy `FACTORY_CHAT_MODEL_TURN_LIMIT=unlimited` through the protected
	workflow for the explicitly authorized uncapped test. This has no hard dollar
	ceiling; preserve durable reservation and audit behavior.
- approve the exact model-family independence matrix;
- approve permitted data locations and provider retention settings.

Current selected routes: synthetic/masked data only; OpenAI GPT-6 Luna is the
doer for coding, architecture, and business; Google Gemini 3.8 Flash reviews
coding; Amazon Nova Pro reviews architecture and business. GPT-6 Luna must pass
the bounded synthetic qualification probe before the routes are marked qualified.
Qualification records actual provider token usage and dated list-price estimates;
there is no dollar-spend call cutoff. Statistical quality/cost qualification
remains pending. The previous Anthropic route was unavailable to this AWS
account and is not qualified.

### Gate G — staging identity and environment isolation

- The live pilot ECS service now runs OIDC on task revision 18; the prior HMAC
  secret injection was removed from the task definition.
- `staging` and `production` plans reject HMAC and require OIDC issuer, audience,
	HTTPS JWKS URL, tenant claim, and role claim configuration.
- Cognito mode provisions an admin-created-only user pool and OAuth code-flow
	client, with PKCE callback and logout URLs on the Factory HTTPS origin. Runtime
	login requires all hosted-domain/callback settings and validates the Cognito
	ID-token nonce before setting an HttpOnly `__Host-` session cookie.
- Pilot Cognito pool `us-east-1_0lYZRro5j`, app client
  `3jblrfnf9kd7vt3cq8jsslulij`, and managed login domain
  `nokinc-factory-pilot-441186133046.auth.us-east-1.amazoncognito.com` are active.
	One admin-managed pilot test user is confirmed with tenant ID `00001` and the
	`operator` group. First login, password change, and TOTP enrollment have been
	completed by the user.
- OIDC tokens must have a valid RS256 signature and verified tenant/role claims;
	the application does not accept tenant or role values from request bodies.
- Database worker secrets and RDS endpoint hostnames must match the selected
	environment. The live pilot continues to use its existing private RDS instance.
- Cognito admin-created users need an email, immutable `custom:tenant_id`, and
	intended group (`operator` for the scoped GitHub access probe). The tenant
	claim must be supplied at user creation because it is immutable.

### Gate H — provider-backed human approvals

The local `gate-approval.yml` validates a GitHub issue and decision digest, then
runs two Environment jobs in sequence: `gate-N-triplexapps`, followed by
`gate-N-chex123`. Before either approval job, it reads both Environment
protections and fails closed unless each has exactly its expected GitHub user as
the required reviewer and prevents self-review. It records only a run-reference
comment after both jobs. The API still returns
`APPROVAL_PROVIDER_NOT_CONFIGURED`; the comment does not authorize work.

The active workflow is on the default branch. A read-only check confirmed all
eight `gate-1..4` / `triplexapps` and `chex123` Environments have exactly one
expected reviewer, `prevent_self_review=true`, and protected-branch-only
deployment policy. No workflow was dispatched and no approval was recorded.

End-to-end wiring requires:

- A verified ALM issue reference linked to each durable work item (current API
	intake creates a local `wi-*` identifier only).
- The approval workflow deployed on the approval repository's default branch,
	with required reviewers and self-review prevention configured for each gate.
- GitHub App `Actions: write` permission, accepted in GitHub, plus an App
	installation that actually includes the approval repository. The current
	organization-owned installation does not cover the personal-owner repository
	`chex123/nokinc-factory`; the current GitHub CLI token cannot list App
	installations (403).
- A dispatcher/status adapter that binds the completed workflow run to the exact
	tenant, canonical ALM work item, gate, and decision digest; it must also
	retrieve and verify both GitHub Environment review records before resuming
	execution.

Until an App credential covers the approval repository and canonical issue
binding, dispatch, run/status verification, and Environment-review verification
exist, approval requests remain fail-closed and do not authorize work.

The separate `pilot-deploy` environment requires only `chex123`, prevents
self-review, allows protected branches only, and has administrator bypass
disabled. `triplexapps` initiates the workflow. This protects only image
deployment and does not connect `/gate` to GitHub or supply the missing approval
App installation.

## Current deployment boundary

 The current image is
 `pilot/nokinc-factory:pilot-chat-multirepo-backend-allowlist-20260927-01`,
 digest `sha256:551a903d61735f253e0c4c0387bfa0380577070d2abca9047b09dc68ceedb513`,
 running on ECS revision 18. No Git commit or push was made. The task has
`FACTORY_CHAT_MODEL_TURN_LIMIT=2` and `FACTORY_CHAT_MODEL_TENANT_ID=00001`;
revision 18 preserves the tenant setting and Postgres atomically reserves each
turn before provider invocation. The deployment circuit breaker and rollback
are enabled. Public health, chat, CSS, and JavaScript requests return 200; the
authenticated repository endpoint returns metadata for all three allowlisted
repositories.
Authenticated trace reads show both authorized turns (Business Analyst and
frontend Code Analyst) have `INTAKE` and `CHAT_TURN_RESERVED`, but no
`CHAT_TURN_RECORDED`; both returned 502 without assistant output. CloudWatch
contains generic Uvicorn 502 access lines only. The two-turn allowance is
still enforced by the live task. On 29 September the user authorized uncapped
testing; the `unlimited` runtime setting is not deployed yet. Actual billing is
unverified, and the application has no hard dollar ceiling.

CloudWatch filters for both old failures returned only access lines and no
`ERROR` records. Local unpublished code now logs only failure stage, exception
class, and fixed provider diagnostic code; it does not log error text, response
bodies, prompts, or credentials.

ECR Basic scan completed for revision 18's image with zero findings. Its
Chainguard Wolfi runtime has no Perl executable and uses zlib `1.3.2.1-motley`.

For the next deployment, current `main` includes a main-only GitHub workflow,
an immutable-tag/ECR-scan gate, digest-pinned task-definition promotion, and a
repository skill requiring this path. The AWS GitHub OIDC provider and
`pilot-factory-github-deploy` role are configured; trust is restricted to
`repo:chex123/nokinc-factory:environment:pilot-deploy`, and IAM simulation
confirmed the role is denied other ECR repos, ECS services, and IAM roles. The
`AWS_PILOT_DEPLOY_ROLE_ARN` repository variable is set. PR #28 merged the
unlimited turn setting as `e408b318`; post-merge gates run `36614842047`
passed. The environment now requires `chex123` as sole reviewer; `triplexapps`
initiates. No image deployment has run yet. The IAM user remains in the
Administrators group and can bypass the workflow with direct AWS calls.

The protected `pilot-deploy` environment requires only `chex123`, with
`triplexapps` as initiator, self-review prevented, protected branches only, and
administrator bypass disabled. This is one independent reviewer in addition to
the initiator, not a three-person approval chain. Account-level configuration
does not by itself prove distinct human ownership.

The broker's AWS secret reference handoff was corrected. The live
`GET /v1/github/repositories` endpoint returns exact metadata for
`NOK-Apps/flur-sdk`, `NOK-Apps/flur-frontend`, and private
`NOK-Apps/flur-backend`, all on `main` and not archived. The bounded App-token
and source-read path for the backend was verified locally; this metadata
endpoint itself does not fetch source files or perform writes.

An authenticated intake smoke test was persisted under tenant `00001`; status
returned `INTAKE_RECORDED` / `REFINING`, and trace contained one `INTAKE` event.
This verifies login, tenant-scoped persistence and status/trace reads, not agent
execution or application changes.

Terraform state is local and currently tracks only the six Cognito resources
created by a targeted apply. The existing VPC/RDS/ECS/ALB stack had no Terraform
state in this workspace; the ALB listener/security-group and ECS revision changes
were applied through AWS APIs and are not imported into Terraform state. Do not
run an untargeted `terraform apply` from this root until the pre-existing stack
has been imported or a shared backend/state strategy is established.

`pilot.yaml` is loaded by the service and limits read-only GitHub access to
`NOK-Apps/flur-sdk`, `NOK-Apps/flur-frontend`, and `NOK-Apps/flur-backend`.
Revision 18 serves the `/chat`
browser interface, deterministic role delegation, a multi-turn Business
Analyst, bounded source retrieval with credential-pattern blocking, exact
repository-qualified citations, different-family review, redacted turn
telemetry, multi-repository selection and durable selected-repository audit.
Raw turn text remains in the current browser tab only. Explicit runtime and
simulation requests fail closed because no isolated execution worker is
connected. Fake-provider tests and PostgreSQL workflow integration tests pass.

The flow is synchronous and has no durable raw conversation replay, outbox
worker, OpenTelemetry exporter, change-request creation, code-writing or
test-execution agent. It does not write to GitHub or advance gates. Human
approval still returns `APPROVAL_PROVIDER_NOT_CONFIGURED`; the API does not
dispatch or verify workflow runs. The active workflow has eight verified
reviewer Environments, but the organization-owned GitHub App does not cover the
personal approval repository. A dispatcher, canonical issue binding, run/status
verification, and Environment-review verification are still required.

The first estimated `$20` synthetic allowance was consumed by four connection
probes. A separate estimated `$20` allowance covered two live turns; both were
reserved, returned 502, and produced no `CHAT_TURN_RECORDED` event. On 29
September the user superseded the five-turn extension and authorized uncapped
testing. The target is `FACTORY_CHAT_MODEL_TURN_LIMIT=unlimited`; the live
revision-18 cap remains 2 until the reviewed change is deployed. There is no
hard dollar ceiling in the application. The
remaining production blockers include provider diagnosis/model qualification,
an isolated runtime worker, durable full-SDLC orchestration, provider-backed
approvals, production policy/SLO/recovery decisions, and shared Terraform
state. The full SDLC is not connected.

## Execution after gates

1. Preserve the current Cognito session controls; the authenticated browser currently returns metadata for all three repositories.
2. Publish the local safe-diagnostic and scan-gated deployment changes; use the `pilot-deploy` workflow with a unique tag and `model_turn_limit=unlimited`, then inspect the safe diagnostic code before further provider testing.
3. Install a dedicated least-privilege GitHub App on the personal approval repository, or move the workflow to an App-covered organization repository; provide a genuinely distinct human reviewer.
4. Implement canonical issue binding, Actions dispatch, run/status checks, and Environment-review verification before any `/gate` request can authorize work.
5. Build and qualify an isolated runtime worker, then connect it to durable SDLC orchestration; runtime asks remain fail-closed until complete.
6. Complete statistical model qualification, independent review, production policy decisions, and a shared Terraform state/import plan before wider rollout.
