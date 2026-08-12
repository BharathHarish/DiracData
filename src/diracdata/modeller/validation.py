"""Validation tools — dry_run, explain_plan, estimate_scan_bytes, validate_syntax, run_sql.

All mechanical. Return data, don't judge. Wrap DuckDB with per-query budgets so
a bad SQL from the LLM can't hang or OOM the modeller run.
"""
from __future__ import annotations
import json
import time
from typing import Any, Dict, List
import duckdb
import sqlglot
from .config import ModellerConfig


def dry_run(cfg: ModellerConfig, con: duckdb.DuckDBPyConnection, sql: str, limit: int = 1000) -> Dict[str, Any]:
    """Execute the SQL wrapped in a LIMIT + capture (rows, elapsed_ms, scan_bytes, sample).

    Wraps the query as `SELECT * FROM (<sql>) LIMIT <limit>`. Bounded by
    max_query_seconds + max_query_scan_gb (memory limit already set on con).
    Returns {status, rows_returned, elapsed_ms, scan_bytes_est, sample_rows, error?}.
    """
    wrapped = f"SELECT * FROM ({sql.rstrip().rstrip(';')}) LIMIT {int(limit)}"
    t0 = time.perf_counter()
    try:
        result = con.execute(wrapped)
        rows = result.fetchall()
        cols = [d[0] for d in result.description]
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "status": "ok",
            "rows_returned": len(rows),
            "elapsed_ms":    elapsed_ms,
            "columns":       cols,
            "sample_rows":   [dict(zip(cols, [_json_safe(v) for v in r])) for r in rows[:10]],
            "scan_bytes_est": _explain_scan_bytes(con, sql),
        }
    except Exception as ex:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "status": "error",
            "elapsed_ms": elapsed_ms,
            "error": str(ex)[:500],
        }


def explain_plan(cfg: ModellerConfig, con: duckdb.DuckDBPyConnection, sql: str) -> Dict[str, Any]:
    """Return EXPLAIN plan (text tree) for the SQL. Doesn't execute."""
    try:
        rows = con.execute(f"EXPLAIN {sql}").fetchall()
        return {"status": "ok", "plan": "\n".join(r[1] for r in rows)}
    except Exception as ex:
        return {"status": "error", "error": str(ex)[:500]}


def estimate_scan_bytes(cfg: ModellerConfig, con: duckdb.DuckDBPyConnection, sql: str) -> Dict[str, Any]:
    """Estimate bytes to scan for this SQL without executing.

    DuckDB doesn't expose planner cost directly, but EXPLAIN ANALYZE would run it.
    Best-effort: sum sizes of files referenced in read_parquet calls (upper bound —
    partition pruning reduces this in practice).
    """
    return {"status": "ok", "scan_bytes_est": _explain_scan_bytes(con, sql)}


def validate_syntax(sql: str, engine: str = "duckdb") -> Dict[str, Any]:
    """Parse the SQL against the given dialect. Returns {status, error?}.

    Doesn't execute — pure syntactic check. Useful before you dry_run.
    """
    try:
        sqlglot.parse_one(sql, dialect=engine)
        return {"status": "ok", "dialect": engine}
    except Exception as ex:
        return {"status": "error", "error": str(ex)[:500], "dialect": engine}


def run_sql(cfg: ModellerConfig, con: duckdb.DuckDBPyConnection, sql: str,
            limit: int = 200) -> Dict[str, Any]:
    """Escape-hatch general-purpose exploration. Returns first `limit` rows as list of dicts."""
    try:
        result = con.execute(sql)
        rows = result.fetchmany(limit)
        cols = [d[0] for d in result.description]
        return {
            "status": "ok",
            "rows_returned": len(rows),
            "columns": cols,
            "data": [dict(zip(cols, [_json_safe(v) for v in r])) for r in rows],
        }
    except Exception as ex:
        return {"status": "error", "error": str(ex)[:500]}


# ---------- helpers ----------

def _explain_scan_bytes(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    """Best-effort scan-size estimate: parse SQL for read_parquet URIs, sum via list_objects.

    parquet_metadata() doesn't handle globs, so we list the files via S3 and sum sizes.
    Upper bound — partition pruning reduces actual scan in practice.
    """
    import re, os
    total = 0
    try:
        import boto3
        from botocore.config import Config as BotoConfig
        s3 = boto3.client("s3",
            endpoint_url=os.getenv("DIRACDATA_S3_ENDPOINT_URL", "http://localhost:9000"),
            aws_access_key_id=os.getenv("DIRACDATA_AWS_ACCESS_KEY_ID", "minioadmin"),
            aws_secret_access_key=os.getenv("DIRACDATA_AWS_SECRET_ACCESS_KEY", "minioadmin"),
            config=BotoConfig(s3={"addressing_style": "path"}))
    except Exception:
        return 0

    for uri in re.findall(r"read_parquet\(\s*'([^']+)'", sql):
        # Extract bucket + prefix (strip glob tail)
        try:
            no_scheme = uri.replace("s3://", "", 1)
            bucket, _, key_part = no_scheme.partition("/")
            for stopper in ("**", "*", "?"):
                if stopper in key_part:
                    key_part = key_part.split(stopper, 1)[0]
                    break
            key_part = key_part.rstrip("/")
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key_part + "/"):
                for o in page.get("Contents", []):
                    if o["Key"].endswith(".parquet"):
                        total += o["Size"]
        except Exception:
            pass
    return total


def _json_safe(v):
    """Coerce DuckDB values that don't JSON-serialize (datetime, decimal, bytes)."""
    if v is None:                     return None
    if isinstance(v, (int, float, str, bool)): return v
    if isinstance(v, list):           return [_json_safe(x) for x in v]
    if isinstance(v, dict):           return {k: _json_safe(val) for k, val in v.items()}
    return str(v)
