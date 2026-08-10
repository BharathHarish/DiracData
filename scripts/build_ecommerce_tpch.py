#!/usr/bin/env python3
"""Build the `ecommerce` schema from TPC-H (SF1 ~1GB) and publish it object-store-native to the lake.

TPC-H is renamed into an ecommerce business vocabulary, and every PK/FK naming crutch is REMOVED: each
table's own key gets a distinct business name, and every foreign key is named by its ROLE (e.g. a
lineitem's order reference is `item_order`, not `order_id`). Values are preserved, so the joins still
exist -- but the learning agent must DISCOVER them by value overlap + cardinality, not by matching
column names. That makes it a real join-discovery test.

    PYTHONPATH=src .venv/bin/python scripts/build_ecommerce_tpch.py --sf 1

Writes staging parquet under data/ecommerce/parquet/ (gitignored) then uploads to s3://<lake>/ecommerce/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.config import settings_from_env  # noqa: E402
from diracdata.stores import S3ObjectStore  # noqa: E402

# old_table -> (new_table, [(old_col, new_col)])  -- FK cols named by ROLE, never the referenced PK name.
MAPPING = {
    "region": ("markets", [
        ("r_regionkey", "market_id"), ("r_name", "market_name"), ("r_comment", "market_notes")]),
    "nation": ("countries", [
        ("n_nationkey", "country_id"), ("n_name", "country_name"),
        ("n_regionkey", "in_market"), ("n_comment", "country_notes")]),          # in_market -> markets.market_id
    "supplier": ("vendors", [
        ("s_suppkey", "vendor_id"), ("s_name", "vendor_name"), ("s_address", "vendor_address"),
        ("s_nationkey", "based_in_country"), ("s_phone", "vendor_phone"),          # -> countries.country_id
        ("s_acctbal", "account_balance"), ("s_comment", "vendor_notes")]),
    "customer": ("shoppers", [
        ("c_custkey", "shopper_id"), ("c_name", "shopper_name"), ("c_address", "shipping_address"),
        ("c_nationkey", "home_country"), ("c_phone", "phone"),                     # -> countries.country_id
        ("c_acctbal", "wallet_balance"), ("c_mktsegment", "segment"), ("c_comment", "shopper_notes")]),
    "part": ("products", [
        ("p_partkey", "product_id"), ("p_name", "product_name"), ("p_mfgr", "manufacturer"),
        ("p_brand", "brand"), ("p_type", "product_type"), ("p_size", "pack_size"),
        ("p_container", "container"), ("p_retailprice", "list_price"), ("p_comment", "product_notes")]),
    "partsupp": ("vendor_catalog", [
        ("ps_partkey", "catalog_product"), ("ps_suppkey", "catalog_vendor"),       # -> products / vendors
        ("ps_availqty", "available_qty"), ("ps_supplycost", "supply_cost"),
        ("ps_comment", "catalog_notes")]),
    "orders": ("orders", [
        ("o_orderkey", "order_id"), ("o_custkey", "placed_by"),                    # placed_by -> shoppers.shopper_id
        ("o_orderstatus", "order_status"), ("o_totalprice", "order_total"),
        ("o_orderdate", "order_date"), ("o_orderpriority", "priority"),
        ("o_clerk", "clerk"), ("o_shippriority", "ship_priority"), ("o_comment", "order_notes")]),
    "lineitem": ("order_items", [
        ("l_orderkey", "item_order"), ("l_partkey", "item_product"),               # item_order -> orders.order_id
        ("l_suppkey", "item_vendor"), ("l_linenumber", "line_no"), ("l_quantity", "quantity"),
        ("l_extendedprice", "gross_price"), ("l_discount", "discount"), ("l_tax", "tax"),
        ("l_returnflag", "return_flag"), ("l_linestatus", "line_status"), ("l_shipdate", "ship_date"),
        ("l_commitdate", "commit_date"), ("l_receiptdate", "receipt_date"),
        ("l_shipinstruct", "ship_instruct"), ("l_shipmode", "ship_mode"), ("l_comment", "item_notes")]),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sf", type=float, default=1.0)
    ap.add_argument("--schema", default="ecommerce")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()

    out = Path(args.data_root) / args.schema / "parquet"
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    con.execute("INSTALL tpch; LOAD tpch")
    print(f"generating TPC-H SF{args.sf} ...", file=sys.stderr)
    con.execute(f"CALL dbgen(sf={args.sf})")

    for old, (new, cols) in MAPPING.items():
        select = ", ".join(f'{o} AS {n}' for o, n in cols)
        dst = (out / f"{new}.parquet").as_posix()
        con.execute(f"COPY (SELECT {select} FROM {old}) TO '{dst}' (FORMAT PARQUET)")
        n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{dst}')").fetchone()[0]
        print(f"  {old} -> {new}: {n:,} rows, {len(cols)} cols")

    if args.no_upload:
        return 0
    s = settings_from_env(args.env_file)
    store = S3ObjectStore(bucket=s.lake_bucket, endpoint_url=s.s3_endpoint_url, region_name=s.aws_region,
                          aws_access_key_id=s.aws_access_key_id, aws_secret_access_key=s.aws_secret_access_key)
    total = 0
    for p in sorted(out.glob("*.parquet")):
        data = p.read_bytes()
        store.write_bytes(f"{args.schema}/{p.name}", data, content_type="application/octet-stream")
        total += len(data)
    print(f"uploaded {args.schema}: {total/1e6:.1f} MB -> s3://{s.lake_bucket}/{args.schema}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
