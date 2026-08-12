# Catalog-level Agent — Design

**Branch:** `catalog-level-agent` (off `main` @ `40f912a`).
**Status:** design for review · no code changes yet.
**Motivation:** every current data platform (Snowflake, Databricks, BigQuery,
Trino, Postgres, DuckDB) is `catalog → database → schema → table → column`.
DiracData today stops at "schema" — one fabric per schema, no notion of
a container. This works for one demo dataset, breaks the moment we point at
a real customer's account or the Spider 2.0 benchmark's 30 databases.

## 0 · The rule that governs everything

> **Single-DB is a degenerate case of multi-DB.**

- Everything at query-time is scoped to `(catalog, database)` — the second may
  be inferred from context, never hardcoded.
- Every fabric artifact is filed under a catalog. The existing single-schema
  fabrics migrate to `catalog="local"`.
- Cross-database joins are a first-class concept — rare, but the abstraction
  must accommodate them so we can reason about them agentically.
- **Zero regressions** on existing single-DB flows (retail_complex,
  fintech_complex) — a compatibility shim makes them look like catalogs with
  one database.

## 1 · Hierarchy + terminology

Pick once, stick with it. Different engines call these differently, so we
normalise to industry-standard terms:

| DiracData term | Snowflake | Databricks UC | BigQuery | Postgres | DuckDB |
|---|---|---|---|---|---|
| **Catalog** | account | catalog | project | cluster | instance/file |
| **Database** | database | schema | dataset | database | attached db |
| **Schema** | schema | *(elided — flat)* | *(elided)* | schema | schema (`main`) |
| **Table** | table | table | table | table | table |
| **Column** | column | column | column | column | column |

For our first three real catalogs:
- **`local`** — DuckDB reading Parquet on MinIO. Databases: `retail_complex`, `fintech_complex`.
- **`spider2_local`** — DuckDB reading 30 ATTACHed SQLite blobs. Databases: `chinook`, `northwind`, `f1`, `IPL`, … 30 total.
- **`fintech`** — DuckDB reading the data_harness `lake/fintech/` (parked on WIP branch, not this line of work).

Schema is almost always `main` today (SQLite default, DuckDB default). We keep it
in the hierarchy so future Postgres/Databricks connectors don't need another
retrofit.

## 2 · Data model

```python
@dataclass
class Catalog:
    name:           str
    engine:         str                  # duckdb, snowflake, bigquery, databricks, trino, postgres
    connection:     Dict[str, Any]        # engine-specific: s3_endpoint, snowflake_account, etc.
    databases:      Dict[str, Database]   # keyed by database name
    cross_db_joins: List[Join]            # optional — FK relationships across databases
    catalog_metrics: List[Metric]         # optional — unified metrics across databases
    catalog_examples: List[Example]       # optional — cross-DB gold seeds
    description:    str = ""              # human-authored one-liner

@dataclass
class Database:
    name:           str
    catalog:        str                   # parent
    schemas:        Dict[str, Schema]     # default: {"main": Schema(...)}
    joins:          List[Join]            # within-database joins (from V3-S1 behavioural)
    metrics:        List[Metric]          # blessed metrics (from semantic_layer.yaml)
    examples:       List[Example]         # gold NL-SQL seeds (from gold_pairs.json)
    description:    str = ""

@dataclass
class Schema:
    name:           str                   # "main" by default
    database:       str                   # parent
    tables:         Dict[str, Table]

@dataclass
class Table:
    # Existing shape from V3, plus a fully-qualified name field
    name:           str
    schema:         str
    database:       str
    catalog:        str
    columns:        List[Column]
    row_count:      Optional[int]
    description:    str
    ...

    @property
    def fqn(self) -> str:                # "spider2_local.chinook.main.tracks"
        return f"{self.catalog}.{self.database}.{self.schema}.{self.name}"
```

Backward-compat: today's `Workspace` becomes a **view** onto a single
`Database`. `workspace.tables` continues to return the same shape; internally it
delegates to `catalog.databases[db].schemas["main"].tables`.

## 3 · Fabric layout (MinIO)

Old layout (still readable via shim):
```
diracdata://fabric/<schema>/
  semantic_model.yaml
  metadata_descriptions.json
  semantic_layer.yaml
  gold_pairs.json
  join_facts.json           # V3-S1
```

New layout:
```
diracdata://fabric/catalogs/<catalog>/
  catalog.yaml                          # machine-readable top-level: engine, connection, DB list
  catalog.md                            # ⭐ hierarchical INDEX — see §3a
  cross_db_joins.yaml                   # optional — discovered from workload
  cross_db_metrics.yaml                 # optional — hand-authored or agent-proposed
  cross_db_examples.jsonl               # optional — cross-DB gold NL-SQL pairs
  databases/
    <db_id>/
      database.yaml                     # per-DB metadata (engine dialect, size, table_count)
      database.md                       # ⭐ per-DB INDEX — table list w/ 1-line each + column highlights
      semantic_model.yaml               # unchanged from today (full detail)
      metadata_descriptions.json        # unchanged (column detail incl. runnable recipes)
      join_facts.json                   # unchanged (within-DB joins)
      semantic_layer.yaml               # unchanged (per-DB blessed metrics)
      gold_pairs.json                   # unchanged
diracdata://memory/catalogs/<catalog>/
  experiences.md                        # catalog-level lessons (rare, mostly per-DB)
  databases/<db_id>/experiences.md      # per-DB experiences (current shape, wrapped)
```

## 3a · The `catalog.md` index — the agent's traversable map

**The critical piece for scale.** As a catalog grows to hundreds of databases,
we cannot afford to load every `semantic_model.yaml` into the agent's prompt.
The `catalog.md` index gives the agent a **compact, hierarchical table of
contents** it reads FIRST — enough to route to the right DBs, tables, and
columns — then drills into full artifacts only where needed.

### Two-tier index

**Tier 1 — `catalog.md`** (~5-30 KB, always in framing context):

```markdown
# Catalog: spider2_local
DuckDB reading 30 ATTACHed SQLite databases. Total: 387 tables, 18.7M rows,
covering sports / e-commerce / civic / finance / education / media domains.

## Databases (30)

### chinook (0.8 MB, 13 tables) — digital music store
Classic music-store schema (albums, artists, tracks, playlists, invoices,
customers, employees, genres, media_types). Use for: catalog/genre/artist
analytics, sales by market, customer lifetime value.
Tables: albums (330), artists (275), customers (59), employees (8), genres (25),
invoice_items (2240), invoices (412), media_types (5), playlist_track (8715),
playlists (18), tracks (3503), sqlite_sequence, sqlite_stat1.

### f1 (71 MB, 29 tables) — Formula 1 racing full history
Race results, driver + constructor stats, laps, pit stops, qualifying, seasons.
Use for: race analytics, driver/constructor rankings, YoY comparisons.
Tables: circuits (77), constructor_results (11.9K), constructor_standings
(12.9K), constructors (211), driver_standings (33K), drivers (855), lap_times
(538K), pit_stops (9.6K), qualifying (9.2K), races (1101), results (25K),
seasons (74), sprint_results (100), status (139).

### bank_sales_trading (72 MB, 19 tables, 15 questions) — retail bank + brokerage
Merged retail banking + securities trading schema. Customers, accounts,
transactions, positions, trades, instruments, branches.
Tables: accounts (12.5K), branches (25), customers (10K), instruments (1500),
loans (3200), positions (54K), securities (1500), trades (487K), transactions
(325K), tellers (110), branch_managers (25), …

### … (27 more)
```

**Tier 2 — `databases/<db>/database.md`** (~2-10 KB, loaded when the agent
selects that DB):

```markdown
# Database: chinook (catalog: spider2_local)
Digital music store — reference schema commonly used for teaching SQL joins.

## Tables

### invoice (412 rows) — customer purchase transactions
Grain: one row per purchase. Joins: customers (customer_id), invoice_items.
Cols: invoice_id (PK), customer_id (FK), invoice_date, billing_address,
billing_city, billing_state, billing_country, billing_postal_code, total.

### invoice_items (2240 rows) — line items per invoice
Grain: one row per (invoice_id, track_id). Joins: invoices, tracks.
Cols: invoice_line_id (PK), invoice_id (FK), track_id (FK), unit_price, quantity.

### tracks (3503 rows) — individual songs
Cols: track_id (PK), name, album_id (FK), media_type_id (FK), genre_id (FK),
composer, milliseconds, bytes, unit_price.

### … (10 more)

## Common query patterns (from gold seeds + experiences)
- Revenue by country: sum(invoice.total) group by billing_country
- Top selling artists: join invoice_items × tracks × albums × artists
- Genre popularity: join invoice_items × tracks × genres, count distinct invoice_id
```

Both files are **agent-authored by the learning phase** — same LLM run that
produces `semantic_model.yaml` also emits the per-DB `database.md`, then a
final catalog-summary turn concatenates + summarises → `catalog.md`.

### How the query agent uses these

```
Framing turn 1: read catalog.md (~5 KB)
  → agent sees 30 DBs with one-liners + table lists
  → matches "which markets have highest driver counts" to f1
  → decides: focus on f1

Framing turn 2: read databases/f1/database.md (~5 KB)
  → agent sees 29 tables with grains + join hints
  → identifies: drivers + constructors + races are relevant

Main loop: load semantic_model.yaml + metadata_descriptions.json for f1 ONLY
  → full column detail, join facts, blessed metrics — but only for one DB
```

vs. today's approach (load full fabric always) which would blow up context on
a 30-DB catalog. **This is what makes the abstraction actually scale.**

### For catalog-scope questions (agent picks DB)

- If the question clearly names a DB or domain → agent picks in framing
- If ambiguous ("show me our top customers") → agent may ask via `ask_user`,
  OR run against multiple DBs and reconcile, OR pick a default via experience
- All decisions are agentic — no routing rules in code

### Cross-DB questions

- `catalog.md` + `cross_db_joins.yaml` together give the agent enough context
  to plan cross-DB SQL
- Agent generates fully-qualified `catalog.database.schema.table` references
- Executed via `run_sql_multi` (C6)

**Migration shim**: on read, if `fabric/<name>/semantic_model.yaml` exists
(old layout) → treat it as `catalog="local"`, `database="<name>"`. Writes always
go to the new layout. Zero-touch for existing users.

## 4 · Agent signature changes

### Query agent (`data_analyst`) — catalog-aware end-to-end

Every prompt, every tool, every artifact reference is now
`(catalog, database, schema, table, column)` aware. There is no path that
assumes a single schema.

```python
# Current (still works — implicitly catalog="local"):
data_analyst(schema="retail_complex", ...)

# New — three explicit modes:
data_analyst(catalog="local", database="retail_complex", ...)     # pinned single-DB (most common)
data_analyst(catalog="spider2_local", database="chinook", ...)     # pinned
data_analyst(catalog="spider2_local", ...)                          # catalog-scope — agent picks the DB
data_analyst(catalog="local", databases=["retail_complex","fintech_complex"], ...)   # multi-DB subset
```

Behaviour differences by scope:
- **Pinned (single DB)** — framing skips DB-selection. Loads `database.md` +
  `semantic_model.yaml` for that DB. Tools default `db=` to the pinned value.
- **Catalog-scope (no `database` set)** — framing gains a **DB-selection turn**:
  1. Load `catalog.md` (index) into prompt
  2. Agent picks DB(s) matching the question
  3. Load `database.md` for chosen DB(s)
  4. Enter main loop with per-DB tools
- **Multi-DB (`databases=[...]`)** — framing bounds the search space.
  Cross-DB joins available; agent generates fully-qualified names.

### What "catalog-aware" means concretely in the query agent

| Location | Change |
|---|---|
| System prompt | Explicitly names the catalog + database context. If catalog-scope, explains the index-first traversal pattern. |
| Framing prompt | Loads `catalog.md` (always) + `database.md` (once DB picked). Neither has to be > 30 KB. |
| Tool signatures | `describe_table(db, table)`, `run_sql(sql, db=?)`, `find_examples(query, db=?)` — `db` optional; defaults to context. |
| Tool results | Always include `(catalog, db, schema)` prefix in table/column refs so the agent never confuses two DBs. |
| Fingerprint / audit | Every tool call and every proposal is tagged with catalog + database. |
| Verify sub-agent | Independently checks SQL uses correct DB (e.g., `chinook.tracks` not `northwind.tracks`). |
| Finish gate | Written answer must cite `(catalog, database, table)` for every number reported. |
| Conversation memory | Summaries record which DBs the conversation touched — helps continuity. |
| Experiences | Written per-DB (existing pattern), plus rare cross-DB lessons at catalog level. |

### Learning agent (`learn2`)

```bash
# Current (still works, implicitly catalog="local"):
dirac learn --schema retail_complex

# New:
dirac learn --catalog local --database retail_complex           # single DB
dirac learn --catalog spider2_local --database chinook           # single DB
dirac learn --catalog spider2_local                             # ALL databases in catalog
dirac learn --catalog spider2_local --database chinook,f1,IPL    # subset
```

Fabric writes go to the new layout. Old layout stays read-only.

## 5 · MCP surface evolution

Existing `diracdata-mcp` (single-schema, per-invocation):
```
# args --schema retail_complex
tools: list_tables, describe_table, run_sql, join_path, temporal_coverage, …
```

New: `dirac-catalog-mcp` (catalog-aware, one server per catalog):
```
# args --catalog spider2_local
observation:
  list_databases()                      → [{db, table_count, size_mb, description}]
  describe_database(db)                 → catalog metadata + a table summary
  use_database(db)                      → set session context; subsequent tools default here
  list_tables(db?)                      → explicit or from context
  describe_table(db?, table)            → explicit or from context
  run_sql(sql, db?)                     → explicit; also accepts fully-qualified refs
  find_examples(query, db?)             → gold seed lookup
  get_metric(name, db?)                 → blessed metric lookup

cross-DB (advanced, rare):
  find_cross_db_joins(dbs=[...])        → candidate FK relationships across DBs
  run_sql_multi(sql)                    → executes with `catalog.database.schema.table` refs
```

Existing `diracdata-mcp --schema X` stays; it becomes a thin wrapper that calls
the catalog-aware layer with `catalog="local"`, `database="X"`.

## 6 · Learning-time considerations

- **Per-database learning is independent** — 30 Spider DBs = 30 concurrent
  `learn2` runs. Each writes to its own subfolder. No coordination needed.
- **Cross-DB joins are discovered from workload, not learned upfront** — the
  data modeller (parked on WIP) or a future "cross-catalog analyzer" reads
  query_history for cross-DB patterns and proposes cross_db_joins.
- **Catalog-level description** — hand-authored one-liner per catalog
  ("Spider 2.0-Lite: 30 diverse SQLite DBs across sports/retail/civic/…") so the
  agent has context when doing catalog-scope reasoning.

## 7 · Migration path (existing single-schema fabrics)

Both existing schemas move under `catalog="local"`:

```
fabric/retail_complex/*         →  fabric/catalogs/local/databases/retail_complex/*
fabric/fintech_complex/*        →  fabric/catalogs/local/databases/fintech_complex/*
memory/retail_complex/experiences.md   →  memory/catalogs/local/databases/retail_complex/experiences.md
```

Automated by a one-shot `scripts/migrate_to_catalog.py`. Old paths remain
readable via the shim so nothing breaks mid-migration.

## 8 · What Spider 2.0 becomes under this design

- **1 catalog: `spider2_local`** (engine: duckdb+sqlite)
- **30 databases**: chinook, northwind, f1, IPL, Baseball, EU_soccer, …
- Each database gets its own fabric via `learn2`
- Each Spider question specifies `db` in the manifest → runs pinned
- Any future cross-DB Spider variants → catalog-scope agent handles them

Spider becomes the first non-trivial user of the new catalog layer — proves
30 databases works. `retail_complex` + `fintech_complex` become the
"backward-compat" case in the same test suite.

## 9 · Build phases

| phase | scope | days | exit criteria |
|---|---|---|---|
| **C0** ✅ | Design doc (this) + branch | today | you sign off on §1-8 |
| **C1** | Data model — `Catalog` / `Database` / `Schema` classes + backward-compat shim + tests | 1-2 | `Workspace.from_legacy(...)` returns a catalog view; all 311 existing tests green |
| **C2** | Fabric layout — new writers + shim readers + migration script | 1 | migrate existing `fabric/retail_complex` → `fabric/catalogs/local/…`, retail_complex UAT still 10/10 |
| **C3** | `learn2` catalog-aware CLI + per-DB batch + **`database.md` + `catalog.md` authoring** | 2-3 | `dirac learn --catalog spider2_local --database chinook` produces per-DB fabric AND `database.md`; `--catalog spider2_local` (no db) learns all 30 + rolls up `catalog.md` at the end |
| **C4** | `data_analyst` catalog-aware end-to-end: prompt + framing + all tools + verify + finish + memory + audit | 3 | pinned mode = identical to today; catalog-scope uses `catalog.md` → `database.md` → per-DB semantic model traversal; every tool call + audit entry tagged (catalog, db) |
| **C5** | `dirac-catalog-mcp` (new MCP server) | 1 | Cursor connects, `list_databases()` shows 30 spider DBs, `use_database chinook` → tools scope correctly |
| **C6** | Cross-DB primitive: `run_sql_multi` + `find_cross_db_joins` (stub for now; real impl later) | 1 | fully-qualified `catalog.database.schema.table` references execute; stub for cross-DB join discovery |
| **C7** | Regression: retail_complex + fintech_complex UAT via new catalog layer | 1 | 10/10 sanity from earlier still passes; MCP end-to-end still passes on both schemas |
| **C8** | Spider 2.0-Lite as first catalog-native user | 3-4 | full flow: catalog+all 30 DBs learned → 135 questions answered → grader run → number |

**Total: ~2.5 weeks** to catalog-native platform + first Spider 2.0-Lite score.
(Bumped from 2 weeks for the `catalog.md` / `database.md` index authoring
in C3 and the deeper query-agent surgery in C4.)

Cost during learning is the wildcard:
- If we use Fireworks (`learn2`): ~$8-15 total for 30 DBs
- If we route learning through Cursor MCP (per your earlier idea): **$0 on our side**

## 9a · Index authoring — who writes `catalog.md` and `database.md`

The learning agent (`learn2`) is the authoring source. Two-pass flow:

**Pass 1 — per-DB learning** (existing shape, extended):
```
for each database in catalog:
    run existing learn2 loop (schema profiling, join facts, metadata_descriptions)
    → in addition, LLM synthesises database.md:
       - one-line DB description (what domain, what data)
       - per-table: grain + one-line description + row_count + key columns
       - common query patterns (from gold seeds + observed experiences)
    → write fabric/catalogs/<catalog>/databases/<db>/database.md
```

**Pass 2 — catalog rollup**:
```
after all DBs learned:
    LLM reads each database.md + catalog.yaml
    → synthesises catalog.md:
       - one-para catalog description (engine, purpose, domain coverage)
       - per-DB one-liner + table list (name + row_count only, no columns)
    → write fabric/catalogs/<catalog>/catalog.md
```

Both files are **regenerated** whenever any per-DB fabric changes — cheap,
keeps them in sync. Catalog-scope query agents always load these first;
they're small enough to always be in prompt context.

## 10 · Open questions before I start C1

1. **Terminology**: OK with `Catalog → Database → Schema → Table → Column`, or
   would you prefer a flatter `Catalog → Namespace → Table → Column` (dropping
   Schema entirely since we rarely use it)? I lean toward keeping Schema —
   Postgres/Databricks/Snowflake all differentiate database from schema,
   and eliding it now forces a retrofit later.
2. **Cross-DB joins**: build the API (fields on Catalog + tool stubs) in C1,
   or defer to C6? I lean **build the API now** (empty by default) — cheap
   to accommodate later without a migration.
3. **Backward-compat window**: how long do we keep the old
   `fabric/<schema>/*` shim? I lean **6 months / until the next major
   version** — after that we deprecate.
4. **Cursor-driven learning** (from prior conversation): still on for the
   `dirac-learn-mcp` server, or is that separate track from this catalog
   work? I'd fold it into **C5** — the new catalog MCP includes the
   learning-authoring tools alongside the observation tools.
5. **`catalog.md` / `database.md` size caps**: what's the ceiling before we
   need summarisation? I'd propose **soft caps** — 30 KB catalog / 10 KB
   per DB — with agent-authored compression if we exceed. Alternative:
   hard-truncate + let framing pull more via a `describe_database(db)` tool.
   I lean soft-cap-with-agentic-compression.

Sign off on any of §1-8 you want reshaped, and answer any of 10.1-10.4, and
I'll start C1 next turn. Design-doc-only for now — no agent code
touched, so branch stays reversible.
