"""Read-side tools — introspect lineage + query_history without mutating anything.

Every tool returns plain Python (dict/list) so the agent can reason about it directly.
"""
from __future__ import annotations
import json
from typing import Any, Dict, List, Optional
import duckdb
from .config import ModellerConfig


def _base(cfg: ModellerConfig) -> str:
    return f"s3://{cfg.bucket}/{cfg.root_prefix}"


def _s3_get_json(s3, cfg: ModellerConfig, key: str, default=None):
    try:
        return json.loads(s3.get_object(Bucket=cfg.bucket, Key=key)["Body"].read())
    except Exception:
        return default


# ---------- lineage ----------

def list_lineage(cfg: ModellerConfig, s3) -> Dict:
    """Full lineage.json — the modeller's structural map."""
    return _s3_get_json(s3, cfg, cfg.lineage_key, default={}) or {}


def gold_table_names(cfg: ModellerConfig, s3) -> List[str]:
    """Just the gold tables that already exist — used for coverage checks."""
    return sorted(list((list_lineage(cfg, s3).get("gold") or {}).keys()))


# ---------- query_history ----------

def list_query_patterns(
    cfg: ModellerConfig, con: duckdb.DuckDBPyConnection,
    *, archetype: Optional[str] = None, since_days: Optional[int] = None,
    min_cost_ms: Optional[int] = None,
) -> List[Dict]:
    """Aggregate query_history by template_id → per-template stats."""
    filters = ["1=1"]
    if archetype:  filters.append(f"archetype = '{archetype}'")
    if since_days: filters.append(f"submitted_at >= now() - INTERVAL {since_days} DAY")
    where = " AND ".join(filters)

    uri = f"{_base(cfg)}/query_history/**/*.parquet"
    r = con.execute(f"""
        SELECT template_id,
               archetype,
               count(*)                                                    AS n_runs,
               avg(elapsed_ms)::INT                                        AS avg_ms,
               approx_quantile(elapsed_ms, 0.50)::INT                      AS p50_ms,
               approx_quantile(elapsed_ms, 0.95)::INT                      AS p95_ms,
               sum(elapsed_ms)                                             AS total_ms,
               count(DISTINCT sql_hash)                                    AS distinct_sql_hashes,
               count(DISTINCT user_id)                                     AS distinct_users,
               avg(layer_mix_raw)::DOUBLE                                  AS avg_raw,
               avg(layer_mix_silver)::DOUBLE                               AS avg_silver,
               avg(layer_mix_gold)::DOUBLE                                 AS avg_gold,
               count_if(status != 'success')                               AS n_errors
        FROM read_parquet('{uri}', hive_partitioning=1)
        WHERE {where}
        GROUP BY template_id, archetype
        {"HAVING avg(elapsed_ms) >= " + str(min_cost_ms) if min_cost_ms else ""}
        ORDER BY total_ms DESC
    """).fetchall()
    cols = ["template_id","archetype","n_runs","avg_ms","p50_ms","p95_ms","total_ms",
            "distinct_sql_hashes","distinct_users","avg_raw","avg_silver","avg_gold","n_errors"]
    return [dict(zip(cols, row)) for row in r]


def get_pattern_cost(
    cfg: ModellerConfig, con: duckdb.DuckDBPyConnection, template_id: str
) -> Dict:
    """Full cost profile for one template."""
    uri = f"{_base(cfg)}/query_history/**/*.parquet"
    r = con.execute(f"""
        SELECT count(*) AS n_runs,
               avg(elapsed_ms)::INT                    AS mean_ms,
               approx_quantile(elapsed_ms, 0.50)::INT  AS p50_ms,
               approx_quantile(elapsed_ms, 0.95)::INT  AS p95_ms,
               approx_quantile(elapsed_ms, 0.99)::INT  AS p99_ms,
               min(elapsed_ms)                         AS min_ms,
               max(elapsed_ms)                         AS max_ms,
               min(submitted_at)                       AS first_seen,
               max(submitted_at)                       AS last_seen,
               count(DISTINCT user_id)                 AS distinct_users
        FROM read_parquet('{uri}', hive_partitioning=1)
        WHERE template_id = '{template_id}'
    """).fetchone()
    cols = ["n_runs","mean_ms","p50_ms","p95_ms","p99_ms","min_ms","max_ms",
            "first_seen","last_seen","distinct_users"]
    profile = dict(zip(cols, r)) if r else {}

    # Tables touched (union across all runs of this template)
    tbls = con.execute(f"""
        SELECT array_agg(DISTINCT t) AS tables
        FROM (
            SELECT unnest(tables_touched) AS t
            FROM read_parquet('{uri}', hive_partitioning=1)
            WHERE template_id = '{template_id}'
        )
    """).fetchone()
    profile["tables_touched"] = sorted(list(tbls[0])) if tbls and tbls[0] else []

    # Layer mix
    mix = con.execute(f"""
        SELECT avg(layer_mix_raw)::DOUBLE     AS avg_raw,
               avg(layer_mix_silver)::DOUBLE  AS avg_silver,
               avg(layer_mix_gold)::DOUBLE    AS avg_gold
        FROM read_parquet('{uri}', hive_partitioning=1)
        WHERE template_id = '{template_id}'
    """).fetchone()
    if mix:
        profile["layer_mix"] = {"raw": mix[0], "silver": mix[1], "gold": mix[2]}

    # One representative SQL text — first non-error run
    sample = con.execute(f"""
        SELECT sql_text
        FROM read_parquet('{uri}', hive_partitioning=1)
        WHERE template_id = '{template_id}' AND status = 'success'
        LIMIT 1
    """).fetchone()
    profile["sample_sql"] = sample[0] if sample else ""

    profile["template_id"] = template_id
    return profile


def get_layer_mix_distribution(
    cfg: ModellerConfig, con: duckdb.DuckDBPyConnection
) -> Dict:
    """Overall workload shape — where are analysts spending their query cost?"""
    uri = f"{_base(cfg)}/query_history/**/*.parquet"
    r = con.execute(f"""
        SELECT
            sum(CASE WHEN layer_mix_raw    > 0 THEN 1 ELSE 0 END) AS queries_touching_raw,
            sum(CASE WHEN layer_mix_silver > 0 THEN 1 ELSE 0 END) AS queries_touching_silver,
            sum(CASE WHEN layer_mix_gold   > 0 THEN 1 ELSE 0 END) AS queries_touching_gold,
            count(*) AS total_queries,
            sum(elapsed_ms * (CASE WHEN layer_mix_raw    > 0 THEN 1 ELSE 0 END)) AS cost_ms_on_raw,
            sum(elapsed_ms * (CASE WHEN layer_mix_silver > 0 THEN 1 ELSE 0 END)) AS cost_ms_on_silver,
            sum(elapsed_ms * (CASE WHEN layer_mix_gold   > 0 THEN 1 ELSE 0 END)) AS cost_ms_on_gold,
            sum(elapsed_ms) AS total_cost_ms
        FROM read_parquet('{uri}', hive_partitioning=1)
    """).fetchone()
    cols = ["queries_touching_raw","queries_touching_silver","queries_touching_gold",
            "total_queries","cost_ms_on_raw","cost_ms_on_silver","cost_ms_on_gold",
            "total_cost_ms"]
    return dict(zip(cols, r)) if r else {}


# ---------- proposals ledger (read side; write comes in Phase 7C) ----------

def describe_table_layout(cfg: ModellerConfig, con: duckdb.DuckDBPyConnection,
                          uri: str, s3=None) -> Dict:
    """Describe on-disk layout of a parquet table (or glob).

    Returns: file_count, total_bytes, avg_file_mb, total_rows, columns (name/type list),
    partition_hints (inferred date=/hour= levels), and compression per representative file.
    The agent uses this to reason about whether a table needs compaction,
    what partitioning it currently has, and how to shape a proposed materialisation.
    """
    layout: Dict = {}
    try:
        # 1) columns via DuckDB DESCRIBE (works on globs)
        schema = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{uri}')").fetchall()
        layout["columns"] = [{"name": s[0], "type": s[1]} for s in schema]
        # 2) total rows via a single count(*) — DuckDB uses row-group stats, doesn't scan data
        row_count = con.execute(f"SELECT count(*) FROM read_parquet('{uri}')").fetchone()
        layout["total_rows"] = int(row_count[0]) if row_count else 0
    except Exception as ex:
        return {"error": str(ex)[:400], "uri": uri}

    # 3) file_count + total_bytes + partition hints via boto3 (parquet_metadata doesn't glob)
    if s3 is not None:
        # Extract prefix from s3://bucket/prefix/pattern
        try:
            no_scheme = uri.replace("s3://", "", 1)
            bucket, _, key_part = no_scheme.partition("/")
            # Strip glob tail — everything after the first * or ?
            for stopper in ("**", "*", "?"):
                if stopper in key_part:
                    key_part = key_part.split(stopper, 1)[0]
                    break
            key_part = key_part.rstrip("/")
            total_bytes = 0; file_count = 0
            partition_levels: set = set()
            partition_keys: set = set()
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key_part + "/"):
                for o in page.get("Contents", []):
                    if not o["Key"].endswith(".parquet"): continue
                    total_bytes += o["Size"]
                    file_count += 1
                    # Extract partition levels — segments with "=" between prefix and file
                    tail = o["Key"][len(key_part) + 1:] if o["Key"].startswith(key_part + "/") else o["Key"]
                    parts = tail.split("/")[:-1]  # drop the filename
                    partition_levels.add(len(parts))
                    for p in parts:
                        if "=" in p:
                            partition_keys.add(p.split("=", 1)[0])
            layout["file_count"]     = file_count
            layout["total_bytes"]    = total_bytes
            layout["avg_file_mb"]    = round(total_bytes / max(1, file_count) / 1024 / 1024, 2) if file_count else 0.0
            layout["partition_keys"] = sorted(partition_keys)
            layout["partition_levels"] = sorted(partition_levels)
        except Exception as ex:
            layout["boto_error"] = str(ex)[:200]

    # 4) Compression + row_group info via parquet_metadata on ONE representative file
    if s3 is not None and layout.get("file_count", 0) > 0:
        try:
            # Pick a single file — first one seen
            no_scheme = uri.replace("s3://", "", 1)
            bucket, _, key_part = no_scheme.partition("/")
            for stopper in ("**", "*", "?"):
                if stopper in key_part:
                    key_part = key_part.split(stopper, 1)[0]
                    break
            key_part = key_part.rstrip("/")
            rep_file = None
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key_part + "/"):
                for o in page.get("Contents", []):
                    if o["Key"].endswith(".parquet"):
                        rep_file = f"s3://{bucket}/{o['Key']}"
                        break
                if rep_file: break
            if rep_file:
                meta = con.execute(f"""
                    SELECT DISTINCT compression,
                           avg(num_rows) OVER () AS avg_rows_per_group,
                           count(*) OVER () AS row_group_count
                    FROM parquet_metadata('{rep_file}')
                """).fetchall()
                if meta:
                    layout["compressions"]        = sorted({r[0] for r in meta if r[0]})
                    layout["avg_rows_per_group"]  = int(meta[0][1]) if meta[0][1] else None
                    layout["row_group_count"]     = int(meta[0][2]) if meta[0][2] else None
                    layout["representative_file"] = rep_file
        except Exception as ex:
            layout["metadata_error"] = str(ex)[:200]

    return layout


def describe_column_stats(cfg: ModellerConfig, con: duckdb.DuckDBPyConnection,
                           uri: str, column: str) -> Dict:
    """Return stats for one column: distinct_count, null_ratio, min, max, mean, sample.

    The agent uses this to reason about cardinality (partition by low-card
    columns; sort by high-card), null density, sortedness of the underlying data.
    """
    try:
        r = con.execute(f"""
            SELECT
                approx_count_distinct("{column}")                                              AS distinct_count,
                (count(*) FILTER (WHERE "{column}" IS NULL))::DOUBLE / NULLIF(count(*), 0)     AS null_ratio,
                count(*)                                                                        AS total_rows,
                CAST(min("{column}") AS VARCHAR)                                                AS min_v,
                CAST(max("{column}") AS VARCHAR)                                                AS max_v
            FROM read_parquet('{uri}')
        """).fetchone()
        cols = ["distinct_count","null_ratio","total_rows","min","max"]
        stats = dict(zip(cols, r)) if r else {}
        # A tiny sample of distinct values (surface cardinality shape)
        samples = con.execute(f"""
            SELECT DISTINCT "{column}" FROM read_parquet('{uri}') LIMIT 10
        """).fetchall()
        stats["sample_values"] = [str(s[0]) for s in samples]
        stats["column"] = column
        return stats
    except Exception as ex:
        return {"error": str(ex)[:400], "uri": uri, "column": column}


def sample_rows(cfg: ModellerConfig, con: duckdb.DuckDBPyConnection,
                uri: str, n: int = 5) -> List[Dict]:
    """Read the first n rows as list-of-dicts. Quick data peek."""
    try:
        result = con.execute(f"SELECT * FROM read_parquet('{uri}') LIMIT {int(n)}")
        rows = result.fetchall()
        cols = [d[0] for d in result.description]
        def _safe(v):
            if v is None or isinstance(v, (int, float, str, bool)): return v
            if isinstance(v, list): return [_safe(x) for x in v]
            if isinstance(v, dict): return {k: _safe(x) for k, x in v.items()}
            return str(v)
        return [dict(zip(cols, [_safe(v) for v in r])) for r in rows]
    except Exception as ex:
        return [{"error": str(ex)[:400], "uri": uri}]


def list_prior_proposals(cfg: ModellerConfig, s3, status: Optional[str] = None) -> List[Dict]:
    """List proposals we've written previously."""
    out = []
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=cfg.bucket, Prefix=cfg.proposals_prefix,
    ):
        for o in page.get("Contents", []):
            if o["Key"].endswith(".json"):
                p = _s3_get_json(s3, cfg, o["Key"])
                if p and (status is None or p.get("status") == status):
                    out.append(p)
    return sorted(out, key=lambda p: p.get("created_at", ""), reverse=True)
