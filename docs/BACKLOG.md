# Backlog — derived from `docs/MVP.md`

`docs/MVP.md` is authoritative for MVP scope and sequence. This file is only an execution checklist; if it disagrees with MVP.md, MVP.md wins and this file must be regenerated.

## Foundation already present / must stay green

- domain state and authorization primitives
- deterministic impact classification
- factory CLI skeleton
- protected GitHub workflow templates
- payment target boots with real OpenTelemetry
- Python target unit + acceptance baseline tests

## Slice 1 — one-repo loop (MVP-A)

1. GitHub Issues `WorkItemPort`
2. Protected `gate-approval.yml` with decision-digest input and provider run evidence
3. `factory gate` dispatches the protected approval workflow; labels are projections only
4. Domain Expert → `BusinessReady`, including refusal behavior
5. Architect → `SolutionReady`
6. Frozen Test Author PR → baseline must fail
7. Implementer PR → frozen paths immutable
8. deterministic Python gates + secret/SCA checks
9. privileged cross-model review triggered only after unprivileged gates; PR diff is data, never executed
10. dev deploy + OTLP/Jaeger
11. trace work item → PR → commit → artifact → span

**Exit:** `nokinc-demo-payments` goes from conversation to running assured service.

## Slice 2 — developer experience

12. minimal deterministic RI: repo/file catalogue, tree-sitter symbols, references, OpenAPI, test inventory
13. Context Builder v0 using deterministic RI only
14. read-mostly Factory MCP surface
15. `factory init`

No embeddings/RAG yet.

## Slice 3 — multi-repo (MVP-B)

16. `SimpleChangeSet`: exact base SHAs, per-repo worktrees, ordered PRs, sequential merge
17. TypeScript Toolchain adapter
18. HCL/Terraform Toolchain adapter
19. rerun the same approved story across payments + SDK + infra

**Exit:** one story produces coordinated, reviewed changes in three repositories.

## Slice 4 — closing evidence

20. story/work-item id in commit trailer, artifact metadata and spans
21. `factory trace`
22. risk classifier visible in workflow output
23. rehearse the >500 refund/manual-review scenario and trace coverage demo

## Deliberately post-MVP

Production ChangeSet recovery (`PARTIALLY_MERGED`) · ReleaseBundle/DeploymentBinding · pre-prod/canary · embeddings/RAG · self-hosted model cascade · Temporal · hardened SandboxPort (gVisor/microVM) · second ALM/provider · strangler mode.
