# Nokinc Software Factory

A platform that turns a conversation into a production-deployed,
assurance-designed service.

- **Normative specification:** `docs/factory-spec.md`
- **How to build it:** `docs/BOOTSTRAP.md` — start here
- **First stories:** `docs/BACKLOG.md`
- **Verified readiness (12 September 2026):** [audit](docs/READINESS-2026-09-12.md)
- **Completion and commercial scope:** [delivery plan](docs/DELIVERY-PLAN.md)
- **Browser chat and agent roles:** [chat and roles](docs/CHAT-AND-AGENTS.md)
- **Current implementation and test evidence:** [delivery ledger](docs/DELIVERY-LEDGER.md)

The current implementation is a tested foundation, **not yet an end-to-end or
production-ready factory**. See the audit for executed simulations and remaining
blockers; the historical layout summary below is not a completion claim.

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q && mypy --strict src && ruff check src tests
```

## What exists today

```text
src/nokinc_factory/
  domain/    states · authorization · story · identity     ← schemas, done
  policy/    impact classification                          ← done
  ports/                                                    ← next
tests/
  acceptance/   FROZEN contracts. Never edited by an implementation PR.
  unit/
```

## The governing principle

> Deterministic systems establish evidence; AI forms interpretations where
> deterministic logic is insufficient; deterministic verification and policy
> determine whether consequential action is permitted.

Short version: **AI reasons. Deterministic controls govern.**
