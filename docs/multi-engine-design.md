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
wrong — the reconciler must be independent of any source. The reconciler is a locked-down DuckDB
connection (`enable_external_access=false` except the result-parquet paths; `memory_limit` +
`temp_directory` so it **spills to disk instead of OOMing**; `threads` capped — all `Config` fields).
Sources produce parquet; the reconciler joins parquet. **It does not run in the agent process** — it
runs inside an isolated execution worker; see §3.6. One fresh reconciler connection per job.

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

### 3.6 Execution and isolation model (runtime infra)

**The requirement:** the agent (LLM) loop must survive a large/expensive combine, an engine OOM, or a
hang — with a clean error it can re-plan from — AND the reconciler must not be able to over-provision
the agent's host. Same-machine isolation (thread *or* process) contains a *crash* but does **not** give
the reconciler its own CPU/RAM budget; a pathological sort can still starve the box. Scalability
therefore requires running the heavy SQL **off-host, in a separately provisioned sandbox.**

**The enabler: bulk data already lives in the object store, not the agent's memory.** Result parquets
are keyed `results/<schema>/<rid>.parquet` in S3/MinIO. So the reconciler can run *anywhere that can
reach the object store*: it pulls the input result-keys from S3, combines, writes the output parquet
back to S3, and returns only the envelope. **Bulk never transits the agent host** — the host ships
`{result-keys, sql}` and gets back `{result-key, envelope}`.

**The seam: `Executor` — one interface, backend selected by ENV (`DIRACDATA_EXECUTOR`).** The agent /
`ResultStore` depend only on this interface.

| backend | isolation | data path | when |
|---|---|---|---|
| `inline` (default) | in-process DuckDB, **bounded**: `memory_limit`→catchable OOM + interrupt-on-timeout for hangs | local temp → object store | dev / small / self-contained (no extra deps) |
| `sandbox` | remote DuckDB runtime with its **own CPU/RAM**, independently scaled | reads+writes the **same object store** by key; host never sees bulk | production / demanding SQL |

- OOM survival is delivered **without a subprocess**: the reconciler's `memory_limit` turns an
  over-budget combine into a *catchable* `OutOfMemoryException` → the tool returns a clean error → the
  agent re-plans. Hangs: an interrupt watchdog (`con.interrupt()` on `exec_job_timeout_s`).
- The `sandbox` backend is a **pip extra** (`pip install diracdata[sandbox]`) + ENV
  (`DIRACDATA_SANDBOX_URL`/token/image) — nothing heavy in the core install. Its contract is
  **provider-agnostic** (`{result-keys, sql}` → `{result-key, envelope}`), so a self-hosted DuckDB
  container, E2B, Modal, or a remote reconciler-service all drop into the same seam.
- **Reference sandbox:** one small DuckDB sandbox server — the *same code* runs as a subprocess (local
  tests, real OS isolation on one host) or as a container (prod, off-host). The remote is that
  interface pointed at a URL.

**Defense in depth (still holds):** push-down keeps inputs `O(answer)` (§3.4); a pre-flight guard
refuses inputs over `Config.fetch_max_rows/bytes`; DuckDB `memory_limit` + `temp_directory` spill to
disk; the `sandbox` backend adds true host isolation + independent provisioning; timeout+interrupt
turns hangs into errors.

**Config:** `executor` (`inline`|`sandbox`), `exec_job_timeout_s`, `sandbox_url`, `sandbox_token`,
`fetch_max_rows/bytes`, plus the reconciler knobs — all `Config` fields, no literals.

**Back-compat:** `executor="inline"` (the default) runs exactly as today; the single-source path is
unchanged and needs no extra process or dependency. `sandbox` turns on explicitly via ENV.

**Build order:** 1.5a = the `Executor` seam + bounded `inline` backend (OOM+hang survival) — the
default, ships now. 1.5b = the `sandbox` backend + reference DuckDB sandbox (off-host scaling).

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
- Connection pooling & concurrency: source drivers + the reconciler live in a **bounded pool of
  worker processes** (§3.6), one fresh reconciler connection per job — isolated so an OOM/crash kills a
  worker, not the agent; stateless and horizontally scalable; never one shared connection.
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
- New package `diracdata.execution` — the `Executor` protocol + `LocalProcessPoolExecutor` (§3.6).
  The agent/`ResultStore` depend on the interface; a consumer can inject a remote backend. Optional:
  the default single-source path runs inline with no worker processes.
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

Every phase lists **Touches** (the concrete modules changed — engines, `ResultStore`/tools, the
**agent skeleton**, **prompts**, **learning**, config) so nothing agent-facing is implicit.

### Phase 0 — `diracdata.engines` skeleton (no behavior change)
- **Engines:** `base.py` (`QueryEngine` protocol + `AbstractEngine`), move `DuckDBEngine` →
  `engines/duckdb.py` (thin re-export from `utils/duckdb_engine.py` for compat), `registry.py`
  (`EngineSpec` + `SourceRegistry` with a **single-source synth** from today's `Config`).
- **Agent skeleton:** none yet — `agent.py`/`loop.py`/tools still receive one engine (the registry
  hands back the synthesized default).
- **Prompts / learning:** none.
- **Config:** none new (reads existing `sql_engine`/`data_root`/`schema`).
- **Tests:** `EngineContract` conformance harness (parametrized), run against `DuckDBEngine`.
- **Gate:** 171 green + conformance passes; imports unchanged.

### Phase 1 — Arrow result contract + reconciler + `combine_results` (inline)
- **Engines:** `to_parquet` via Arrow batches in `AbstractEngine` (DuckDB keeps native `COPY`);
  `engines/arrow.py` type canonicalization scaffold.
- **ResultStore:** own a **separate locked-down DuckDB reconciler** (`memory_limit`+`temp_directory`
  spill; not a source); generalize `query(result_id, sql)` → `combine(result_ids, sql)` binding many
  parquets; store combine output as a new `result_id` (faithfulness holds).
- **Tools (`tools/query.py`):** add **`combine_results(result_ids, sql)`** (DuckDB dialect); register
  in `tools/__init__.build_tools`; numbers land in `WorkingMemory` like `run_sql`.
- **Agent skeleton:** `tools/__init__` + `agent.py` pass the reconciler to `ResultStore`; loop
  unchanged. **`run_sql` signature unchanged** (still single default source).
- **Prompts:** `analyst.md` gains a short "to join two stored results, use `combine_results`" note;
  `sql_rules.md` unchanged.
- **Config:** `reconciler_memory_limit`, `reconciler_temp_dir`, `reconciler_threads`, `executor`
  (`"inline"` default).
- **Tests:** two-DuckDB combine, ASOF freshness, nulls, nested round-trip, spill-on-large.

### Phase 1.5a — `diracdata.execution` seam + bounded `inline` backend (§3.6)
- **Execution (new pkg):** `Executor` protocol + `InlineExecutor` — wraps each materialize with the
  reconciler's `memory_limit` (OOM → catchable) and an interrupt watchdog (`con.interrupt()` on
  `exec_job_timeout_s`) so hangs become errors. No multiprocessing.
- **ResultStore / tools:** `run`/`combine` go **through the executor**; the tool try/except turns an
  OOM/timeout into a clean error string the agent re-plans from.
- **Agent skeleton:** `ResultStore` owns/receives the executor (default `InlineExecutor`); loop + tool
  call-sites unchanged.
- **Config:** `executor` (`inline`), `exec_job_timeout_s`.
- **Tests:** combine OOM (tiny memory_limit) → clean error + agent survives; a hang → interrupt →
  error; inline is the default; suite green.

### Phase 1.5b — `sandbox` backend + reference DuckDB sandbox (off-host scaling)
- **Execution:** `SandboxExecutor` — submits `{result-keys, sql}` to a sandbox that reads/writes the
  **same object store** and returns `{result-key, envelope}`; provider-agnostic contract; client as a
  `diracdata[sandbox]` pip extra, selected by `DIRACDATA_EXECUTOR=sandbox` + `sandbox_url`/token ENV.
- **Reference sandbox:** one small DuckDB sandbox server, the same code as a subprocess (local test,
  real OS isolation) or a container (prod, off-host).
- **Tests:** subprocess sandbox does a real combine via object-store handoff (bulk never touches the
  host); host crash/OOM in the sandbox → clean error; `inline` still the default.

### Phase 2.0 — Provision + seed the fintech Postgres (test/UAT data)
The connector is worthless without a real Postgres to point it at. This is an explicit deliverable.
- **Provision:** a local Postgres (macOS: `brew install postgresql@16` + a local data dir + a
  `fintech` database — no sudo). Documented in `docs/postgres-setup.md`; CI/other devs point
  `DIRACDATA_TEST_PG_DSN` at any Postgres.
- **Seed:** `scripts/setup_fintech_pg.py` — idempotently create and populate **`orders`** and
  **`payments`** in the fintech Postgres (realistic columns incl. a `jsonb`/array/`timestamptz` so the
  type tests are real; deterministic seed so numbers are reproducible). Reads the DSN from ENV
  (`DIRACDATA_SOURCE_ORDERS_PG_DSN` / `DIRACDATA_TEST_PG_DSN`), never a literal. Prints row counts +
  a couple of ground-truth aggregates for hand-verification.
- **Lake side (for cross-source):** a small `customers` dimension seeded into a local DuckDB/parquet
  lake so ME-FIN-04 can join Postgres `orders`/`payments` against a different engine.
- **Tests:** `scripts/setup_fintech_pg.py --check` verifies the seed is present + counts match
  (skips/att errors clearly if no PG); a unit test asserts the seeder is idempotent against a
  throwaway PG when `DIRACDATA_TEST_PG_DSN` is set.

### Phase 2 — PostgresEngine + source registry loaders
- **Engines:** `engines/postgres.py` (ADBC/connectorx → Arrow; read-only txn; statement timeout;
  identifier quoting); `registry.from_env` / `from_yaml` (secrets from ENV, redacted); driver as an
  install-extra with a clear missing-driver hint.
- **Arrow:** finish `arrow.py` canonicalization — `jsonb`→canonical JSON text, arrays→list, decimals,
  `timestamptz`→UTC.
- **Agent skeleton / prompts / learning:** none (Postgres reachable as an engine, not yet wired into
  the agent's tools — that's Phase 3).
- **Config:** `sources`, per-source spec fields.
- **Tests:** `EngineContract` against the **seeded** Postgres (**skips without `DIRACDATA_TEST_PG_DSN`**);
  complex-type round-trip PG→Arrow→parquet→reconciler using the seeded `jsonb`/array/`timestamptz`.

### Phase 3 — Wire multi-source into the agent (skeleton + tools + prompt + dialect)
This is the **agent change** phase — split so each delta is reviewable.
- **3a — sourced tools + skeleton:**
  - `tools/query.py`: **`run_sql(source, sql)`** selects the engine from the registry, validates in
    *that* dialect, echoes the dialect used; back-compat: `source` optional, defaults to the sole/
    default source so existing calls and tests are unchanged.
  - `tools/navigation.py`: `get_tables/describe_columns/get_columns/profile_column/find_examples`
    gain an optional `source` arg (default source).
  - `agent.py` / `tools/__init__.build_tools`: accept a **`SourceRegistry`** (back-compat: a single
    engine is wrapped into a one-source registry); thread it to tools + `ResultStore`.
  - `agents/loop.py`: no control-flow change — tools just carry `source` now.
- **3b — estate catalog + dialect in the prompt + routing:**
  - `context/`: an `EstateCatalog` renderer (per-source dialect/tables/freshness/bindings → the
    estate-map block, §5) built from the registry + fabric/`bindings.json`.
  - **Prompts:** `analyst.md` + `framing.md` inject the estate map and the "SELECT per source in its
    dialect; combine in DuckDB" rules; per-source `dialect_<engine>.md` concatenated only for sources
    in play; `verify.md` gains cross-source grain/freshness checks.
  - `agents/framing.py`: framing **picks the source(s)** for the intent from the catalog (agentic, no
    ENV policy) — reuses the router muscle.
  - `agents/verify.py`: faithfulness spans sources (a number traces to a stored result **from a named
    source**); verifier checks as-of/grain alignment.
- **Config:** `estate` name; catalog rendering caps.
- **Tests:** `run_sql(source,…)` default-source regression; a real **PG↔lake cross-source question
  end-to-end** (per-source reduce → `combine_results` → verified answer with an as-of label).

### Phase 4 — Cross-source learning (learning agent + estate)
- **Learning:** `learning/fabric_agent.py` runs **per source** (keyed `fabric/<estate>/<source>/…`;
  `profiler.py` unchanged — already engine-agnostic); a new **binding-discovery pass**
  (`learning/bindings.py`): propose candidate cross-source keys (name/type/domain overlap) → sample
  each in its source → overlap via the reconciler → write verified `fabric/<estate>/bindings.json`.
  New prompt `prompts/learn_bindings.md`.
- **Estate:** `EstateCatalog` reads `bindings.json`; `scripts/learn.py` gains `--estate` (learn N
  sources + bindings).
- **Experiences:** curator taxonomy already open — cross-source bindings/gotchas curated at runtime
  (no code change; a note in `prompts/curate.md`).
- **Tests:** overlapping ids → correct edge + overlap %; disjoint ids → **no** false binding;
  per-source fabric written under the estate.

### Phase 5 — Lifecycle/GC + observability
- **ResultStore:** `gc(run_id, keep=cited)`, `sweep(older_than_turns)`, `close()`; run-namespaced
  keys; cited-result protection wired from the finish gate.
- **Agent skeleton:** `agent.py` calls `gc` per turn and `close` on exit; per-source query log
  (sql/rows/bytes/ms) appended to the transcript.
- **Config:** `result_ttl_turns`, `results_prefix`, retention mode.
- **Tests:** cited survive, scratch swept, object-store keys deleted, temp cleaned; retention ∞/0.

### Phase 6 — More connectors + optimizations
- **Engines:** `engines/mysql.py`, `engines/trino.py` (each just passes `EngineContract`); optional
  DuckDB-attach single-statement path for attachable sources.
- **Deferred (earn-its-keep):** isolated `run_python` escape hatch only if a real query proves DuckDB
  insufficient.
- **Tests:** conformance for each new connector; a 3-store (`postgres`+`lake`+`mysql`/`trino`)
  reconcile.

**Agent-facing change ledger** (which phase touches what — the parts you flagged as missing):

| Module | Phase | Change |
|---|---|---|
| `tools/query.py` | 1, 3a | `combine_results` added (1); `run_sql(source, sql)` (3a) |
| `tools/navigation.py` | 3a | optional `source` arg on every nav tool |
| `tools/__init__.build_tools` | 1, 1.5, 3a | reconciler + executor + registry wiring |
| `agent.py` (skeleton) | 1, 1.5, 3a, 5 | owns reconciler/executor/registry; gc+close |
| `agents/framing.py` | 3b | picks source(s) from the estate catalog |
| `agents/verify.py` | 3b | cross-source faithfulness + as-of/grain checks |
| `prompts/analyst.md`,`framing.md`,`verify.md` | 1, 3b | combine note; estate map; cross-source rules |
| `prompts/dialect_<engine>.md`, `learn_bindings.md` | 2, 4 | per-dialect notes; binding-discovery prompt |
| `learning/fabric_agent.py`, `learning/bindings.py` | 4 | per-source learning + binding discovery |
| `context/EstateCatalog` | 3b | render dialect/tables/freshness/bindings into the prompt |
| `scripts/setup_fintech_pg.py`, `docs/postgres-setup.md` | 2.0 | provision + seed orders/payments in Postgres (+ lake customers dim) |

Each phase ends with: the full suite green, new conformance/integration tests added, and the
single-source default path proven unchanged.
