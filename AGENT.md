# DiracData Workspace Guide

This root is intentionally small.

Read order:

1. `v2/AGENT.md` for new development.
2. `v2/docs/context_fabric.md` for the current architecture.

Rules:

- Do not add new package code at the root.
- Keep root-level `.env` and `.venv` shared.
- Keep active local data under `v2/data/`.
- Treat `v2/` as the active rebuild lane.
- Keep legacy local experiments such as `v1/` out of Git.
