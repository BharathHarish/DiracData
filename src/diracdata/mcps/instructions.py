"""QUERY vs LEARN instruction packs for DiracData MCP servers.

Two host-facing packs (Cursor/Claude). Internal learning prompts
(learn_core.md etc.) are a separate lever and are not replaced here.
"""
from __future__ import annotations

QUERY_PACK = """\
DiracData MCP — QUERY mode. YOU are the analyst; these tools/resources ground you.

DIALECT: Call get_dialect (or read dialect://) BEFORE the first run_sql. Write that engine's SQL exactly \
(DuckDB list indexing is 1-based; date_trunc/extract idioms differ across engines).

Recommended flow:
(1) find_examples FIRST — reuse a proven pattern;
(2) Prefer resources for known nouns (@metric://, @table://, @index://, @ontology://) over re-fetching via tools;
(3) search_context / search_fabric to locate concepts; describe_column for nested ACCESS RECIPE; \
get_metric + metric_bind_check before trusting blessed SQL;
(4) join_path BEFORE any join (cardinality; aggregate-then-join);
(5) run_sql (read-only); (6) data_check on multi-table drafts;
(7) temporal_coverage before joining two time-bearing tables for an implied period;
(8) attribute for governed metric RCA between periods.

GO BEYOND THE ASK (when answering a KPI): bind the definition, name the top driver dimension if known, \
surface companions from get_metric/metric://, and call out fabric gotchas (NULL meaning, SCD, taxonomy maps). \
If metric/period/database is ambiguous, call clarify — do NOT silently guess.

If schema/catalog fabric is incomplete, call completeness_check / fabric_health and learn or refuse confidently.
"""

LEARN_PACK = """\
DiracData MCP — LEARN mode (fabric authoring). Do NOT claim done until completeness_check(db).ok is true.

Workflow:
(1) list_databases / use_database; (2) list_tables → describe_table / profile;
(3) propose_table_description / propose_column_description (complex cols MUST include access_recipe) / \
propose_join (with cardinality) / propose_metric;
(4) metric_bind_check every proposed metric before save;
(5) save_semantic_model; (6) refresh_database_md (first paragraph = business-model narrative); \
(7) refresh_catalog_md; (8) completeness_check + fabric_health must be green.

COMPLETION CHECKLIST (all required):
- No empty/unreachable database; catalog.md and database.md exist and are not stubs
- Every table has verified grain + kind; every column has a description
- Complex columns have verified access recipes
- Joins have cardinality (+ facts when measured)
- Metrics bind-check clean against the live engine
- ontology/channel maps authored when cross-taxonomy labels exist (e.g. campaign channel vs UTM)

Prefer resources after save to verify published nouns. Use save_experience for durable gotchas.
"""

# Combined default for catalog MCP (supports both modes in one process).
CATALOG_INSTRUCTIONS = f"""\
DiracData CATALOG MCP: observation AND learning over a whole catalog.

=== QUERY ===
{QUERY_PACK}

Harness-first query path: search_fabric → use_database → search_schema/profile → sql_diff if ambiguous → run_sql.
Fallback browse: get_catalog_index / get_database_index / describe_table (soft-fail), or @index:// resources.

=== LEARN ===
{LEARN_PACK}
"""

SCHEMA_INSTRUCTIONS = f"""\
DiracData CONTEXT MCP: governed context over one schema so you write correct SQL.

=== QUERY ===
{QUERY_PACK}

If a schema is not learned yet, call learn_schema and poll learn_status; then completeness_check / fabric_health.
"""


def query_pack() -> str:
    return QUERY_PACK


def learn_pack() -> str:
    return LEARN_PACK


def catalog_instructions() -> str:
    return CATALOG_INSTRUCTIONS


def schema_instructions() -> str:
    return SCHEMA_INSTRUCTIONS
