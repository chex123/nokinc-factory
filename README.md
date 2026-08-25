# Nokinc Software Factory

A platform that turns a conversation into a production-deployed,
assurance-designed service.

- **Normative specification:** `docs/factory-spec.md`
- **How to build it:** `docs/BOOTSTRAP.md` — start here
- **First stories:** `docs/BACKLOG.md`

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q && mypy --strict src && ruff check src tests
```

## What exists today

```
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
