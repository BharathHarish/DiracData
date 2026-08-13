"""Unit tests for MCP phase 1-5 pure modules (no MinIO / no live Cursor)."""
from __future__ import annotations

import json

import duckdb
import pytest

from diracdata.mcps.bind_check import metric_bind_check
from diracdata.mcps.boundary import detect_boundary_convention
from diracdata.mcps.completeness import completeness_check, fabric_health
from diracdata.mcps.dialect import dialect_card
from diracdata.mcps.experiences import save_experience
from diracdata.mcps.fabric_fields import companions_of, enrich_metric_fields
from diracdata.mcps.instructions import catalog_instructions, learn_pack, query_pack, schema_instructions
from diracdata.mcps.ontology import empty_channel_maps, map_utm_to_channel
from diracdata.mcps.prompts_mcp import prompt_executive_scorecard, prompt_learn_database
from diracdata.mcps.register import parse_dirac_uri


class _Eng:
    def __init__(self, con):
        self._con = con

    def query(self, sql, max_rows=1):
        res = self._con.execute(sql)
        rows = res.fetchmany(max_rows)
        cols = [d[0] for d in res.description] if res.description else []

        class R:
            pass

        r = R()
        r.columns = cols
        r.rows = rows
        return r

    def list_tables(self):
        return [x[0] for x in self._con.execute("SHOW TABLES").fetchall()]


@pytest.fixture
def eng():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE sales(id INTEGER, net_paid DOUBLE)")
    con.execute("INSERT INTO sales VALUES (1, 10.0)")
    return _Eng(con)


def test_instructions_packs_have_required_sections():
    from diracdata.mcps.instructions import SCHEMA_ONLY_QUERY_TOOLS

    q, l = query_pack(), learn_pack()
    assert "DIALECT" in q
    assert "GO BEYOND" in q
    assert "completeness_check" in l
    assert "COMPLETION CHECKLIST" in l
    cat = catalog_instructions()
    sch = schema_instructions()
    assert "QUERY" in cat and "LEARN" in cat
    assert "get_dialect" in sch
    # Schema MCP keeps the five join/RCA tools; catalog MCP must not advertise them.
    for name in SCHEMA_ONLY_QUERY_TOOLS:
        assert name in sch, name
        assert name in q, name
        assert name not in cat, name
    assert "search_fabric" in cat
    assert "sql_diff" in cat
    assert "data_check" in cat
    assert "join_path" in cat
    assert "children-per-parent" in cat or "GRAIN" in cat
    assert "search_context" not in cat


def test_dialect_cards():
    d = dialect_card("duckdb")
    assert d["indexing_base"] == 1
    assert "UNNEST" in d["cheat_sheet"]
    s = dialect_card(engine_kind="duckdb+sqlite")
    assert s["dialect"] == "duckdb"


def test_metric_bind_check_ok_and_missing(eng):
    ok = metric_bind_check(eng, "SELECT SUM(net_paid) FROM sales")
    assert ok["ok"] is True
    bad = metric_bind_check(eng, "SELECT SUM(gross_amount) FROM sales")
    assert bad["ok"] is False
    assert bad["parse_error"]


def test_completeness_stub_and_green():
    fabric = {
        "db_a": {
            "metadata_descriptions.json": {
                "tables": {
                    "sales": {
                        "description": "orders",
                        "grain": "one row per order",
                        "columns": {
                            "id": {"description": "pk"},
                            "net_paid": {"description": "amount"},
                        },
                    }
                }
            },
            "join_facts.json": [
                {"left_table": "sales", "left_col": "id", "right_table": "x",
                 "right_col": "id", "cardinality": "N-1",
                 "match_rate": 1.0, "fan_out_avg": 1.0, "verified_by": "unit test"}
            ],
            "database.md": "# sales db\n\n" + ("Business narrative. " * 40),
            "semantic_layer.yaml": "metrics:\n- name: rev\n  sql: SELECT 1\n",
        }
    }

    def get_text(db, name):
        return fabric[db].get(name)

    def get_json(db, name):
        return fabric[db].get(name)

    green = completeness_check(db="db_a", get_text=get_text, get_json=get_json)
    assert green["ok"] is True

    fabric["db_a"]["database.md"] = "# stub\n"
    red = completeness_check(db="db_a", get_text=get_text, get_json=get_json)
    assert red["ok"] is False
    assert red["indexes_stub"] is True


def test_fabric_health_missing_catalog():
    def get_text(db, name):
        return None

    def get_json(db, name):
        return {}

    h = fabric_health(
        catalog="c",
        list_dbs=lambda: ["db_a"],
        get_catalog_text=lambda _n: None,
        get_text=get_text,
        get_json=get_json,
    )
    assert h["ok"] is False
    assert h["missing_catalog_md"] is True


def test_experiences_append_dedupe():
    md = save_experience(None, insight="XS is always OOS", evidence="variants.in_stock")
    assert "GOTCHAS" in md
    md2 = save_experience(md, insight="XS is always OOS", evidence="again")
    assert md2.count("XS is always OOS") == 1


def test_boundary_and_companions():
    b = detect_boundary_convention("amount_threshold", ["0", "10", "20"])
    assert b["applies"] is True
    assert b["convention"]
    m = enrich_metric_fields({"name": "net_revenue"}, companions=["refund_rate"])
    assert companions_of(m) == ["refund_rate"]


def test_channel_map_utm():
    maps = empty_channel_maps()
    assert map_utm_to_channel(maps, "google") == "search"
    assert map_utm_to_channel(maps, "tiktok") == "social"


def test_parse_uri_and_prompts():
    p = parse_dirac_uri("metric://cat/db/net_revenue")
    assert p["scheme"] == "metric"
    assert p["parts"] == ["cat", "db", "net_revenue"]
    assert "completeness_check" in prompt_learn_database("retail")
    assert "Money" in prompt_executive_scorecard()
    schema_sc = prompt_executive_scorecard(surface="schema")
    catalog_sc = prompt_executive_scorecard(surface="catalog")
    assert "temporal_coverage" in schema_sc
    assert "temporal_coverage" not in catalog_sc
    assert "run_sql" in catalog_sc or "profile" in catalog_sc
