# DiracData v2 Agent Notes

Read this folder before touching v2 code:

1. `README.md` explains the rebuild boundary.
2. `docs/context_fabric.md` defines the product shape.
3. `src/diracdata_v2/context/contracts.py` defines the first stable data contracts.
4. `tests/test_context_contracts.py` protects the contract shape.

## Responsibilities

v2 is intentionally small. Do not port v1 abstractions unless they directly
serve the context fabric.

Keep the runtime model:

- `schema_graph` is the lossless source of truth.
- `sql_library` is the reusable pattern memory.
- `context_compiler` will eventually produce a compact `context_slice`.
- agents should consume slices, not whole catalogs.

## Shared Assets

Use root-level assets instead of copying them:

- environment: `../.env`
- old implementation: `../v1`

Use v2-local runtime inputs:

- data: `data/`
- query history: `data/query_history/`
- metadata descriptions: `context/metadata_descriptions.json`
