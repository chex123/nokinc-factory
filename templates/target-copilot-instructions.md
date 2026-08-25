# Nokinc Factory-enabled target

This repository is governed by `docs/factory-spec.md` and `.factory/` metadata.

- Deterministic controls are authoritative; model output is a proposal.
- Read the linked story, approved design and frozen tests before implementation.
- Never modify frozen acceptance/contract/regression tests, fixtures, feature files or step bindings from an implementation task.
- Use the language-specific commands in `.factory/toolchain.yaml`; do not substitute a different toolchain to get a green result.
- Preserve assurance wiring and declared span topology.
- Do not change GitHub protection workflows, CODEOWNERS, `.factory/` policy or security controls unless the work item explicitly authorizes that control-plane change.
- Do not add dependencies without stating why.
- Fail closed when a required capability is unavailable.
