# Factory Evolution

## Preflight Core — Slice A

- Run preflight before push to discover deterministic defects locally; remote
  protected-branch evidence remains authoritative for merge.
- Capture the complete working tree relative to an explicit base, including
  committed, staged, unstaged, and untracked content.
- Reproduce CI-equivalent local gates exactly when later slices execute them.
- TaskContext is explicit, real, and provider-validated; issue text is data.
- Candidate and TaskContext digests bind review evidence to content so edits make
  older results stale.
- `REVIEW_UNAVAILABLE` is distinct from `CHANGES_REQUIRED`.
- Findings may be reconciled as `REJECTED/WITH_EVIDENCE`, not blindly accepted.
- Provider serialization is strict before owned schema validation.
- Protected-branch evidence remains remote-authoritative.