"""Phase 1: CTE join detection + children-per-parent grain amplification in data_check."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from diracdata.mcps.catalog_server import catalog_tools
from diracdata.utils.sql import analyze_sql_references
from diracdata.utils.stewardship import probe_footprint
from tests.test_catalog_mcp_server import _make_runtime


def _sqlite_orders_payments(tmp_path: Path) -> Path:
    """1 order → 3 payments: classic parent-grain amplification setup."""
    path = tmp_path / "db_a.sqlite"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE orders (order_ref TEXT PRIMARY KEY, score DOUBLE)")
    con.execute("CREATE TABLE payments (payment_ref TEXT, order_ref TEXT, status TEXT)")
    con.executemany("INSERT INTO orders VALUES (?, ?)", [("o1", 0.5), ("o2", 0.5)])
    con.executemany(
        "INSERT INTO payments VALUES (?, ?, ?)",
        [
            ("p1", "o1", "SUCCESS"),
            ("p2", "o1", "FAILED"),
            ("p3", "o1", "SUCCESS"),
            ("p4", "o2", "SUCCESS"),
        ],
    )
    con.commit()
    con.close()
    return path


def test_analyze_sql_references_recovers_cte_join_pairs():
    table_columns = {
        "orders": ["order_ref", "score"],
        "payments": ["payment_ref", "order_ref", "status"],
    }
    sql = """
    WITH rules AS (SELECT o.order_ref, o.score FROM orders o)
    SELECT COUNT(*) FROM rules r JOIN payments p ON p.order_ref = r.order_ref
    """
    a = analyze_sql_references(sql, table_columns)
    pairs = {(jp.left_column, jp.right_column) for jp in a.join_pairs}
    assert ("orders.order_ref", "payments.order_ref") in pairs or \
           ("payments.order_ref", "orders.order_ref") in pairs


def test_probe_footprint_flags_children_per_parent(tmp_path):
    from diracdata.mcps.catalog_server import _SqliteBackedDuckDB

    sqlite_path = _sqlite_orders_payments(tmp_path)
    eng = _SqliteBackedDuckDB(sqlite_path, "db_a", max_rows=100)
    sql = """
    WITH rules AS (SELECT o.order_ref, o.score FROM orders o)
    SELECT AVG(r.score) FROM rules r JOIN payments p ON p.order_ref = r.order_ref
    """
    dq = probe_footprint(eng, sql)
    assert dq.get("joins"), f"expected join probe, got {dq}"
    join = dq["joins"][0]
    assert join.get("children_per_parent_avg", 0) > 1.5
    assert any("amplif" in f.lower() or "children" in f.lower() for f in dq.get("flags") or [])


def test_join_path_cards_merge_and_warn(tmp_path):
    from diracdata.mcps.harness import join_path_cards, load_join_facts, render_join_card

    rt = _make_runtime(tmp_path)
    rt.store.write_text(
        "fabric/catalogs/test_cat/databases/db_a/semantic_model.yaml",
        """
relationships:
  - left: payments
    left_keys: [order_ref]
    right: orders
    right_keys: [order_ref]
    cardinality: many_to_one
    match_rate: 1.0
    fan_out_avg: 1.0
    fan_out_max: 1
    disposition: INNER
    verified_by: 0 orphans
""",
        "text/yaml",
    )
    rt.store.write_json(
        "fabric/catalogs/test_cat/databases/db_a/join_facts.json",
        [{"left_table": "payments", "left_col": "order_ref",
          "right_table": "orders", "right_col": "order_ref", "cardinality": "N-1"}],
    )
    joins = load_join_facts(rt.cs, "test_cat", "db_a")
    assert len(joins) == 1  # deduped
    card = render_join_card({**joins[0], "children_per_parent_avg": 1.73})
    assert "payments" in card and "orders" in card
    assert "children/parent" in card
    assert "aggregate-then-join" in card

    # Live enrichment via sqlite engine
    sqlite_path = _sqlite_orders_payments(tmp_path)
    from diracdata.mcps.catalog_server import _SqliteBackedDuckDB
    eng = _SqliteBackedDuckDB(sqlite_path, "db_a", max_rows=100)
    out = join_path_cards(rt.cs, "test_cat", "db_a", table="orders", engine=eng)
    assert out["ok"] and out["n_joins"] == 1
    assert out["joins"][0].get("children_per_parent_avg", 0) > 1.5
    tools = {t.__name__: t for t in catalog_tools(rt)}
    rt.store.write_json(
        "fabric/catalogs/test_cat/catalog.json",
        {"name": "test_cat", "engine": "duckdb+sqlite"},
    )
    rt._download_sqlite = lambda db: sqlite_path
    tools["use_database"]("db_a")
    jp = json.loads(tools["join_path"]("orders"))
    assert jp["n_joins"] == 1
    assert "children/parent" in jp["text"] or jp["joins"][0].get("children_per_parent_avg")


def test_propose_join_rejects_and_measures(tmp_path):
    sqlite_path = _sqlite_orders_payments(tmp_path)
    rt = _make_runtime(tmp_path)
    rt.store.write_json(
        "fabric/catalogs/test_cat/catalog.json",
        {"name": "test_cat", "engine": "duckdb+sqlite"},
    )
    rt._download_sqlite = lambda db: sqlite_path
    tools = {t.__name__: t for t in catalog_tools(rt)}
    tools["use_database"]("db_a")

    bad = json.loads(tools["propose_join"](
        "orders", "order_ref", "payments", "payment_ref", db="db_a",
    ))
    assert bad["ok"] is False and bad.get("rejected") is True

    good = json.loads(tools["propose_join"](
        "payments", "order_ref", "orders", "order_ref", db="db_a",
    ))
    assert good["ok"] is True
    assert good["measurement"]["verdict"] == "accept"
    assert good["edge"]["children_per_parent_avg"] > 1.5
    assert good["warnings"]  # amplification warning

    v = json.loads(tools["verify_join"](
        "payments", "order_ref", "orders", "order_ref", "db_a",
    ))
    assert v["verdict"] == "accept"


def test_completeness_flags_unmeasured_joins():
    from diracdata.mcps.completeness import completeness_check
    fabric = {
        "db_a": {
            "metadata_descriptions.json": {
                "tables": {
                    "orders": {
                        "grain": "one row per order",
                        "description": "orders",
                        "columns": {"order_ref": {"description": "pk"}},
                    }
                }
            },
            "join_facts.json": [
                {"left_table": "payments", "left_col": "order_ref",
                 "right_table": "orders", "right_col": "order_ref",
                 "cardinality": "N-1"},  # label only — not measured
            ],
            "database.md": "# db\n\n" + ("Business narrative. " * 40),
            "semantic_layer.yaml": "metrics:\n- name: rev\n  sql: SELECT 1\n",
        }
    }

    def get_text(db, name):
        return fabric[db].get(name)

    def get_json(db, name):
        return fabric[db].get(name)

    red = completeness_check(db="db_a", get_text=get_text, get_json=get_json)
    assert red["ok"] is False
    assert red["joins_unmeasured"]

    fabric["db_a"]["join_facts.json"][0]["match_rate"] = 1.0
    fabric["db_a"]["join_facts.json"][0]["verified_by"] = "measured"
    greenish = completeness_check(db="db_a", get_text=get_text, get_json=get_json)
    assert greenish["joins_unmeasured"] == []

    # Label-only join_facts + measured semantic_model edge → prefer measured
    fabric["db_a"]["join_facts.json"] = [
        {"left_table": "payments", "left_col": "order_ref",
         "right_table": "orders", "right_col": "order_ref", "cardinality": "N-1"},
    ]
    fabric["db_a"]["semantic_model.yaml"] = """
relationships:
  - left: payments
    left_keys: [order_ref]
    right: orders
    right_keys: [order_ref]
    cardinality: many_to_one
    match_rate: 1.0
    fan_out_avg: 1.0
    verified_by: measured in SM
"""
    merged = completeness_check(db="db_a", get_text=get_text, get_json=get_json)
    assert merged["joins_unmeasured"] == []
    assert merged["n_joins"] == 1


def test_catalog_data_check_flags_bad_and_clears_good(tmp_path):
    sqlite_path = _sqlite_orders_payments(tmp_path)
    rt = _make_runtime(tmp_path)
    rt.store.write_json(
        "fabric/catalogs/test_cat/catalog.json",
        {"name": "test_cat", "engine": "duckdb+sqlite"},
    )
    rt._download_sqlite = lambda db: sqlite_path
    tools = {t.__name__: t for t in catalog_tools(rt)}
    tools["use_database"](db="db_a")

    bad = """
    WITH rules AS (SELECT o.order_ref, o.score FROM orders o)
    SELECT AVG(CASE WHEN p.status='SUCCESS' THEN 1.0 ELSE 0 END) AS success_share
    FROM rules r JOIN payments p ON p.order_ref = r.order_ref
    """
    good = """
    WITH order_success AS (
      SELECT order_ref, MAX(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END) AS has_success
      FROM payments GROUP BY 1
    )
    SELECT AVG(COALESCE(s.has_success, 0) * 1.0) AS success_share
    FROM orders o LEFT JOIN order_success s ON o.order_ref = s.order_ref
    """
    bad_out = json.loads(tools["data_check"](bad))
    good_out = json.loads(tools["data_check"](good))
    assert bad_out["ok"] is False
    assert any("amplif" in f.lower() or "children" in f.lower() for f in bad_out["flags"])
    # Good path aggregates payments first — join probe on order_success CTE may still
    # see payments↔orders if both appear; accept ok=True OR no amplification flag.
    amp = [f for f in (good_out.get("flags") or []) if "amplif" in f.lower() or "children" in f.lower()]
    assert amp == [], f"good query should not amplify; flags={good_out.get('flags')}"
