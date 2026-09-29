# Agent workflow instructions

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Commands you must run before opening a PR

```bash
ruff check src tests          # lint
mypy --strict src             # types — must be clean
pytest -q                     # all tests
pytest tests/acceptance -q    # acceptance scenarios specifically
```

## What "done" means

A task is done when every command above exits zero **and** the acceptance
scenarios that were merged before you started still pass unmodified.

For any change to Factory runtime behavior or its production image, passing
unit/acceptance tests is not enough to call it PR-ready or deploy-ready. Before
creating a PR, pushing its branch, or dispatching `deploy-pilot`, run the local
production-like validation in `docs/LOCAL-PILOT-VALIDATION.md`. Exercise the
changed behavior through the locally started Factory against the real configured
repositories using read-only GitHub App access, and verify both the assistant
result and durable `CHAT_TURN_RESERVED` / `CHAT_TURN_RECORDED` trace. Use a
separately bounded, explicitly authorized provider probe; fakes do not count as
provider or E2E evidence. Never perform writes to the three connected
repositories during this smoke. Docs-only changes do not require provider calls,
but must still pass focused docs/policy tests and lint.

If a required prerequisite is absent (AWS/GitHub credentials, PostgreSQL,
provider access, or safe local runtime), stop and report the exact check as
`BLOCKED` with the missing prerequisite and evidence. Do not create the PR,
push, dispatch, or claim readiness until the local scenario passes or the user
explicitly accepts that named residual risk. Deployment workflow quality gates
remain required as a second, independent layer; they do not substitute for the
local scenario.

## Rules for this repository

- **You may not modify anything under `tests/acceptance/`.** Those are frozen
  contracts merged in a separate PR by a different task. If you believe an
  acceptance test is wrong, say so in the PR body and stop. Do not edit it.
- **You may add unit tests freely** under `tests/unit/`.
- If a test fails twice with the same error signature, stop and explain in the PR
  body rather than attempting a third fix.
- Do not add dependencies not listed in `pyproject.toml` without saying why.

## Repository layout

```
src/nokinc_factory/
  domain/     schemas — the contracts. Change carefully.
  policy/     deterministic decision tables. No LLM calls here, ever.
  ports/      Protocol definitions. Provider-neutral.
  adapters/   provider-specific. All vendor logic lives here.
  agents/     PydanticAI agents.
  gates/      gate runner + toolchain adapters.
  mcp/        MCP server — read-mostly developer surface.
docs/factory-spec.md    the normative specification
```
