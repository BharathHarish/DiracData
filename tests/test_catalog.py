"""Unit tests for the C1 catalog data model.

Additive — proves the new module works in isolation and preserves the legacy
single-schema shape via `wrap_legacy`. Does not exercise storage (C2) or
agent wiring (C4+). Those will get their own tests.
"""
from __future__ import annotations

from diracdata.context.catalog import (
    DEFAULT_SCHEMA, LEGACY_CATALOG,
    Catalog, Database, Schema, TableRef, Join, Metric, Example, wrap_legacy,
)


# ---------- TableRef / fqn / qualify ----------

def test_table_ref_fqn_is_catalog_db_schema_table():
    tr = TableRef(catalog="spider2_local", database="chinook", schema="main", table="tracks")
    assert tr.fqn == "spider2_local.chinook.main.tracks"


def test_table_ref_qualify_drops_main_schema_by_default():
    tr = TableRef(catalog="local", database="retail_complex", schema="main", table="online_purchases")
    assert tr.qualify() == "local.retail_complex.online_purchases"


def test_table_ref_qualify_keeps_non_main_schema():
    tr = TableRef(catalog="pg_prod", database="sales", schema="public", table="orders")
    assert tr.qualify() == "pg_prod.sales.public.orders"


# ---------- Join / is_cross_db ----------

def test_join_within_database_is_not_cross_db():
    a = TableRef("local", "retail", "main", "orders")
    b = TableRef("local", "retail", "main", "customers")
    j = Join(left=a, right=b, left_keys=["customer_id"], right_keys=["customer_id"],
             cardinality="N-1", disposition="INNER")
    assert not j.is_cross_db


def test_join_across_databases_is_cross_db():
    a = TableRef("local", "retail_complex", "main", "clients")
    b = TableRef("local", "fintech_complex", "main", "users")
    j = Join(left=a, right=b, left_keys=["client_ref"], right_keys=["user_ref"])
    assert j.is_cross_db


def test_join_across_catalogs_is_cross_db():
    a = TableRef("prod_snowflake", "sales", "public", "orders")
    b = TableRef("legacy_pg", "billing", "public", "invoices")
    j = Join(left=a, right=b)
    assert j.is_cross_db


# ---------- Database defaults ----------

def test_database_auto_creates_main_schema_when_none_provided():
    d = Database(name="chinook", catalog="spider2_local")
    assert DEFAULT_SCHEMA in d.schemas
    assert d.default_schema().name == DEFAULT_SCHEMA


def test_database_table_names_flatten_across_schemas():
    d = Database(name="pg_sales", catalog="pg_prod", schemas={
        "public":    Schema(name="public",    database="pg_sales", catalog="pg_prod", table_names=["orders", "customers"]),
        "reporting": Schema(name="reporting", database="pg_sales", catalog="pg_prod", table_names=["daily_revenue"]),
    })
    assert set(d.table_names()) == {"orders", "customers", "daily_revenue"}


def test_schema_table_ref_produces_correctly_qualified_ref():
    s = Schema(name="main", database="chinook", catalog="spider2_local", table_names=["tracks"])
    tr = s.table_ref("tracks")
    assert tr.fqn == "spider2_local.chinook.main.tracks"


# ---------- Catalog navigation ----------

def test_catalog_db_accessor_returns_database():
    d = Database(name="chinook", catalog="spider2_local")
    c = Catalog(name="spider2_local", engine="duckdb", databases={"chinook": d})
    assert c.db("chinook") is d


def test_catalog_db_accessor_raises_helpful_error_when_missing():
    c = Catalog(name="spider2_local", databases={"chinook": Database(name="chinook", catalog="spider2_local")})
    try:
        c.db("northwind")
    except KeyError as ex:
        assert "chinook" in str(ex)   # available list is included
        assert "northwind" in str(ex) # requested name is included
    else:
        assert False, "expected KeyError"


def test_catalog_database_names_are_sorted():
    c = Catalog(name="local", databases={
        "fintech_complex": Database(name="fintech_complex", catalog="local"),
        "retail_complex":  Database(name="retail_complex",  catalog="local"),
    })
    assert c.database_names() == ["fintech_complex", "retail_complex"]


def test_catalog_all_within_db_joins_aggregates_from_every_database():
    j1 = Join(left=TableRef("local","a","main","x"), right=TableRef("local","a","main","y"))
    j2 = Join(left=TableRef("local","b","main","p"), right=TableRef("local","b","main","q"))
    c = Catalog(name="local", databases={
        "a": Database(name="a", catalog="local", joins=[j1]),
        "b": Database(name="b", catalog="local", joins=[j2]),
    })
    assert len(c.all_within_db_joins()) == 2
    # cross_db_joins on the Catalog itself remain separate:
    assert c.cross_db_joins == []


# ---------- Legacy shim ----------

def test_wrap_legacy_creates_local_catalog_with_one_database():
    c = wrap_legacy("retail_complex", table_names=["clients", "online_purchases"])
    assert c.name == LEGACY_CATALOG
    assert c.name == "local"                                # confirm the constant
    assert list(c.databases.keys()) == ["retail_complex"]
    d = c.db("retail_complex")
    assert d.default_schema().table_names == ["clients", "online_purchases"]
    # fqn wiring for a wrapped table
    tr = d.default_schema().table_ref("clients")
    assert tr.fqn == "local.retail_complex.main.clients"


def test_wrap_legacy_handles_empty_table_list():
    c = wrap_legacy("cold_schema")
    d = c.db("cold_schema")
    assert d.default_schema().table_names == []
