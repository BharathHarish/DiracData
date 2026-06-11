# DiracData Workspace Guide

This root is intentionally small.

Read order:

1. `v2/AGENT.md` for new development.
2. `v2/docs/context_fabric.md` for the current architecture.
3. `v1/AGENT.md` only when you need to inspect the preserved implementation.

Rules:

- Do not add new package code at the root.
- Keep root-level `.env` and `.venv` shared.
- Keep active local data under `v2/data/`.
- Treat `v1/` as preserved reference code.
- Treat `v2/` as the active rebuild lane.
