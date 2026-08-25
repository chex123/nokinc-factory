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
