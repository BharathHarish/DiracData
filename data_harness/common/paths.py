"""Centralised S3 URI builders. Everyone else calls these — nobody concatenates paths.

Layout (locked in PLAN.md §4):
  lake/fintech/
    reference/<table>/*.parquet
    raw/<domain>/<table>/date=YYYY-MM-DD/hour=HH/part-*.parquet
    silver/<table>/date=YYYY-MM-DD/part-*.parquet
    gold/<table>/date=YYYY-MM-DD/part-*.parquet
    query_history/date=YYYY-MM-DD/part-*.parquet
    query_history_agg/day=YYYY-MM-DD/part-*.parquet
    lineage.json
    generator_state/<domain>.json
"""
from __future__ import annotations
from datetime import datetime, timezone
from data_harness.common.config import Config


def _base(cfg: Config) -> str:
    return f"s3://{cfg.bucket}/{cfg.root_prefix}"


def reference_key(cfg: Config, table: str) -> str:
    return f"{cfg.root_prefix}/reference/{table}.parquet"


def reference_uri(cfg: Config, table: str) -> str:
    return f"{_base(cfg)}/reference/{table}.parquet"


def raw_partition_key(cfg: Config, domain: str, table: str, ts: datetime) -> str:
    d = ts.strftime("%Y-%m-%d")
    h = ts.strftime("%H")
    return f"{cfg.root_prefix}/raw/{domain}/{table}/date={d}/hour={h}/part-{ts.strftime('%Y%m%dT%H%M%S')}.parquet"


def raw_scan_uri(cfg: Config, domain: str, table: str) -> str:
    """A DuckDB-friendly glob covering every partition of a raw table."""
    return f"{_base(cfg)}/raw/{domain}/{table}/**/*.parquet"


def silver_partition_key(cfg: Config, table: str, ts: datetime) -> str:
    d = ts.strftime("%Y-%m-%d")
    return f"{cfg.root_prefix}/silver/{table}/date={d}/part-{ts.strftime('%Y%m%dT%H%M%S')}.parquet"


def silver_scan_uri(cfg: Config, table: str) -> str:
    return f"{_base(cfg)}/silver/{table}/**/*.parquet"


def gold_partition_key(cfg: Config, table: str, ts: datetime) -> str:
    d = ts.strftime("%Y-%m-%d")
    return f"{cfg.root_prefix}/gold/{table}/date={d}/part-{ts.strftime('%Y%m%dT%H%M%S')}.parquet"


def gold_scan_uri(cfg: Config, table: str) -> str:
    return f"{_base(cfg)}/gold/{table}/**/*.parquet"


def query_history_key(cfg: Config, ts: datetime) -> str:
    # Include microseconds — otherwise multiple writes in the same second collide + overwrite,
    # which we hit during signal-growth loops.
    d = ts.strftime("%Y-%m-%d")
    return f"{cfg.root_prefix}/query_history/date={d}/part-{ts.strftime('%Y%m%dT%H%M%S%f')}.parquet"


def lineage_key(cfg: Config) -> str:
    return f"{cfg.root_prefix}/lineage.json"


def state_key(cfg: Config, domain: str) -> str:
    return f"{cfg.root_prefix}/generator_state/{domain}.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
