# Nokinc Factory — repository instructions

You are building a **software factory**: a platform that turns a conversation into
a production-deployed, assurance-designed service. The normative specification is
`docs/factory-spec.md`. **Read the relevant Part before implementing.** Do not
invent architecture that contradicts it.

## Non-negotiable invariants

1. **Deterministic first.** If plain code can answer it, plain code answers it.
   Reach for an LLM only for interpretation, correlation or novel patterns.
2. **LLMs reason. Policy governs.** An LLM never authorizes its own conclusion.
   Producers propose; deterministic policy decides.
3. **Fail closed.** Unknown is never "safe". Unclassifiable impact promotes to
   human review. A missing capability reports `NOT_AVAILABLE`, never `PASS`.
4. **Own the schemas.** Third-party tools produce inputs; the formats are ours.
5. **Bind to content, not labels.** Approvals and evidence bind to digests.
   `version: 7` is a label; a digest is content.

## Architecture you must not contradict

- **Three kinds of state**: ALM owns business lifecycle; the workflow engine owns
  execution; the approval system owns authorization evidence. ALM authority over
  *position* never confers *execution authorization*.
- **Repository Intelligence** is deterministic (AST, LSP, contract graph). A
  Retrieval Planner routes questions. Embeddings discover; the symbol table verifies.
- **Test Author and Implementer are separate.** Acceptance and contract tests are
  frozen before implementation and are immutable to the Implementer.
- **Ports and adapters** for everything provider-specific. Adapters declare
  capabilities. The core is provider-neutral.

## Stack

Python 3.12 · Pydantic v2 · PydanticAI (agents) · FastAPI · SQLAlchemy 2 +
Postgres · official `mcp` SDK · tree-sitter + multilspy (RI) · pytest + pytest-bdd.

**Do not add LangChain or LangGraph.** Business state lives in the ALM and
execution state in Postgres; a durable graph checkpointer would be a second store
for a solved problem. See spec Part 15.

## How to work

- **Type everything.** `mypy --strict` must pass. No bare `Any`, no untyped dicts
  crossing a module boundary. Pydantic models at every boundary.
- **Docstrings state the *why*,** especially for security-relevant code. Reference
  the spec Part. A reader must understand what breaks if the rule is removed.
- **Small, single-purpose modules.** If a file exceeds ~300 lines, split it.
- **No network calls in unit tests.** No sleeps. No real clocks — inject `now`.
- **Never** write code that reads secrets from the environment and logs, prints or
  returns them. Anything reachable by a model-controlled shell is compromised.

## Definition of Done for any issue here

- Acceptance scenarios in `tests/acceptance/` pass (they were merged first)
- `mypy --strict`, `ruff`, `pytest` all green
- Diff coverage ≥ 90% on changed lines
- Public behaviour documented in the module docstring
- No new dependency without justification in the PR body

## Things that are wrong here even though they look normal elsewhere

- Catching an exception and returning a default. **Fail closed and raise.**
- `if not x: x = safe_default()` for anything security-relevant.
- Regex over source text where an AST query exists.
- A tool that returns `True` when it could not actually check.
- Mutating a frozen artefact (acceptance test, approved ChangeSet, snapshot).
