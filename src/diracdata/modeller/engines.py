"""Engine + dialect + optimisation knowledge — curated JSON facts.

This is a STATIC knowledge base. The modeller reads these to decide
"what engine am I proposing for and what primitives can I use?" — the
knowledge itself doesn't judge anything; the agent picks.

Extend the tables here when we learn something new about an engine's
capabilities. Never bake in cost or "always prefer X" judgements.
"""
from __future__ import annotations
from typing import Any, Dict, List


# ---------- capability + primitive facts ----------

_ENGINES: Dict[str, Dict[str, Any]] = {
    "duckdb": {
        "display_name": "DuckDB",
        "write_model": "single-writer",
        "capabilities": {
            "acid": True,               # single-writer only
            "schema_evolution": "partial",  # add col ok; drop/rename limited
            "time_travel": False,
            "merge_into": True,
            "incremental_refresh": "manual",  # no built-in refresh scheduler
            "materialized_views": False,
        },
        "optimisation_primitives": [
            {"name": "row_group_stats",   "kind": "layout",      "note": "min/max/nulls per column per row group; enables predicate pushdown"},
            {"name": "dictionary_encoding","kind": "encoding",   "note": "automatic for categorical columns"},
            {"name": "zstd_compression",  "kind": "encoding",    "note": "default; good ratio for text/JSON"},
            {"name": "hive_partitioning", "kind": "layout",      "note": "date=/hour= folder layout, DuckDB prunes at scan planning"},
            {"name": "sort_within_files", "kind": "layout",      "note": "ORDER BY at COPY-time enables range skip within row groups"},
            {"name": "bloom_filters",     "kind": "index",       "note": "opt-in at write time for high-cardinality equality"},
        ],
        "layout_options": {
            "file_size_mb":    {"min": 16,  "default": 128, "max": 1024},
            "row_group_rows":  {"min": 10_000, "default": 100_000, "max": 1_000_000},
            "compressions":    ["zstd", "snappy", "gzip", "lz4", "brotli", "uncompressed"],
        },
        "dialect_notes": [
            "date arithmetic: use `date_diff('day', a, b)` — not `b - a` (returns INTERVAL)",
            "list operations: `length(list)` works; `cardinality(map)` for maps",
            "UNNEST WITH ORDINALITY supported via LATERAL",
            "arg_max/arg_min are native",
        ],
    },
    "iceberg": {
        "display_name": "Apache Iceberg",
        "write_model": "multi-writer (via catalog)",
        "capabilities": {
            "acid": True,
            "schema_evolution": True,
            "time_travel": True,
            "merge_into": True,
            "incremental_refresh": True,
            "materialized_views": False,
        },
        "optimisation_primitives": [
            {"name": "sort_order",         "kind": "layout",     "note": "declared per table; used at compact/write time"},
            {"name": "partition_transform","kind": "layout",     "note": "identity, bucket(N), truncate(N), day, month, year, hour"},
            {"name": "z_order",            "kind": "layout",     "note": "via Spark/Trino writer — not native Iceberg spec"},
            {"name": "manifest_pruning",   "kind": "metadata",   "note": "per-file column stats indexed; skip files before opening"},
            {"name": "snapshot_isolation", "kind": "correctness","note": "readers see consistent snapshot; MERGE creates new snapshot"},
            {"name": "row_group_stats",    "kind": "layout",     "note": "inherited from Parquet"},
            {"name": "bloom_filters",      "kind": "index",      "note": "opt-in per column"},
        ],
        "layout_options": {
            "file_size_mb":    {"min": 64,  "default": 512, "max": 2048},
            "row_group_rows":  {"min": 100_000, "default": 1_000_000, "max": 10_000_000},
            "compressions":    ["zstd", "snappy", "gzip"],
        },
        "dialect_notes": [
            "MERGE INTO … USING … ON … WHEN MATCHED / WHEN NOT MATCHED — standard",
            "Time travel: `FOR TIMESTAMP AS OF` or `FOR VERSION AS OF`",
            "Partition evolution: can add/remove transforms without rewriting old data",
        ],
    },
    "delta": {
        "display_name": "Delta Lake",
        "write_model": "multi-writer (via transaction log)",
        "capabilities": {
            "acid": True,
            "schema_evolution": True,
            "time_travel": True,
            "merge_into": True,
            "incremental_refresh": True,
            "materialized_views": True,  # via Databricks
        },
        "optimisation_primitives": [
            {"name": "z_order",             "kind": "layout",     "note": "OPTIMIZE table ZORDER BY (col1, col2) — multi-col data skipping"},
            {"name": "liquid_clustering",   "kind": "layout",     "note": "newer; adaptive clustering, replaces z_order"},
            {"name": "optimize_compaction", "kind": "maintenance","note": "OPTIMIZE compacts small files"},
            {"name": "vacuum",              "kind": "maintenance","note": "removes old snapshots"},
            {"name": "deletion_vectors",    "kind": "correctness","note": "efficient row-level deletes without rewrite (v3+)"},
            {"name": "change_data_feed",    "kind": "streaming",  "note": "CDF for incremental readers"},
            {"name": "partition_pruning",   "kind": "layout",     "note": "standard Hive-style + manifest pruning"},
        ],
        "layout_options": {
            "file_size_mb":    {"min": 128, "default": 1024, "max": 2048},
            "row_group_rows":  {"min": 100_000, "default": 1_000_000, "max": 10_000_000},
            "compressions":    ["zstd", "snappy", "gzip"],
        },
        "dialect_notes": [
            "MERGE INTO — full SQL support",
            "OPTIMIZE … ZORDER BY — Databricks + Delta OSS",
            "`SELECT … VERSION AS OF n` or `TIMESTAMP AS OF ts`",
        ],
    },
    "snowflake": {
        "display_name": "Snowflake",
        "write_model": "managed multi-writer",
        "capabilities": {
            "acid": True,
            "schema_evolution": True,
            "time_travel": True,
            "merge_into": True,
            "incremental_refresh": True,
            "materialized_views": True,
        },
        "optimisation_primitives": [
            {"name": "clustering_keys",           "kind": "layout",   "note": "declared per table; automatic reclustering"},
            {"name": "materialized_views",        "kind": "prebuild", "note": "auto-maintained; billed per compute time"},
            {"name": "search_optimization_service","kind": "index",   "note": "point-lookup speed on high-cardinality equality"},
            {"name": "result_cache",              "kind": "cache",    "note": "24h automatic query-result caching"},
            {"name": "micro_partitions",          "kind": "layout",   "note": "proprietary; automatic per-partition min/max"},
        ],
        "layout_options": {
            "file_size_mb":    "managed",
            "row_group_rows":  "managed",
            "compressions":    "managed",
        },
        "dialect_notes": [
            "Semi-structured: VARIANT / OBJECT / ARRAY native, FLATTEN for unnesting",
            "Warehouse sizing is separate from storage — proposal should note expected size class",
            "Time travel default 1 day; extendable up to 90 days (enterprise)",
        ],
    },
    "databricks": {
        "display_name": "Databricks (Unity Catalog + Delta/Photon)",
        "write_model": "multi-writer via Delta",
        "capabilities": {
            "acid": True,
            "schema_evolution": True,
            "time_travel": True,
            "merge_into": True,
            "incremental_refresh": True,
            "materialized_views": True,
        },
        "optimisation_primitives": [
            {"name": "liquid_clustering",      "kind": "layout",     "note": "recommended over z_order; adaptive; single ALTER TABLE CLUSTER BY (…)"},
            {"name": "z_order",                "kind": "layout",     "note": "legacy; still supported via OPTIMIZE ZORDER BY"},
            {"name": "predictive_optimization","kind": "maintenance","note": "autoruns OPTIMIZE/VACUUM based on read patterns"},
            {"name": "deletion_vectors",       "kind": "correctness","note": "efficient DELETE/UPDATE without file rewrite"},
            {"name": "photon_engine",          "kind": "runtime",    "note": "vectorised query engine; automatic for supported ops"},
            {"name": "materialized_views",     "kind": "prebuild",   "note": "incrementally refreshed via CDF"},
        ],
        "layout_options": {
            "file_size_mb":   {"min": 128, "default": 1024, "max": 2048},
            "row_group_rows": {"min": 100_000, "default": 1_000_000, "max": 10_000_000},
            "compressions":   ["zstd", "snappy"],
        },
        "dialect_notes": [
            "MERGE INTO — extended (WHEN NOT MATCHED BY SOURCE etc.)",
            "COPY INTO for streaming ingestion",
            "OPTIMIZE … clusters by liquid_clustering columns if declared",
        ],
    },
    "trino": {
        "display_name": "Trino",
        "write_model": "reads Iceberg/Delta/Hive; writes via connector",
        "capabilities": {
            "acid": "via underlying table format",
            "schema_evolution": "via underlying table format",
            "time_travel": "via underlying table format",
            "merge_into": True,
            "incremental_refresh": "via job scheduler",
            "materialized_views": True,
        },
        "optimisation_primitives": [
            {"name": "cost_based_optimizer",  "kind": "runtime",  "note": "uses ANALYZE stats"},
            {"name": "dynamic_filtering",     "kind": "runtime",  "note": "runtime bloom filter push-down through joins"},
            {"name": "materialized_views",    "kind": "prebuild", "note": "Iceberg/Hive backed"},
        ],
        "layout_options": {
            "file_size_mb":   "via connector",
            "row_group_rows": "via connector",
            "compressions":   "via connector",
        },
        "dialect_notes": [
            "MERGE INTO supported for Iceberg/Delta/Hive connectors",
            "UNNEST WITH ORDINALITY supported",
            "Cost-based optimizer needs ANALYZE for good plans",
        ],
    },
    "spark": {
        "display_name": "Apache Spark (with Iceberg / Delta)",
        "write_model": "multi-writer via table format",
        "capabilities": {
            "acid": "via Iceberg/Delta",
            "schema_evolution": "via Iceberg/Delta",
            "time_travel": "via Iceberg/Delta",
            "merge_into": True,
            "incremental_refresh": "streaming (Structured Streaming)",
            "materialized_views": False,  # not native
        },
        "optimisation_primitives": [
            {"name": "adaptive_query_execution",   "kind": "runtime", "note": "AQE reoptimizes plans mid-query based on runtime stats"},
            {"name": "dynamic_partition_pruning",  "kind": "runtime", "note": "prunes partitions using join keys at runtime"},
            {"name": "broadcast_hash_join",        "kind": "runtime", "note": "auto for small side (spark.sql.autoBroadcastJoinThreshold)"},
            {"name": "columnar_shuffle",           "kind": "runtime", "note": "reduces shuffle bytes for wide rows"},
            {"name": "z_order",                    "kind": "layout",  "note": "via Delta OPTIMIZE ZORDER BY"},
        ],
        "layout_options": {
            "file_size_mb":   {"min": 64, "default": 512, "max": 2048},
            "row_group_rows": {"min": 100_000, "default": 1_000_000, "max": 10_000_000},
            "compressions":   ["zstd", "snappy", "gzip"],
        },
        "dialect_notes": [
            "MERGE INTO — Iceberg + Delta both support",
            "explode() / posexplode() for unnesting arrays",
            "CACHE TABLE for iterative workloads",
        ],
    },
}


# ---------- tool bodies (called by agent) ----------

def list_supported_engines() -> List[str]:
    """Return the list of engines the modeller has facts about."""
    return sorted(_ENGINES.keys())


def describe_engine_capabilities(engine: str) -> Dict[str, Any]:
    """Return {display_name, write_model, capabilities} for one engine.

    Capabilities keys: acid, schema_evolution, time_travel, merge_into,
    incremental_refresh, materialized_views. Values are bool or explanatory
    string (e.g. 'via underlying table format').
    """
    e = _ENGINES.get(engine.lower())
    if not e:
        return {"error": f"unknown engine: {engine}", "supported": list_supported_engines()}
    return {
        "engine":         engine.lower(),
        "display_name":   e["display_name"],
        "write_model":    e["write_model"],
        "capabilities":   e["capabilities"],
    }


def list_optimisation_primitives(engine: str, kind: str = None) -> List[Dict[str, str]]:
    """List optimisation primitives available on this engine.

    kind (optional) filters to one category: layout | encoding | index | cache |
    maintenance | runtime | correctness | streaming | prebuild.
    """
    e = _ENGINES.get(engine.lower())
    if not e:
        return [{"error": f"unknown engine: {engine}"}]
    prims = e["optimisation_primitives"]
    if kind:
        prims = [p for p in prims if p.get("kind") == kind]
    return prims


def list_layout_options(engine: str) -> Dict[str, Any]:
    """Return file-layout options (file size, row group rows, compressions) for the engine."""
    e = _ENGINES.get(engine.lower())
    if not e:
        return {"error": f"unknown engine: {engine}"}
    return e["layout_options"]


def describe_sql_dialect(engine: str) -> Dict[str, Any]:
    """Return dialect notes for the engine — syntax quirks, function name diffs, MERGE support."""
    e = _ENGINES.get(engine.lower())
    if not e:
        return {"error": f"unknown engine: {engine}"}
    return {
        "engine":           engine.lower(),
        "display_name":     e["display_name"],
        "dialect_notes":    e["dialect_notes"],
        "merge_into":       e["capabilities"].get("merge_into"),
        "time_travel":      e["capabilities"].get("time_travel"),
    }
