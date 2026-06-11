# DiracData

This workspace now has two lanes:

- `v1/`: preserved first implementation, including the prior package, scripts, docs, and tests.
- `v2/`: clean-room rebuild focused on a minimal context fabric.

Shared runtime assets stay at the root:

- `.env`
- `.venv`
- `.diracdata/`

Start new development in `v2/` unless you are explicitly inspecting or borrowing from `v1/`.

v2 owns the active local data copy:

- `v2/data/`
- `v2/data/query_history/`
- `v2/context/`
