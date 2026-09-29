# Local Pilot Validation Gate

Before creating a PR, pushing a branch, or dispatching `deploy-pilot`, run this
gate for every change that affects the Factory application or image. It is a
fail-fast local SDLC check; GitHub CI and the protected deploy workflow are
additional controls, not substitutes.

## Required evidence

1. Run the frozen local quality baseline from the Factory repository root:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests\unit -q
   .\.venv\Scripts\python.exe -m pytest tests\acceptance -q
   .\.venv\Scripts\python.exe -m compileall -q src scripts
   .\.venv\Scripts\mypy.exe --strict src scripts\deploy_pilot.py
   .\.venv\Scripts\ruff.exe check src tests scripts
   .\.venv\Scripts\pip-audit.exe . --strict
   ```

2. Start the Factory locally using its documented dev profile and an isolated
   disposable PostgreSQL database. Do not point a local process at the live ECS
   database. Use the real GitHub App installation-token adapter in read-only
   mode and prove access to all configured repositories:

   - `NOK-Apps/flur-sdk`
   - `NOK-Apps/flur-frontend`
   - `NOK-Apps/flur-backend`

   Capture repository identity/default branch, but never print installation
   tokens, secret values, or repository source in test summaries.

3. Send one synthetic, read-only chat request through the local Factory HTTP
   application to each repository. For each request verify:

   - the selected repository identity is the requested allowlisted repository;
   - the user-visible result is successful and its citations bind to retrieved
     source, or the response is an explicit safe `NOT_AVAILABLE` outcome;
   - Postgres contains `CHAT_TURN_RESERVED` followed by `CHAT_TURN_RECORDED` for
     a successful response, with no transcript text in the trace;
   - a provider failure is reported with a redacted diagnostic and no fabricated
     answer. Do not retry a real provider failure automatically.

   All smoke questions and repository operations must be read-only. Do not
   create issues, branches, commits, PRs, or modify repository files. Use no
   customer data. Bound every model request by an explicitly configured call
   count and output-token limit, and record the exact allowance consumed.

4. Run `scripts/qualify_models.py` only as a separate provider-qualification
   step, not as a substitute for app E2E. It makes real calls to every configured
   model route (default maximum four calls, 256 output tokens); run it only when
   explicitly authorized and with its call limit stated in the evidence.

## Blocked is not ready

Before starting, record whether local AWS identity, the GitHub App token path,
the three repository reads, disposable PostgreSQL owner/worker URLs, provider
credentials, and local app configuration are available. Never inspect or print
secret values.

### Current local capability boundary

As of this runbook's authoring, the repository has unit tests with fake GitHub
transports/providers and a PostgreSQL integration suite that skips without
`FACTORY_TEST_DATABASE_URL` and `FACTORY_TEST_WORKER_URL`. The
`scripts/qualify_models.py` command makes bounded synthetic provider calls but
does not start the Factory app, read the three repositories, or verify a chat
trace. There is **no checked-in local real-repository chat E2E runner** yet.
Do not describe those existing tests as production-like E2E coverage.

The current workstation also has no AWS CLI or AWS environment credentials and
no disposable PostgreSQL owner/worker URLs. The Factory App private key and
provider credentials are referenced in AWS Secrets Manager, so GitHub CLI
access to repository metadata alone does not exercise the App-token or chat
provider paths. Until an approved local credential flow, disposable database,
and actual local E2E runner are available, runtime changes are `BLOCKED` before
PR/push/deployment. Docs-only or test-harness-only changes may proceed through
their normal local tests without making provider calls, but they do not clear
the runtime E2E gate.

If any prerequisite is missing, any repository cannot be read, no durable trace
can be verified, the response fails, or the model's behavior is not qualified,
record that cell as `BLOCKED` with the exact command/status and missing
prerequisite. Do not create the PR, push, dispatch deployment, or claim the
Factory feature is production-ready. Do not work around a blocked local gate by
testing against live ECS. Ask the user only for the specific prerequisite that
requires their action.

Record for each run: commit SHA, environment/profile, repository, exact command
and exit code, response status, trace event kinds, provider-call count, and
remaining blockers. Do not include credentials, raw prompts, customer data, or
full source snapshots in the report.