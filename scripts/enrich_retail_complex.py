#!/usr/bin/env python3
"""Build the `retail_complex` schema: the full 24-table retail estate (unchanged tables symlinked, so
no data is duplicated or destroyed) PLUS complex/nested columns added to a strategic few tables, so the
learning agent + base Agent can be exercised on complex types at real scale.

Synthesis is DETERMINISTIC (hash-seeded off each row's key -- no RNG), so it is reproducible.

Complex-type coverage:
  merchandise.attributes         MAP(VARCHAR, VARCHAR)
  merchandise.variants           LIST(STRUCT(variant_sku, size, in_stock))          -- array of struct
  clients.preferences            JSON                                               -- json blob
  clients.contact_methods        LIST(STRUCT(kind, value, verified))                -- array of struct
  marketing_campaigns.touchpoints LIST(STRUCT(channel, metrics STRUCT(impr, clicks))) -- array of struct-with-struct
  online_purchases.session_context STRUCT(device STRUCT(os,browser), utm STRUCT(source,medium), page_views INT[])
                                                                                     -- deep struct-of-struct + array

    PYTHONPATH=src .venv/bin/python scripts/enrich_retail_complex.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "retail_analytics" / "parquet" / "sf1"
DST = ROOT / "data" / "retail_complex" / "parquet"

# key expr per enriched table -> the complex-column SELECT additions (DuckDB SQL)
# DuckDB hash() -> UBIGINT (already non-negative); array indexing needs BIGINT, so every index is
# CAST(hash(...) % N AS BIGINT). range(1, 2+k) yields 1..(1+k) elements.
ENRICH = {
    "merchandise": """
        MAP(['material','color','warranty_months'],
            [(['cotton','leather','plastic','steel','wood'])[1 + CAST(hash(merchandise_record) % 5 AS BIGINT)],
             (['black','white','red','blue','green'])[1 + CAST(hash(merchandise_record * 7) % 5 AS BIGINT)],
             CAST(hash(merchandise_record * 13) % 36 AS VARCHAR)]) AS attributes,
        list_transform(range(1, 2 + CAST(hash(merchandise_record) % 3 AS BIGINT)),
            i -> struct_pack(
                variant_sku := 'V-' || merchandise_code || '-' || CAST(i AS VARCHAR),
                size := (['XS','S','M','L','XL'])[1 + CAST((hash(merchandise_record) + i) % 5 AS BIGINT)],
                in_stock := (CAST((hash(merchandise_record) + i) % 5 AS BIGINT) <> 0))) AS variants
    """,
    "clients": """
        to_json(struct_pack(
            email_opt_in := (CAST(hash(client_record) % 2 AS BIGINT) = 0),
            pref_channel := (['email','sms','push','none'])[1 + CAST(hash(client_record) % 4 AS BIGINT)],
            locale := (['en-US','en-GB','fr-FR','de-DE'])[1 + CAST(hash(client_record * 3) % 4 AS BIGINT)])) AS preferences,
        list_transform(range(1, 2 + CAST(hash(client_record) % 3 AS BIGINT)),
            i -> struct_pack(
                kind := (['email','phone','mail'])[1 + CAST((hash(client_record) + i) % 3 AS BIGINT)],
                value := 'contact-' || CAST(hash(client_record * 11 + i) % 1000000 AS VARCHAR),
                verified := (CAST((hash(client_record) + i) % 3 AS BIGINT) <> 0))) AS contact_methods
    """,
    "marketing_campaigns": """
        list_transform(range(1, 2 + CAST(hash(campaign_record) % 4 AS BIGINT)),
            i -> struct_pack(
                channel := (['search','social','display','email','affiliate'])[1 + CAST((hash(campaign_record) + i) % 5 AS BIGINT)],
                metrics := struct_pack(
                    impressions := CAST(hash(campaign_record * 5 + i) % 100000 AS BIGINT),
                    clicks := CAST(hash(campaign_record * 7 + i) % 5000 AS BIGINT)))) AS touchpoints
    """,
    "online_purchases": """
        struct_pack(
            device := struct_pack(
                os := (['ios','android','windows','macos','linux'])[1 + CAST(hash(order_number, merchandise_ref) % 5 AS BIGINT)],
                browser := (['chrome','safari','firefox','edge'])[1 + CAST(hash(order_number, sale_calendar_day_ref) % 4 AS BIGINT)]),
            utm := struct_pack(
                source := (['google','meta','tiktok','email','direct'])[1 + CAST(hash(merchandise_ref, billing_client_ref) % 5 AS BIGINT)],
                medium := (['cpc','organic','social','referral'])[1 + CAST(hash(billing_client_ref) % 4 AS BIGINT)]),
            page_views := list_transform(range(1, 2 + CAST(hash(order_number) % 4 AS BIGINT)),
                i -> CAST((hash(order_number, merchandise_ref) + i * 7) % 50 AS INTEGER))) AS session_context
    """,
}


def main() -> int:
    if not SRC.exists():
        print(f"missing source {SRC}", file=sys.stderr)
        return 1
    DST.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    tables = sorted(p.stem for p in SRC.glob("*.parquet"))
    for t in tables:
        src_pq = (SRC / f"{t}.parquet").as_posix()
        dst_pq = DST / f"{t}.parquet"
        if dst_pq.exists() or dst_pq.is_symlink():
            dst_pq.unlink()
        if t in ENRICH:
            con.execute(f"COPY (SELECT *, {ENRICH[t]} FROM read_parquet('{src_pq}')) "
                        f"TO '{dst_pq.as_posix()}' (FORMAT PARQUET)")
            n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{dst_pq.as_posix()}')").fetchone()[0]
            print(f"  enriched {t}: {n:,} rows (+{ENRICH[t].count(' AS ')} complex cols)")
        else:
            dst_pq.symlink_to(src_pq)   # unchanged -> symlink (no duplication)
    print(f"retail_complex ready: {len(tables)} tables ({len(ENRICH)} enriched) -> {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
