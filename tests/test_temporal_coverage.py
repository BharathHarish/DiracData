"""V3-S3 unit + integration: temporal_coverage catches the silent nearest-day proxy."""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import pytest

from diracdata.context.temporal import temporal_coverage
from diracdata.engines.duckdb import DuckDBEngine


@pytest.fixture
def engine():
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "s" / "parquet"
    root.mkdir(parents=True)
    con = duckdb.connect(":memory:")
    # sales: 2000-01-01 .. 2003-12-31 (4 years)
    con.execute(f"COPY (SELECT DATE '2000-01-01' + INTERVAL (range) DAY AS sale_date "
                f"FROM range(1461)) TO '{root/'sales.parquet'}' (FORMAT PARQUET)")
    # stock: 2002-01-01 .. 2004-12-31 (3 years, ~50% overlap with sales)
    con.execute(f"COPY (SELECT DATE '2002-01-01' + INTERVAL (range) DAY AS snapshot_date "
                f"FROM range(1096)) TO '{root/'stock.parquet'}' (FORMAT PARQUET)")
    # campaigns: 1995-01-01 .. 1997-12-31 (before sales -> ZERO overlap with sales)
    con.execute(f"COPY (SELECT DATE '1995-01-01' + INTERVAL (range) DAY AS start_date "
                f"FROM range(1095)) TO '{root/'campaigns.parquet'}' (FORMAT PARQUET)")
    return DuckDBEngine(data_root=tmp, schema_name="s")


def test_partial_overlap_warns(engine):
    r = temporal_coverage(engine, "sales.sale_date", "stock.snapshot_date")
    assert r["overlap"]["days"] > 0
    assert r["overlap_pct_a"] < 1.0 and r["overlap_pct_b"] < 1.0
    assert r["warning"] and "PARTIAL OVERLAP" in r["warning"]


def test_zero_overlap_screams(engine):
    r = temporal_coverage(engine, "sales.sale_date", "campaigns.start_date")
    assert r["overlap"]["days"] == 0.0
    assert r["warning"] and "NO OVERLAP" in r["warning"]
    assert "nearest-day proxy" in r["warning"]


def test_full_overlap_no_warning(engine):
    r = temporal_coverage(engine, "sales.sale_date", "sales.sale_date")
    assert r["overlap_pct_a"] == 1.0 and r["overlap_pct_b"] == 1.0
    assert r["warning"] is None


def test_bad_ref_raises_helpful_error(engine):
    with pytest.raises(ValueError, match="expected 'table.column'"):
        temporal_coverage(engine, "salesdate", "stock.snapshot_date")
