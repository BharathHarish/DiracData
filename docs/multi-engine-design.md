# Multi-Engine DiracData — the unified analyst across data stores

Status: **design** (no code yet). Owner: analyst-harness. Target branch: `multi-engine`.

## 0. Thesis and non-goals

DiracData becomes a **unified analyst** across heterogeneous data stores (Postgres, a lake, MySQL,
Trino, DuckDB, …) — one agent that frames a question, decides *which store(s)* hold the answer,
writes correct per-dialect SQL, pushes work **down** to each engine, and reconciles the small
results, then **verifies** the number and **learns** the estate.

Two hard boundaries keep this lean and winnable:

- **We are a reasoning layer, not an execution engine.** We do *not* build a distributed query
  optimizer (that is Trino/Starburst/Spark). We push aggregates down to each source and reconcile
  small results in an embedded DuckDB. The moat is the reasoning + verification + the learned map of
  a messy estate — not federated execution.
- **We are a framework, not a SaaS.** Every capability is a cleanly importable Python package with a
  programmatic API first; ENV/YAML are *loaders*, not the interface. No hosted assumptions, no hidden
  global state, no service required to `import diracdata`.

**The invariant that makes it scale:** the reconciler only ever sees data already reduced to
`O(answer)`, never `O(source)`. A genuine large row-level join with no reducing predicate is *not*
a reconciler job — it is pushed to a real engine (Trino, or materialized into a warehouse). Knowing
that boundary is a routing decision, not a failure.

## 1. Seams we already have (build on, don't rebuild)

| Existing | What it already does | Reused as |
|---|---|---|
| `memory/results.py::ResultStore` | materializes a full result to **parquet in the object store**, returns a compact envelope (columns, dtypes, row_count, preview ≤100/≤200); `query(result_id, sql)` runs **DuckDB SQL over the stored parquet** as table `result` | the combine substrate — generalize one→many results |
| `utils/duckdb_engine.py::DuckDBEngine` | `dialect`, `list_tables/columns`, `describe_columns`, `query(sql,max_rows)`, `copy_to_parquet`, `describe_query` | the **reference `QueryEngine`** — its surface becomes the protocol |
| `learning/profiler.py::column_facts` | engine-agnostic (`engine.query`) one-pass null%/distinct/domain | per-source profiling, unchanged |
| `learning/fabric_agent.py::LearningAgent` | per-table agentic learning over `engine`, writes fabric per `schema` | per-source learning + a new cross-source binding pass |
| `context/fabric.py::FabricStore` | schema-keyed artifacts + runtime state over the object store | estate/source-keyed catalog + `bindings.json` |
| `routing/` agentic router | main model picks model+budget from a catalog | extended: framing also picks **source(s)** from the estate catalog |
| `Config.sql_engine`, `Config.data_root`, object-store config | single-engine knobs already in ENV | become the *default* source in a multi-source registry |

The architecture below is mostly **generalizing three things** (Engine → protocol, one result → many,
one schema → an estate) plus **one new connector** (Postgres) and **one new artifact** (bindings).

## 2. Core abstraction: `diracdata.engines` (new package)

A self-contained, importable package. One-way deps: `engines → config/utils`. The agent depends on
the **registry + protocol**, never a concrete engine.

```
src/diracdata/engines/
  __init__.py      exports QueryEngine, AbstractEngine, EngineSpec, SourceRegistry, DuckDBEngine, PostgresEngine
  base.py          QueryEngine (Protocol) + AbstractEngine (shared: Arrow→parquet, quoting, read-only, timeouts)
  duckdb.py        DuckDBEngine   (reference impl; moved from utils, re-exported for compat)
  postgres.py      PostgresEngine (first external connector)
  registry.py      EngineSpec (one source's config) + SourceRegistry (name→lazy engine) + from_env/from_yaml
  arrow.py         canonicalization at the Arrow boundary (types, JSON/array, tz) — shared by all engines
```

### 2.1 The `QueryEngine` protocol

Exactly today's `DuckDBEngine` surface, plus an **Arrow contract** so the store is engine-agnostic:

```
class QueryEngine(Protocol):
    name: str                 # source name, e.g. "orders_pg"
    dialect: str              # "duckdb" | "postgres" | "mysql" | "trino" | ...
    read_only: bool

    def list_tables() -> list[str]
    def list_columns(table) -> list[str]
    def describe_columns(table) -> list[{column_name, column_type}]

    def fetch(sql, max_rows) -> QueryResult          # bounded preview for tool responses
    def describe_query(sql) -> list[{column_name, column_type}]   # types without a full run
    def to_parquet(sql, out_path) -> int             # FULL result → local parquet, returns row count
```

`AbstractEngine` supplies the shared behavior so a connector is ~50 lines:
- `to_parquet` **default** = stream the query as **Arrow RecordBatches** and write parquet with
  `pyarrow` (never buffer the full result in memory). DuckDB **overrides** with native
  `COPY (…) TO … (FORMAT PARQUET)` for zero-copy — so single-source performance is unchanged.
- identifier quoting + dialect-correct casing (Postgres folds unquoted to lowercase; DuckDB/Trino
  differ) → one `quote_ident`/`quote_literal` per dialect.
- read-only enforcement (Postgres `SET default_transaction_read_only=on`; DuckDB `read_only`) +
  a per-source statement timeout.

`DuckDBEngine` already satisfies this protocol → **Phase 0 is a move + a conformance test, zero
behavior change.**

### 2.2 Connector kinds (two, one contract)

1. **External-client connectors** (the general path): Postgres, Trino, Snowflake, BigQuery,
   ClickHouse. Talk to the source over its own driver, return **Arrow**, land parquet. Works for
   *any* engine; native dialect + native pushdown per source. Postgres is first.
2. **DuckDB-attach optimization** (later, opportunistic): for sources DuckDB can attach
   (`postgres_scanner`, `mysql_scanner`, `httpfs`/parquet), a single DuckDB statement can push a
   filter+aggregate down and read the lake in one shot. Free perf where available; **not** required
   and **not** universal (no Trino/Snowflake extension). Sequenced to Phase 6, behind the same
   protocol.

The **spine is #1 + a DuckDB reconciler**; #2 is a bonus for attachable sources.

### 2.3 Sources in ENV — no hardcoding, framework-first

Programmatic API is canonical; ENV/YAML are loaders. Secrets (DSNs) **only** from ENV/secret store,
never in code or YAML literals.

```python
# canonical: construct it
reg = SourceRegistry([
    EngineSpec(name="orders_pg", kind="postgres", dialect="postgres", dsn=os.environ["ORDERS_DSN"]),
    EngineSpec(name="users_lake", kind="duckdb", data_root="…/lake", read_only=True),
])
# or load from ENV
reg = SourceRegistry.from_env()          # DIRACDATA_SOURCES=orders_pg,users_lake + per-source keys
# or from a YAML manifest whose secrets interpolate ${ENV}
reg = SourceRegistry.from_yaml("sources.yaml")
```

ENV pattern for N sources (all `DIRACDATA_*`, defaults in `Config`):

```
DIRACDATA_SOURCES=orders_pg,users_lake
DIRACDATA_SOURCE_ORDERS_PG_KIND=postgres
DIRACDATA_SOURCE_ORDERS_PG_DSN=postgresql://…      # secret; never logged, redacted in transcripts
DIRACDATA_SOURCE_ORDERS_PG_READ_ONLY=true
DIRACDATA_SOURCE_ORDERS_PG_TIMEOUT_S=30
DIRACDATA_SOURCE_USERS_LAKE_KIND=duckdb
DIRACDATA_SOURCE_USERS_LAKE_DATA_ROOT=/data/lake
```

Back-compat: with no `DIRACDATA_SOURCES`, the registry synthesizes **one** source from today's
`sql_engine`/`data_root`/`schema` → the current single-source path is byte-identical (0 regression).

## 3. Result storage and the combine layer

### 3.1 What is stored where — the three tiers

Raw source tables **never** get copied. Only reduced results do.

| Tier | Holds | Format / location | Lifetime |
|---|---|---|---|
| Source engines | raw tables | in Postgres / the lake | untouched |
| Reconciler (embedded DuckDB) | pushed-down aggregates, mid-join | **Arrow**, in-memory, per turn | ephemeral |
| Result store | reduced result envelopes | **parquet** at `results/<estate>/<run_id>/<rid>.parquet` (object store) | policy §4 |

Arrow is the **in-memory interchange** (zero-copy between a connector and the reconciler); parquet is
the **durable at-rest** form (audit, faithfulness, reuse). We do **not** persist Arrow to the object
store — parquet is the on-disk representation of the same columnar data.

### 3.2 The reconciler is a *separate* DuckDB, not a source

Today `ResultStore` uses `self.engine` (the source DuckDB) to slice results. In multi-engine that is
wrong — the reconciler must be independent of any source. `ResultStore` gains a dedicated,
locked-down DuckDB `:memory:` **reconciler connection** (`enable_external_access=false` except the
result-parquet paths; `memory_limit`; `threads` capped — all `Config` fields). Sources produce
parquet; the reconciler joins parquet. Clean separation of concerns.

### 3.3 `combine_results` — the cross-source join primitive

Generalize `ResultStore.query(result_id, sql)` (one result as `result`) to bind **many**:

```
combine_results(result_ids=["r1","r2"], sql="SELECT … FROM r1 ASOF JOIN r2 USING(user_id) …")
```

Each `result_id` is bound as a DuckDB view over its parquet, named by its id. The output is **stored
as a new `result_id`** → the finish-gate faithfulness check ("every number traces to a stored
result") extends for free; the combine step is not a hole in verification. `combine_results` always
speaks the **DuckDB dialect** (stated in the tool doc + estate prompt).

### 3.4 Large results and the push-down contract

- Per-source `run_sql(source, sql)` runs *in the source*, streams the full result to parquet via
  Arrow batches, returns only the envelope (§3.5). Big output lives on disk, never in context.
- The planner/verify enforce **"aggregate at source, reconcile small"**: a `combine_results` input
  that is `O(source)` (an un-aggregated pull of a big table) is a rejected plan, not a slow one. A
  soft guard (row/byte cap per fetch, `Config.fetch_max_bytes`) trips before memory does; the agent
  is told to add a reducing predicate or push the join down.
- Genuine large joins with no reducing predicate → routed to a real engine (Trino / warehouse
  materialization), explicitly *not* the reconciler.

### 3.5 The 100-row shape view (unchanged envelope, now cross-engine)

The envelope the agent already receives is the shape view: `columns`, `dtypes`, `row_count`,
`preview` (≤100 rows, or ALL if ≤200), `truncated`. On top of it:

- **Nulls / distinct on demand**: a small `profile_result(result_id)` reusing `profiler.column_facts`
  over the reconciler → per-column `null_pct`, `distinct`, `is_unique_key`, domain sample. Same
  one-pass aggregate already proven for the learning profiler; no new stats code.
- Preview values that are nested/large are rendered as **truncated JSON text** with a `has_nested`
  flag and the per-column type, so the agent sees structure without flooding context.

## 4. Ephemeral result lifecycle (cleanup policy)

Results accumulate (per-turn outputs, combine intermediates, learning samples). Policy, all knobs in
`Config`:

- **Layout**: `results/<estate>/<run_id>/<rid>.parquet` — namespaced by run so a sweep is a prefix
  delete. `run_id` = conversation turn (or a learning run).
- **Two classes**:
  - *durable* — results whose numbers were **cited in the answer** (protected for the conversation's
    life, for audit/faithfulness/replay).
  - *scratch* — combine intermediates, pushdown samples, uncited results (deletable after the turn).
- **Mechanisms** (the object store already has `delete` + `list_keys`):
  - process-scoped local temp dir (already `tempfile.mkdtemp`) → auto-removed on exit;
  - `ResultStore.gc(run_id, keep=cited_ids)` deletes scratch keys after each turn;
  - a TTL sweep `ResultStore.sweep(older_than_turns=Config.result_ttl_turns)` for durable results past
    retention or not referenced by the transcript;
  - `ResultStore.close()` / context-manager so an embedding app controls teardown deterministically.
- **Default**: nothing leaks — scratch swept per turn, durable swept by TTL, temp auto-cleaned. A
  framework consumer can set retention to ∞ (audit mode) or 0 (stateless mode).

## 5. Dialect and datastore context in the prompt

The analyst must know, per source: name, **dialect**, tables/columns (or a summary), **freshness**,
and cross-source bindings — plus that combining is DuckDB dialect. Rendered as an **estate map**
injected at framing/authoring (alongside today's `dialect_<engine>.md`):

```
ESTATE (3 sources):
- orders_pg   [postgres, real-time]      orders(order_id, user_id, amount, created_at), refunds(…)
- users_lake  [duckdb/parquet, daily as-of 00:00 UTC]  users(user_id, …), user_facts(user_id, segment, …)
- events_ch   [clickhouse, ~5 min lag]   events(user_id, ts, kind, props JSON)
CROSS-SOURCE BINDINGS (verified):
- orders_pg.user_id = users_lake.user_id   (99.8% overlap on a 50k sample)
- events_ch.user_id = users_lake.user_id   (97.1%)
RULES: SELECT from a source in ITS dialect via run_sql(source, sql). To join across sources, first
reduce each in its source, then combine_results([...], sql) in DuckDB dialect. Mind freshness skew:
user_facts is as-of 00:00; orders/events are fresher — align (ASOF) or state the as-of boundary.
```

- Tools carry the source: **`run_sql(source, sql)`** (selects the engine, echoes the dialect used);
  **`combine_results(result_ids, sql)`** (DuckDB). Navigation tools (`get_tables`, `describe_columns`,
  `find_examples`) gain a `source` argument; per-source `dialect_<engine>.md` notes are concatenated
  only for the sources in play (keeps the prompt lean).
- The estate map is rendered from the `SourceRegistry` + the catalog/`bindings.json` — no hardcoding.

## 6. Cross-source learning pipeline

Today the learning agent runs per-table over one engine into `fabric/<schema>/…`. Multi-source adds
one scope level and one new artifact.

- **Per-source learning** (unchanged mechanics): for each source, `LearningAgent(engine=source)` runs
  its per-table loop; artifacts land at `fabric/<estate>/<source>/{metadata_descriptions,
  value_domains, join_graph}.json`. `profiler.py` already engine-agnostic → no change.
- **Cross-source binding discovery** (new): candidate keys proposed from name+type+value-domain
  overlap across sources (e.g. `orders_pg.user_id` vs `users_lake.user_id`). **Verified by a real
  cross-source overlap**: sample each candidate's distinct values in its source → two parquet
  results → `combine_results` intersect/overlap in the reconciler → confidence (% overlap). Confirmed
  edges → `fabric/<estate>/bindings.json`. This *is* the learned map of the estate; it can't be found
  by any single engine, which is exactly why it's defensible.
- **Runtime augmentation**: `experiences.md` (already async, agentic) additionally learns cross-source
  bindings, gotchas ("events_ch.user_id is hashed; join needs decode"), and freshness leads from
  verified runs. Estate catalog = compiled (learning) + curated (experiences).
- **Scale**: learning is per-source and embarrassingly parallel; binding discovery works on *samples*,
  never full tables (the same push-down-and-reduce invariant).

## 7. Second- and third-order concerns (explicit)

Grouped; each has a home in the design so none is a surprise.

**Type system (Arrow boundary, `engines/arrow.py`)**
- Numeric precision/decimals (Postgres `numeric` → Arrow decimal128; avoid float coercion for money).
- Timestamps & timezones: normalize to **UTC** at the boundary; carry tz; `timestamptz` vs naive.
- **JSON/JSONB** → canonical UTF-8 JSON text (DuckDB re-parses with `json_extract`); `has_nested` flag.
- **Arrays / structs / lists** → Arrow `list`/`struct` (DuckDB has native LIST/STRUCT); round-trip
  preserved through parquet.
- `uuid`→text, `enum`→text, `bytea`/blob → type-tagged, **value omitted** unless explicitly requested
  (never pull megabytes into context/parquet).
- Unsupported/opaque types → represented by a type tag so the agent knows to cast, not guess.

**Correctness across sources**
- Freshness/as-of skew (the driving example) — per-source freshness in the catalog; verifier checks
  temporal consistency; ASOF joins; answers labeled with the as-of boundary.
- Identifier case-folding & quoting differences (per-dialect quoting).
- NULL ordering / collation differences in sorts.
- Unit/grain/currency mismatches across sources — binding metadata + the verify gate.

**Safety / ops**
- Read-only enforced per source; per-source statement timeout; `validate_sql` per dialect.
- Secrets: DSNs from ENV/secret store, **redacted** in transcripts and logs.
- Partial failure: a source down → degrade, report which source failed, never fabricate.
- Pushdown cost/guardrails: row/byte caps; reject `O(source)` combine inputs; warehouse $ awareness
  (bytes scanned) surfaced to the router.
- Schema drift between learn-time and query-time → introspect-on-demand + stale-catalog detection.
- Connection pooling & concurrency: a pool per source; the reconciler is one ephemeral `:memory:`
  connection **per in-flight turn** (stateless, horizontally scalable) — never one shared connection.
- Auth expiry (Snowflake/BigQuery OAuth) → connector refresh hook.
- Observability: per-source query log (sql, rows, bytes, ms) into the transcript.

**Deferred (earn-its-keep, not in the base)**
- Arbitrary-Python reconciliation (fuzzy entity resolution, stats/ML SQL can't express) → an
  **isolated** `run_python` escape hatch, added only when a real query proves DuckDB insufficient.
- DuckDB-attach single-statement federation (Strategy A) for attachable sources.

## 8. Framework packaging (importable, no SaaS)

- New package `diracdata.engines` with a clean public API (`from diracdata.engines import
  SourceRegistry, EngineSpec, QueryEngine, DuckDBEngine, PostgresEngine`). Same "optional package"
  pattern as `streaming`/`routing`/`experiences`.
- The agent takes a `SourceRegistry` (or, back-compat, a single engine). No global state; a consumer
  constructs the registry in Python, or loads from ENV/YAML.
- Connectors are **extras**: `pip install diracdata[postgres]`, `[trino]`, `[mysql]` → each pulls its
  driver (`adbc-driver-postgresql`/`connectorx`, `trino`, …). Core install needs only DuckDB. A
  missing driver raises a clear install hint, never a crash on import.
- Public protocol `QueryEngine` means **third parties add a connector** without touching core — the
  conformance suite (§9) is the contract they implement.

## 9. Test strategy (local, and genuinely good)

The whole unit suite must run with **zero external services** (like today's MinIO-optional tests).

- **Engine conformance harness** (the centerpiece): one parametrized `EngineContract` test class every
  connector must pass — `list_tables/columns`, `describe_query` types, `fetch` bounds, `to_parquet`
  row count + Arrow types, read-only rejection of writes, identifier quoting, NULL handling, and
  complex-type (JSON/array/timestamp) round-trip. Run against DuckDBEngine always; against
  PostgresEngine when a PG DSN is present.
- **Multi-source + combine, no external deps**: a *second DuckDB* (different db/dir) stands in as
  "another engine." Two sources with overlapping `user_id`s → per-source reduce → `combine_results`
  join → assert cross-source correctness, ASOF freshness alignment, null handling, JSON/array
  round-trip through parquet.
- **Postgres integration** (`DIRACDATA_TEST_PG_DSN`, **skips when unset** — it is unset on this
  machine today): the same `EngineContract` + a real cross-source PG↔lake question end-to-end.
  Fixture options: a throwaway container, `testing.postgresql`, or a dev PG.
- **Binding discovery**: two sources with overlapping ids → correct edge + overlap %; disjoint ids →
  **no** false binding.
- **Lifecycle/GC**: cited results survive `gc`, scratch swept, object-store keys deleted, temp dir
  cleaned; retention ∞ / 0 both honored.
- **Regression (0 tolerance)**: the current 171 stay green. DuckDBEngine behavior is identical through
  the protocol; the synthesized single-source path is byte-identical to today. All multi-source
  behavior is opt-in (a `SourceRegistry` with >1 source / a flag); the default path is untouched.

## 10. Implementation phases (each shippable, tested, behind flags, 0 regression)

- **Phase 0 — `diracdata.engines` skeleton.** `QueryEngine` protocol + `AbstractEngine`; move
  `DuckDBEngine` in (re-export from `utils/duckdb_engine.py` for compat); `EngineSpec`/`SourceRegistry`
  with a back-compat single-source synth; the `EngineContract` conformance harness. *No behavior
  change.* Gate: 171 green + conformance passes for DuckDB.
- **Phase 1 — Arrow result contract + reconciler + `combine_results`.** `to_parquet` via Arrow
  (DuckDB keeps native COPY); a dedicated locked-down DuckDB reconciler in `ResultStore`;
  `combine_results(result_ids, sql)` tool storing output as a new result. Tests: two-DuckDB combine,
  ASOF, nulls, nested round-trip. Single-source path unchanged.
- **Phase 2 — PostgresEngine + registry loaders.** ADBC/connectorx → Arrow; ENV/YAML source loaders
  (secrets from ENV); read-only + timeout; `arrow.py` canonicalization (jsonb/array/timestamptz).
  Conformance test skips without `DIRACDATA_TEST_PG_DSN`.
- **Phase 3 — Estate catalog + routing in the prompt.** Render the estate map (per-source
  dialect/tables/freshness/bindings); `run_sql(source, sql)` + sourced navigation tools; framing picks
  source(s); a real PG↔lake cross-source question end-to-end.
- **Phase 4 — Cross-source learning.** Per-source learning keyed by source + binding discovery
  (sample-overlap via reconciler) → `bindings.json`, injected into the catalog.
- **Phase 5 — Lifecycle/GC + observability.** Retention config, `gc`/`sweep`/`close`, cited-result
  protection, per-source query log.
- **Phase 6 — More connectors + optimizations.** MySQL (driver or DuckDB `mysql_scanner`), Trino
  (client); optional DuckDB-attach single-statement path for attachable sources; optional isolated
  `run_python` escape hatch (only if a real query needs it).

Each phase ends with: the full suite green, new conformance/integration tests added, and the
single-source default path proven unchanged.
