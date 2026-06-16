# DiracData v2 Agent Notes

Read this folder before touching v2 code:

1. `README.md` explains the rebuild boundary.
2. `docs/context_fabric.md` defines the product shape.
3. `src/diracdata_v2/context/contracts.py` defines the first stable data contracts.
4. `tests/test_context_contracts.py` protects the contract shape.

## Responsibilities

v2 is intentionally independent. Do not import from legacy local experiments in
v2 package code or v2 scripts. If an old helper is still useful, re-create the
narrow v2-native contract instead of coupling lanes.

Keep the runtime model:

- `schema_graph` and `schema_ast` describe schema traversal.
- `sql_library` is reusable pattern memory.
- `semantic_catalog` is the searchable runtime context layer.
- `context_compiler` produces compact context packets.
- agents should consume compiled context, not whole catalogs.

## Shared Assets

Use root-level assets instead of copying them:

- environment: `../.env`

Use v2-local runtime inputs:

- data: `data/`
- query history: `data/query_history/`
- metadata descriptions: `context/metadata_descriptions.json`
