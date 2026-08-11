"""V3-S2 unit + integration: verified-runnable recipe snippets for COMPLEX columns.

Uses DuckDBEngine over local tmp parquet so the test is self-contained (no MinIO). Covers the exact
shape that broke Cursor and my own agent: deep struct-of-array-of-struct-of-array-of-struct."""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import pytest

from diracdata.engines.duckdb import DuckDBEngine
from diracdata.learning.compiler import SemanticModel
from diracdata.learning.recipe_verify import build_runnable, enrich_recipes, verify_runnable


@pytest.fixture
def engine():
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "s" / "parquet"
    root.mkdir(parents=True)
    con = duckdb.connect(":memory:")
    # array-of-struct (single unnest level)
    con.execute(f"""COPY (SELECT range AS id,
        [{{'channel':'social','clicks':100}}, {{'channel':'email','clicks':50}}] AS touchpoints
        FROM range(3)) TO '{root/'campaigns.parquet'}' (FORMAT PARQUET)""")
    # deep struct-of-array-of-struct-of-array-of-struct (mirrors fintech fulfillment)
    con.execute(f"""COPY (SELECT range AS order_id,
        {{'warehouse':'WH', 'shipments': [
            {{'carrier':'ups', 'items': [{{'sku':'A','qty':1}}, {{'sku':'B','qty':2}}]}},
            {{'carrier':'dhl', 'items': [{{'sku':'C','qty':1}}]}} ]}} AS fulfillment
        FROM range(3)) TO '{root/'orders.parquet'}' (FORMAT PARQUET)""")
    # simple struct-of-struct (no unnest)
    con.execute(f"""COPY (SELECT range AS id,
        {{'device': {{'os':'ios','browser':'safari'}}}} AS session_context
        FROM range(3)) TO '{root/'sessions.parquet'}' (FORMAT PARQUET)""")
    return DuckDBEngine(data_root=tmp, schema_name="s")


def test_build_runnable_for_deep_nested_array_of_struct(engine):
    ty = engine.describe_columns("orders")[1]["column_type"]   # the fulfillment struct type
    sql = build_runnable("orders", "fulfillment", ty)
    assert sql and "WITH" in sql and "UNNEST" in sql
    assert verify_runnable(engine, sql)                        # actually runs on the engine


def test_build_runnable_for_array_of_struct(engine):
    ty = engine.describe_columns("campaigns")[1]["column_type"]
    sql = build_runnable("campaigns", "touchpoints", ty)
    assert sql and "UNNEST(campaigns.touchpoints)" not in sql   # never inline; CTE-staged
    assert verify_runnable(engine, sql)


def test_build_runnable_for_pure_struct_no_ctes(engine):
    ty = engine.describe_columns("sessions")[1]["column_type"]
    sql = build_runnable("sessions", "session_context", ty)
    assert sql and "WITH" not in sql                            # struct-only => direct path
    assert verify_runnable(engine, sql)


def test_scalar_columns_get_no_runnable_example(engine):
    # id column is scalar -> no recipe needed
    sql = build_runnable("orders", "order_id", "BIGINT")
    assert sql is None


def test_enrich_recipes_stores_working_form_on_the_column(engine):
    sm = SemanticModel(schema="s")
    sm.columns = {"orders": {"fulfillment": {"short": "nested", "long": "deep",
                                              "access_recipe": "UNNEST(UNNEST(fulfillment.shipments).items).sku"}}}
    ok = enrich_recipes(model=sm, engine=engine)
    assert ok == 1
    cd = sm.columns["orders"]["fulfillment"]
    assert cd["runnable_example"].startswith("WITH")
    assert cd["runnable_dialect"] == "duckdb"


def test_metadata_folds_runnable_into_long_description(engine):
    sm = SemanticModel(schema="s")
    sm.columns = {"orders": {"fulfillment": {"short": "shipments", "long": "deep",
                                              "access_recipe": "UNNEST(...).sku"}}}
    enrich_recipes(model=sm, engine=engine)
    meta = sm.to_metadata_descriptions()
    ld = meta["columns"]["orders"]["fulfillment"]["long_description"]
    assert "Runnable example (duckdb, verified):" in ld
    assert "WITH" in ld
