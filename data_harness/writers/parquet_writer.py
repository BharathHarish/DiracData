"""Buffered parquet writer — pyarrow.Table → bytes → boto3 put_object.

Supports two layouts:
  - unpartitioned (reference tables): one file per write, key from caller
  - Hive-partitioned by date+hour (raw): splits table by _event_ts, one file per (date, hour)

Silver/gold writers use the same primitives but partition by date only.
"""
from __future__ import annotations
import io
from datetime import datetime, timezone
from typing import Dict
import pyarrow as pa
import pyarrow.parquet as pq
from data_harness.common.config import Config
from data_harness.common.paths import (raw_partition_key, silver_partition_key,
                                       gold_partition_key, reference_key, utc_now)


_COMPRESSION = "zstd"
_ROW_GROUP = 100_000


def _table_bytes(table: pa.Table) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table, buf, compression=_COMPRESSION, row_group_size=_ROW_GROUP,
                   use_dictionary=True, write_statistics=True)
    return buf.getvalue()


def write_reference(s3, cfg: Config, table_name: str, table: pa.Table) -> Dict:
    """One file per reference table — overwrites on repeat (idempotent seed)."""
    key = reference_key(cfg, table_name)
    data = _table_bytes(table)
    s3.put_object(Bucket=cfg.bucket, Key=key, Body=data)
    return {"key": key, "rows": table.num_rows, "bytes": len(data)}


def _ensure_ts_cols(table: pa.Table, now: datetime) -> pa.Table:
    """Add _event_ts/_ingest_ts filled with `now` if the generator forgot them."""
    ts_arr = pa.array([now] * table.num_rows, type=pa.timestamp("us", tz="UTC"))
    if "_event_ts" not in table.column_names:
        table = table.append_column("_event_ts", ts_arr)
    if "_ingest_ts" not in table.column_names:
        table = table.append_column("_ingest_ts", ts_arr)
    return table


def write_raw(s3, cfg: Config, domain: str, table_name: str, table: pa.Table,
              ts_col: str = "_event_ts") -> list[Dict]:
    """Hive partition by date+hour on ts_col. Returns list of {key, rows, bytes} per partition."""
    if table.num_rows == 0:
        return []
    table = _ensure_ts_cols(table, utc_now())
    ts = table.column(ts_col).to_pylist()
    # Group row indices by (date, hour) key
    by_part: dict[tuple[str, str], list[int]] = {}
    for i, t in enumerate(ts):
        if t is None:
            t = utc_now()
        if isinstance(t, str):
            t = datetime.fromisoformat(t.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        d, h = t.strftime("%Y-%m-%d"), t.strftime("%H")
        by_part.setdefault((d, h), []).append(i)
    out = []
    now = utc_now()
    for (d, h), idxs in by_part.items():
        sub = table.take(pa.array(idxs))
        # Anchor the partition key to a representative ts in the group
        anchor = datetime.strptime(f"{d}T{h}:00:00+0000", "%Y-%m-%dT%H:%M:%S%z")
        # Uniquify file name with wall-clock write-time (so re-runs don't clobber)
        key = raw_partition_key(cfg, domain, table_name, anchor.replace(
            minute=now.minute, second=now.second, microsecond=now.microsecond))
        data = _table_bytes(sub)
        s3.put_object(Bucket=cfg.bucket, Key=key, Body=data)
        out.append({"key": key, "rows": sub.num_rows, "bytes": len(data),
                    "date": d, "hour": h})
    return out


def write_silver(s3, cfg: Config, table_name: str, table: pa.Table) -> Dict:
    now = utc_now()
    key = silver_partition_key(cfg, table_name, now)
    data = _table_bytes(table)
    s3.put_object(Bucket=cfg.bucket, Key=key, Body=data)
    return {"key": key, "rows": table.num_rows, "bytes": len(data)}


def write_gold(s3, cfg: Config, table_name: str, table: pa.Table) -> Dict:
    now = utc_now()
    key = gold_partition_key(cfg, table_name, now)
    data = _table_bytes(table)
    s3.put_object(Bucket=cfg.bucket, Key=key, Body=data)
    return {"key": key, "rows": table.num_rows, "bytes": len(data)}
