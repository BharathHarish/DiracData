"""Catalog / Database / Schema abstractions — the multi-DB backbone.

Hierarchy (locked in CATALOG_DESIGN.md §1):

    Catalog                        # the connection / account / instance
      ├── Database                 # a group of schemas (or top-level namespace)
      │     ├── Schema             # "main" by default; "public" for Postgres; etc.
      │     │     └── Table        # existing Table shape lives elsewhere; we
      │     │           └── Column # reference it here by name
      │     ├── joins              # within-database joins (behavioural, from V3-S1)
      │     ├── metrics            # blessed metrics (semantic_layer.yaml)
      │     └── examples           # gold NL-SQL seeds
      ├── cross_db_joins           # discovered by learning agent (§9b)
      ├── catalog_metrics          # rare — unified metrics across DBs
      └── catalog_examples         # rare — cross-DB gold seeds

Design constraints (locked):
  1. Zero regressions on existing single-schema flows. Legacy fabric/<schema>/*
     layout continues to work through Catalog.load_legacy(schema_name).
  2. Additive only — this module does NOT rewrite Workspace. Workspace can be
     obtained from a Database via .workspace() when ready (built in C4).
  3. Fully-qualified names: every Table exposes fqn = "catalog.database.schema.table"
     so the query agent can never confuse two DBs.
  4. Cross-DB joins are first-class from day 1 (per Decision #2). Empty by
     default; populated by the learning agent's third pass (§9b).

Nothing in this module reads/writes MinIO. Storage IO lives in ContextStore
(C2). This is the pure data model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Terminology
#
# Different engines call these differently — we normalise:
#   Catalog  = Snowflake account | Databricks catalog | BigQuery project | DuckDB instance
#   Database = Snowflake database | Databricks schema | BigQuery dataset  | DuckDB attached db
#   Schema   = Postgres/SF schema | (elided as "main" for Databricks/BigQuery/DuckDB flat)
#   Table + Column = universal
#
# For our current use cases:
#   "local" catalog → databases: retail_complex, fintech_complex — each schema="main"
#   "spider2_local" catalog → 30 SQLite databases — each schema="main"
# --------------------------------------------------------------------------- #


DEFAULT_SCHEMA = "main"


@dataclass
class TableRef:
    """Lightweight table reference — used by joins, examples, cross-DB joins.

    We keep this decoupled from the full Table dataclass in workspace.py so
    Catalog can be reasoned about without pulling in the whole workspace
    machinery. Full Table lives on a Schema (loaded lazily by C2 store code).
    """
    catalog:  str
    database: str
    schema:   str
    table:    str

    @property
    def fqn(self) -> str:
        """Fully-qualified name: catalog.database.schema.table."""
        return f"{self.catalog}.{self.database}.{self.schema}.{self.table}"

    def qualify(self, sep: str = ".") -> str:
        """SQL-friendly qualified reference (may drop 'main' schema)."""
        parts = [self.catalog, self.database]
        if self.schema and self.schema != DEFAULT_SCHEMA:
            parts.append(self.schema)
        parts.append(self.table)
        return sep.join(parts)


@dataclass
class Join:
    """A join edge — within-DB or cross-DB.

    Cross-DB joins have `left.database != right.database`; within-DB otherwise.
    """
    left:        TableRef
    right:       TableRef
    left_keys:   List[str]  = field(default_factory=list)
    right_keys:  List[str]  = field(default_factory=list)
    cardinality: str        = ""      # "1-1" | "1-N" | "N-M" | ""
    disposition: str        = ""      # "INNER" | "LEFT" | "" (from V3-S1 join cards)
    match_rate:  Optional[float] = None
    notes:       str        = ""

    @property
    def is_cross_db(self) -> bool:
        return self.left.database != self.right.database or self.left.catalog != self.right.catalog


@dataclass
class Metric:
    """A blessed metric (from semantic_layer.yaml or catalog-level metrics)."""
    name:        str
    sql:         str
    description: str = ""
    grain:       str = ""


@dataclass
class Example:
    """A gold NL→SQL pair (from gold_pairs.json or cross_db_examples.jsonl)."""
    nl_query:  str
    sql:       str
    tables:    List[TableRef] = field(default_factory=list)
    notes:     str = ""


@dataclass
class Schema:
    """A schema within a database. Contains table names (full Table shape loaded lazily).

    Note: we store table names here rather than full Table objects to keep Catalog
    lightweight and let ContextStore lazy-load full details on demand.
    """
    name:         str
    database:     str
    catalog:      str
    table_names:  List[str] = field(default_factory=list)

    def table_ref(self, table_name: str) -> TableRef:
        return TableRef(catalog=self.catalog, database=self.database,
                        schema=self.name, table=table_name)


@dataclass
class Database:
    """A database (schema in Databricks terminology, dataset in BigQuery).

    In our first cut every Database has one Schema ("main"), which is what
    DuckDB and SQLite expose by default. Multi-schema engines (Postgres,
    Databricks) will populate multiple Schemas without any Database-shape change.
    """
    name:          str
    catalog:       str
    schemas:       Dict[str, Schema]   = field(default_factory=dict)
    joins:         List[Join]          = field(default_factory=list)
    metrics:       List[Metric]        = field(default_factory=list)
    examples:      List[Example]       = field(default_factory=list)
    description:   str                 = ""
    # Optional summary index authored by the learning agent (§3a).
    # Loaded from databases/<db>/database.md by ContextStore (C2).
    database_md:   Optional[str]       = None

    def __post_init__(self):
        if not self.schemas:
            self.schemas[DEFAULT_SCHEMA] = Schema(
                name=DEFAULT_SCHEMA, database=self.name, catalog=self.catalog,
            )

    def default_schema(self) -> Schema:
        return self.schemas.get(DEFAULT_SCHEMA) or next(iter(self.schemas.values()))

    def table_names(self) -> List[str]:
        """All table names across all schemas."""
        out: List[str] = []
        for s in self.schemas.values():
            out.extend(s.table_names)
        return out


@dataclass
class Catalog:
    """A catalog — the top-level container. Holds many databases.

    Represents "the connection" — one Snowflake account, one Databricks catalog,
    one BigQuery project, one DuckDB instance with N ATTACHed files, etc.

    Cross-database concerns (joins, metrics, examples) live here, not on any one
    Database. Catalog is authoritative for `catalog.md` (the top-level index).
    """
    name:          str
    engine:        str                    = "duckdb"    # duckdb | snowflake | bigquery | databricks | trino | postgres
    connection:    Dict[str, Any]         = field(default_factory=dict)  # engine-specific config
    databases:     Dict[str, Database]    = field(default_factory=dict)
    cross_db_joins:   List[Join]          = field(default_factory=list)
    catalog_metrics:  List[Metric]        = field(default_factory=list)
    catalog_examples: List[Example]       = field(default_factory=list)
    description:   str                    = ""
    # Optional rollup index authored by the learning agent (§3a).
    # Loaded from catalog.md by ContextStore (C2).
    catalog_md:    Optional[str]          = None

    def db(self, name: str) -> Database:
        """Convenience accessor with a clear error when missing."""
        if name not in self.databases:
            raise KeyError(
                f"database {name!r} not in catalog {self.name!r}; "
                f"available: {sorted(self.databases.keys())}"
            )
        return self.databases[name]

    def database_names(self) -> List[str]:
        return sorted(self.databases.keys())

    def all_within_db_joins(self) -> List[Join]:
        """Every within-DB join across every database in this catalog."""
        out: List[Join] = []
        for d in self.databases.values():
            out.extend(d.joins)
        return out


# --------------------------------------------------------------------------- #
# Legacy shim — Decision #3: keep indefinite backward-compat
#
# Existing single-schema fabrics (fabric/retail_complex/*, fabric/fintech_complex/*)
# get wrapped as: Catalog(name="local", databases={<schema>: Database(...)})
# on read. Nothing writes back to the legacy layout — writes always go to the
# new fabric/catalogs/<catalog>/databases/<db>/ shape. C2 owns storage.
# --------------------------------------------------------------------------- #

LEGACY_CATALOG = "local"


def wrap_legacy(schema_name: str, table_names: Optional[List[str]] = None) -> Catalog:
    """Wrap a legacy single-schema fabric as a Catalog with one Database.

    Doesn't touch storage — pure in-memory construction. The caller (C2 store
    code) will pass table_names discovered from the fabric artifacts. Joins,
    metrics, examples are added by the store loader.
    """
    schema = Schema(
        name=DEFAULT_SCHEMA, database=schema_name, catalog=LEGACY_CATALOG,
        table_names=list(table_names or []),
    )
    db = Database(
        name=schema_name, catalog=LEGACY_CATALOG,
        schemas={DEFAULT_SCHEMA: schema},
    )
    return Catalog(name=LEGACY_CATALOG, engine="duckdb", databases={schema_name: db})


__all__ = [
    "DEFAULT_SCHEMA", "LEGACY_CATALOG",
    "TableRef", "Join", "Metric", "Example",
    "Schema", "Database", "Catalog",
    "wrap_legacy",
]
