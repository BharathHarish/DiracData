"""catalog_index — LLM-authored catalog.md + database.md summaries.

Two small LLM calls that turn the machine-readable fabric (semantic_model.yaml +
metadata_descriptions.json) into human/agent-readable hierarchical indexes:

  database.md  — one file per database. Table list with grain + 1-line description +
                 key columns. Common query patterns from gold seeds/experiences.
                 Loaded by the query agent once it has picked a DB.

  catalog.md   — one file per catalog. Catalog description + per-DB one-liner +
                 table names + row counts. Loaded by the query agent FIRST for
                 catalog-scope questions (agent picks the DB).

The LLM is passed in as a callable `LlmCallable = Callable[[str], str]` — no
framework lock-in. Real production uses `learn_catalog.py` which wires a
Fireworks / Anthropic backed LLM; tests pass a fake.

Nothing here decides content — the LLM authors the markdown. This module is
just the prompts + the read/write plumbing. Judgement is agentic (per §0 of
CATALOG_DESIGN.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from diracdata.context.catalog_store import CatalogStore


LlmCallable = Callable[[str], str]   # prompt → completion


# --------------------------------------------------------------------------- #
# Prompts — pure functions returning the prompt text. Testable in isolation.
# --------------------------------------------------------------------------- #

def prompt_database_md(
    *,
    catalog: str,
    database: str,
    semantic_model_yaml: str,
    metadata_descriptions_json: str,
    join_facts_json: str = "",
    semantic_layer_yaml: str = "",
    gold_pairs_json: str = "",
) -> str:
    """The prompt that asks an LLM to author a database.md file.

    The LLM should return ONLY the markdown body (no code fences, no preamble).
    """
    hints = []
    if join_facts_json:      hints.append(f"\n## Join facts (behavioural)\n\n{join_facts_json[:4000]}")
    if semantic_layer_yaml:  hints.append(f"\n## Blessed metrics + dims (semantic_layer.yaml)\n\n{semantic_layer_yaml[:2500]}")
    if gold_pairs_json:      hints.append(f"\n## Gold NL-SQL seeds\n\n{gold_pairs_json[:3000]}")

    return f"""You are authoring a compact, agent-readable index for one database.

Target file: `fabric/catalogs/{catalog}/databases/{database}/database.md`

The **query agent** will load this file (~2-10 KB) after it has picked this
database, to orient itself before diving into per-table details. Write the
markdown so the agent gets, in one read:

- What domain this database covers, in one line
- Every table with: row count (if known), one-line grain/description, key columns
- Common query patterns extracted from gold seeds or observed workload
- Any surprising joins, denormalisations, or nested-type quirks worth flagging

Structure (adjust as needed — this is guidance, not a template):

```
# Database: {database}   (catalog: {catalog})

<one-paragraph description of what this DB is + what analysts use it for>

## Tables

### <table_name> (<n> rows) — <one-line description>
Grain: <primary key or unique combo>. Joins: <table_a> (key), <table_b> (key).
Cols: <name (type), name (type), … key columns only, elide the rest>.

… (repeat for every table)

## Common query patterns

- <pattern from gold seeds, e.g. "revenue by country: sum(invoice.total) group by billing_country">
- <pattern>

## Notes
<any nested-type recipes, unusual conventions, gotchas worth flagging>
```

Rules:
- Return ONLY the markdown body — no ``` fences, no "Here's the file" preamble.
- Keep it under ~10 KB. If the DB is huge (>50 tables), group by domain rather than listing all.
- Every table listed must exist in the semantic_model.
- No hallucinated columns or joins. Cite from the sources below only.
- If a section (e.g. Common patterns) has no data available, omit it.

## Semantic model

{semantic_model_yaml[:6000]}

## Column metadata (with runnable recipes for nested types)

{metadata_descriptions_json[:4000]}
{"".join(hints)}
"""


def prompt_catalog_md(
    *,
    catalog: str,
    engine: str,
    catalog_description: str,
    databases: List[Dict[str, str]],
) -> str:
    """The prompt that asks an LLM to author a catalog.md file.

    `databases` is a list of dicts like:
        {"name": "chinook", "table_count": 13, "size_mb": 0.8, "database_md": "<the .md>"}

    LLM returns ONLY the markdown body.
    """
    per_db_block = "\n\n".join(
        f"### {d.get('name')} — engine hints: {d.get('table_count','?')} tables, "
        f"{d.get('size_mb','?')} MB\n\n{(d.get('database_md') or '')[:2000]}"
        for d in databases
    )
    return f"""You are authoring the TOP-LEVEL index for a data catalog.

Target file: `fabric/catalogs/{catalog}/catalog.md`

The **query agent** loads this file (~5-30 KB) as the FIRST thing when a
question arrives without a pinned database. It uses this to pick the right
database(s) to dive into. Optimise for that routing decision.

Structure (guidance, not template):

```
# Catalog: {catalog}
<one-paragraph: engine, total databases, domain coverage, purpose>

## Databases (<N>)

### <db_name> (<size_mb> MB, <table_count> tables) — <one-line domain description>
<2-4 sentences: what this DB is, what analysts use it for, what CAN'T be
answered from it>.
Tables: <table_1> (<rows>), <table_2> (<rows>), … <table_N> (<rows>).

… (repeat for every database)

## Cross-database relationships
<summarise cross_db_joins.yaml if any — otherwise omit>
```

Rules:
- Return ONLY the markdown body — no fences, no preamble.
- One-line domain descriptions are the highest-value signal for routing —
  make them specific ("digital music store: invoices, tracks, playlists" not
  "music data").
- Under ~30 KB. If catalog has 100+ DBs, group by domain and elide table
  lists past the first 5 per DB.
- No hallucinated databases or tables — use only what's in the per-DB blocks below.

## Catalog

- name: {catalog}
- engine: {engine}
- description: {catalog_description or '(none provided — infer from databases)'}
- database_count: {len(databases)}

## Per-database summaries (from each database.md)

{per_db_block}
"""


# --------------------------------------------------------------------------- #
# Build helpers — read stored artifacts, prompt LLM, write result.
# --------------------------------------------------------------------------- #

def build_database_md(
    store: CatalogStore, *, catalog: str, database: str, llm: LlmCallable,
) -> str:
    """Author (or refresh) database.md for one DB. Returns the written markdown."""
    sm  = store.get_text(catalog, database, "semantic_model.yaml", default="") or ""
    md  = store.get(catalog, database, "metadata_descriptions.json", default={}) or {}
    jf  = store.get(catalog, database, "join_facts.json",           default=[]) or []
    sl  = store.get_text(catalog, database, "semantic_layer.yaml",   default="") or ""
    gp  = store.get(catalog, database, "gold_pairs.json",            default=[]) or []

    import json
    prompt = prompt_database_md(
        catalog=catalog, database=database,
        semantic_model_yaml=sm,
        metadata_descriptions_json=json.dumps(md, indent=2, default=str),
        join_facts_json=json.dumps(jf, indent=2, default=str) if jf else "",
        semantic_layer_yaml=sl,
        gold_pairs_json=json.dumps(gp, indent=2, default=str) if gp else "",
    )
    md_text = llm(prompt).strip()
    # Strip accidental code fence if the LLM wrapped anyway
    if md_text.startswith("```"):
        lines = md_text.splitlines()
        if lines and lines[0].startswith("```"): lines = lines[1:]
        if lines and lines[-1].startswith("```"): lines = lines[:-1]
        md_text = "\n".join(lines)

    store.put_text(catalog, database, "database.md", md_text, content_type="text/markdown")
    return md_text


def build_catalog_md(
    store: CatalogStore, *, catalog: str, llm: LlmCallable,
) -> str:
    """Author (or refresh) catalog.md by rolling up all databases' database.md files.

    Reads catalog.yaml for top-level metadata + each database.md for the per-DB
    summary block. Returns the written markdown.
    """
    cat_meta = store.get_catalog(catalog, "catalog.yaml", default={}) or {}
    engine = cat_meta.get("engine", "duckdb")
    description = cat_meta.get("description", "")
    db_names = store.list_databases(catalog)

    databases = []
    for db in db_names:
        db_meta = store.get(catalog, db, "database.yaml", default={}) or {}
        databases.append({
            "name":         db,
            "table_count":  db_meta.get("table_count", "?"),
            "size_mb":      db_meta.get("size_mb", "?"),
            "database_md":  store.get_text(catalog, db, "database.md", default="") or "",
        })

    prompt = prompt_catalog_md(
        catalog=catalog, engine=engine, catalog_description=description, databases=databases,
    )
    md_text = llm(prompt).strip()
    if md_text.startswith("```"):
        lines = md_text.splitlines()
        if lines and lines[0].startswith("```"): lines = lines[1:]
        if lines and lines[-1].startswith("```"): lines = lines[:-1]
        md_text = "\n".join(lines)

    store.put_catalog_text(catalog, "catalog.md", md_text, content_type="text/markdown")
    return md_text


def build_all_databases_md(
    store: CatalogStore, *, catalog: str, llm: LlmCallable,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, str]:
    """Refresh database.md for every database in the catalog. Returns {db_name: md_text}."""
    out = {}
    for db in store.list_databases(catalog):
        if on_progress: on_progress(f"authoring database.md for {catalog}/{db}...")
        out[db] = build_database_md(store, catalog=catalog, database=db, llm=llm)
    return out


__all__ = [
    "LlmCallable",
    "prompt_database_md", "prompt_catalog_md",
    "build_database_md", "build_catalog_md", "build_all_databases_md",
]
