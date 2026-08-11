"""V3-S1 unit + integration: behavioural join facts + concise rendering.

Uses DuckDBEngine over local tmp parquet so the test is self-contained (no MinIO)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import pytest

from diracdata.engines.duckdb import DuckDBEngine
from diracdata.learning.compiler import SemanticModel
from diracdata.learning.join_facts import enrich_joins
from diracdata.learning.model_index import SemanticModelIndex, _render_join


@pytest.fixture
def engine():
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "s" / "parquet"
    root.mkdir(parents=True)
    con = duckdb.connect(":memory:")
    # clients (dim, 100 rows), online_purchases (fact, 500 lines: 5 per client on avg, all match)
    con.execute("COPY (SELECT range AS client_id FROM range(100)) TO "
                f"'{root/'clients.parquet'}' (FORMAT PARQUET)")
    con.execute("COPY (SELECT range AS line_id, (range % 100) AS billing_client_id "
                f"FROM range(500)) TO '{root/'online_purchases.parquet'}' (FORMAT PARQUET)")
    # orphans table: 10% keys don't exist in clients
    con.execute("COPY (SELECT range AS line_id, (range % 110) AS billing_client_id "
                f"FROM range(220)) TO '{root/'partial.parquet'}' (FORMAT PARQUET)")
    return DuckDBEngine(data_root=tmp, schema_name="s")


def test_clean_join_full_match_and_no_fanout(engine):
    sm = SemanticModel(schema="s")
    sm.joins = [{"left": "online_purchases", "left_keys": ["billing_client_id"],
                 "right": "clients", "right_keys": ["client_id"], "cardinality": "many_to_one"}]
    n = enrich_joins(model=sm, engine=engine, sample=500)
    assert n == 1
    j = sm.joins[0]
    assert j["match_rate"] == 1.0
    assert j["fan_out_avg"] == 1.0 and j["fan_out_max"] == 1
    assert j["disposition"].startswith("INNER")


def test_orphan_join_flags_left_disposition(engine):
    sm = SemanticModel(schema="s")
    sm.joins = [{"left": "partial", "left_keys": ["billing_client_id"],
                 "right": "clients", "right_keys": ["client_id"], "cardinality": "many_to_one"}]
    enrich_joins(model=sm, engine=engine, sample=220)
    j = sm.joins[0]
    assert j["match_rate"] < 0.95            # ~10% orphans by construction
    assert j["disposition"].startswith("LEFT")


def test_render_default_is_one_line_and_shows_new_facts():
    j = {"left": "op", "left_keys": ["k"], "right": "c", "right_keys": ["id"],
         "cardinality": "many_to_one", "match_rate": 1.0, "fan_out_avg": 1.0,
         "fan_out_max": 1, "disposition": "INNER", "sampled": 500}
    line = _render_join(j, "k", "id")
    assert "\n" not in line                                   # 1 line
    assert "match=100.0%" in line and "use INNER" in line


def test_render_adds_warning_only_when_risk():
    j = {"left": "op", "left_keys": ["k"], "right": "c", "right_keys": ["id"],
         "cardinality": "many_to_one", "match_rate": 0.90, "fan_out_avg": 1.0,
         "fan_out_max": 1, "disposition": "LEFT (keep left rows -- 10.0% would be dropped by INNER)",
         "sampled": 220}
    out = _render_join(j, "k", "id")
    assert "\n" in out and "orphan on INNER" in out
    # fan-out risk fires too
    j2 = dict(j, match_rate=1.0, fan_out_max=3, fan_out_avg=2.4,
              disposition="INNER")
    out2 = _render_join(j2, "k", "id")
    assert "fan-out risk" in out2


def test_legacy_join_without_new_fields_still_renders():
    """Zero-regression check: joins from an older model still render (additive contract)."""
    j = {"left": "a", "left_keys": ["x"], "right": "b", "right_keys": ["y"],
         "cardinality": "many_to_one", "verified_by": "legacy note"}
    line = _render_join(j, "x", "y")
    assert "many_to_one" in line and "legacy note" in line
    assert "match=" not in line               # no behavioural fields => none rendered


def test_context_joins_end_to_end(engine):
    sm = SemanticModel(schema="s")
    sm.tables = {"online_purchases": {"grain": "line", "kind": "fact"},
                 "clients": {"grain": "one row per client", "kind": "dimension"}}
    sm.joins = [{"left": "online_purchases", "left_keys": ["billing_client_id"],
                 "right": "clients", "right_keys": ["client_id"], "cardinality": "many_to_one"}]
    enrich_joins(model=sm, engine=engine, sample=500)
    idx = SemanticModelIndex(sm.to_yaml_dict() if hasattr(sm, "to_yaml_dict")
                              else {"schema": "s", "models": sm.tables, "relationships": sm.joins,
                                    "metrics": {}, "dimensions": {}})
    txt = idx.joins("online_purchases")
    assert "match=100.0%" in txt and "use INNER" in txt
