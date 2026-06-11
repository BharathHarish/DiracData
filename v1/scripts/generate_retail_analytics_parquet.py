"""Create a retail analytics harness dataset from local source parquet files."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb


DEFAULT_SOURCE_DIR = Path("data/tpcds/parquet/sf1")
DEFAULT_OUTPUT_DIR = Path("data/retail_analytics/parquet/sf1")
DEFAULT_CATALOG_OUTPUT = Path("conf/catalogs/retail_analytics.minio.json")
DEFAULT_BUSINESS_CONTEXT_OUTPUT = Path("conf/business_contexts/retail_analytics.json")
DEFAULT_MANIFEST_OUTPUT = Path("data/retail_analytics/rename_manifest.json")
DEFAULT_CATALOG = "retail_pod"
DEFAULT_DATABASE = "analytics"
DEFAULT_SCHEMA = "retail_analytics"
DEFAULT_LAKE_BUCKET = "lake"
DEFAULT_LAKE_PREFIX = "retail_analytics/sf1"


TABLE_RENAMES = {
    "call_center": "support_centers",
    "catalog_page": "mailer_pages",
    "catalog_returns": "mail_order_refunds",
    "catalog_sales": "mail_order_purchases",
    "customer": "clients",
    "customer_address": "addresses",
    "customer_demographics": "client_profiles",
    "date_dim": "calendar_days",
    "household_demographics": "household_profiles",
    "income_band": "income_ranges",
    "inventory": "stock_levels",
    "item": "merchandise",
    "promotion": "marketing_campaigns",
    "reason": "return_reasons",
    "ship_mode": "delivery_methods",
    "store": "retail_locations",
    "store_returns": "store_refunds",
    "store_sales": "store_purchases",
    "time_dim": "clock_times",
    "warehouse": "fulfillment_centers",
    "web_page": "site_pages",
    "web_returns": "online_refunds",
    "web_sales": "online_purchases",
    "web_site": "online_properties",
}

TABLE_PREFIXES = {
    "call_center": "cc_",
    "catalog_page": "cp_",
    "catalog_returns": "cr_",
    "catalog_sales": "cs_",
    "customer": "c_",
    "customer_address": "ca_",
    "customer_demographics": "cd_",
    "date_dim": "d_",
    "household_demographics": "hd_",
    "income_band": "ib_",
    "inventory": "inv_",
    "item": "i_",
    "promotion": "p_",
    "reason": "r_",
    "ship_mode": "sm_",
    "store": "s_",
    "store_returns": "sr_",
    "store_sales": "ss_",
    "time_dim": "t_",
    "warehouse": "w_",
    "web_page": "wp_",
    "web_returns": "wr_",
    "web_sales": "ws_",
    "web_site": "web_",
}

PRIMARY_KEY_COLUMNS = {
    "cc_call_center_sk": "support_center_record",
    "cp_catalog_page_sk": "mailer_page_record",
    "c_customer_sk": "client_record",
    "ca_address_sk": "address_record",
    "cd_demo_sk": "client_profile_record",
    "d_date_sk": "calendar_day_record",
    "hd_demo_sk": "household_profile_record",
    "ib_income_band_sk": "income_range_record",
    "i_item_sk": "merchandise_record",
    "p_promo_sk": "campaign_record",
    "r_reason_sk": "return_reason_record",
    "sm_ship_mode_sk": "delivery_method_record",
    "s_store_sk": "retail_location_record",
    "t_time_sk": "clock_time_record",
    "w_warehouse_sk": "fulfillment_center_record",
    "wp_web_page_sk": "site_page_record",
    "web_site_sk": "online_property_record",
}

BUSINESS_CODE_COLUMNS = {
    "cc_call_center_id": "support_center_code",
    "cp_catalog_page_id": "mailer_page_code",
    "c_customer_id": "client_code",
    "ca_address_id": "address_code",
    "i_item_id": "merchandise_code",
    "p_promo_id": "campaign_code",
    "r_reason_id": "return_reason_code",
    "sm_ship_mode_id": "delivery_method_code",
    "s_store_id": "retail_location_code",
    "t_time_id": "clock_time_code",
    "w_warehouse_id": "fulfillment_center_code",
    "wp_web_page_id": "site_page_code",
    "web_site_id": "online_property_code",
    "d_date_id": "calendar_day_code",
}

STEM_ALIASES = {
    "addr": "address",
    "address": "address",
    "bill_addr": "billing_address",
    "bill_cdemo": "billing_client_profile",
    "bill_customer": "billing_client",
    "bill_hdemo": "billing_household_profile",
    "call_center": "support_center",
    "catalog": "mailer",
    "catalog_page": "mailer_page",
    "cdemo": "client_profile",
    "closed_date": "closure_calendar_day",
    "close_date": "closure_calendar_day",
    "creation_date": "creation_calendar_day",
    "customer": "client",
    "date": "calendar_day",
    "demo": "profile",
    "hdemo": "household_profile",
    "first_sales_date": "first_sale_calendar_day",
    "first_shipto_date": "first_shipping_calendar_day",
    "fy_quarter_seq": "fiscal_quarter_sequence",
    "fy_week_seq": "fiscal_week_sequence",
    "fy_year": "fiscal_year",
    "income_band": "income_range",
    "item_desc": "merchandise_description",
    "item": "merchandise",
    "last_review_date": "last_review_calendar_day",
    "mkt": "market",
    "open_date": "opening_calendar_day",
    "promo": "campaign",
    "reason": "return_reason",
    "rec_end_date": "record_effective_end",
    "rec_start_date": "record_effective_start",
    "refunded_addr": "refunded_address",
    "refunded_cdemo": "refunded_client_profile",
    "refunded_customer": "refunded_client",
    "refunded_hdemo": "refunded_household_profile",
    "return_time": "return_clock_time",
    "returned_date": "return_calendar_day",
    "returned_time": "return_clock_time",
    "returning_addr": "returning_address",
    "returning_cdemo": "returning_client_profile",
    "returning_customer": "returning_client",
    "returning_hdemo": "returning_household_profile",
    "same_day_lq": "same_day_last_quarter",
    "same_day_ly": "same_day_last_year",
    "ship_addr": "shipping_address",
    "ship_cdemo": "shipping_client_profile",
    "ship_customer": "shipping_client",
    "ship_date": "shipping_calendar_day",
    "ship_hdemo": "shipping_household_profile",
    "ship_mode": "delivery_method",
    "sold_date": "sale_calendar_day",
    "sold_time": "sale_clock_time",
    "store": "retail_location",
    "time": "clock_time",
    "warehouse": "fulfillment_center",
    "web_page": "site_page",
    "web_site": "online_property",
}

TOKEN_ALIASES = {
    "addr": "address",
    "amt": "amount",
    "autogen": "auto_generated",
    "cdemo": "client_profile",
    "catalog": "mailer",
    "company": "company",
    "cp": "mailer_page",
    "cs": "mail_order_purchase",
    "cust": "client",
    "customer": "client",
    "demo": "profile",
    "dep": "dependent",
    "desc": "description",
    "dim": "reference",
    "dmail": "direct_mail",
    "dom": "day_of_month",
    "dow": "day_of_week",
    "ext": "extended",
    "fy": "fiscal_year",
    "gmt": "timezone",
    "hdemo": "household_profile",
    "inc": "with",
    "inv": "stock",
    "item": "merchandise",
    "ly": "last_year",
    "lq": "last_quarter",
    "manufact": "manufacturer",
    "mkt": "market",
    "moy": "month_of_year",
    "promo": "campaign",
    "qoy": "quarter_of_year",
    "rec": "record",
    "seq": "sequence",
    "ship": "delivery",
    "sk": "reference",
    "sq": "square",
    "sr": "store_refund",
    "ss": "store_purchase",
    "wholesale": "wholesale",
    "wp": "site_page",
    "wr": "online_refund",
    "ws": "online_purchase",
}

TABLE_DESCRIPTIONS = {
    "support_centers": "Customer service and support center locations used to analyze assisted commerce operations.",
    "mailer_pages": "Catalog or mailer page records used to analyze print-assisted shopping journeys.",
    "mail_order_refunds": "Returned purchases from the mail-order channel, including refund amounts, return reasons, and client roles.",
    "mail_order_purchases": "Purchases made through the mail-order channel, including pricing, discounts, shipping, and profitability.",
    "clients": "Shopper account records connecting people to addresses, demographics, purchases, returns, and tenure.",
    "addresses": "Client mailing and residential locations used for city, state, county, ZIP, and country analysis.",
    "client_profiles": "Client demographic segments such as gender, marital status, education, credit rating, and dependents.",
    "calendar_days": "Calendar reference records for daily, weekly, monthly, quarterly, yearly, and fiscal reporting.",
    "household_profiles": "Household-level segments including income range, buying potential, dependents, and vehicle ownership.",
    "income_ranges": "Income range bands used to classify households into economic tiers.",
    "stock_levels": "Product stock counts by merchandise item, fulfillment center, and calendar day.",
    "merchandise": "Product catalog records describing items sold by the business, including brand, category, color, size, and pricing.",
    "marketing_campaigns": "Campaign and offer records used to analyze promoted sales across channels.",
    "return_reasons": "Reference records describing why an item was returned.",
    "delivery_methods": "Shipping and delivery method reference records, including carrier, service type, and contract.",
    "retail_locations": "Physical store locations and operating attributes for retail-channel analysis.",
    "store_refunds": "Returned purchases from physical stores, including refund amounts, reasons, and client details.",
    "store_purchases": "Item-level purchases made in physical stores, including pricing, discounts, tax, and profit.",
    "clock_times": "Time-of-day reference records used to analyze activity by hour, minute, second, shift, and meal period.",
    "fulfillment_centers": "Fulfillment center locations used for stock storage and order shipment.",
    "site_pages": "Online page records used to analyze website pages, content volume, and access timing.",
    "online_refunds": "Returned purchases from the online channel, including refund amounts, return reasons, and client roles.",
    "online_purchases": "Online purchases, including pricing, discounts, shipping, and profitability details.",
    "online_properties": "Website or digital commerce property records used for online-channel analysis.",
}


@dataclass(frozen=True)
class TableRenamePlan:
    source_table: str
    target_table: str
    source_path: Path
    target_path: Path
    columns: dict[str, str]


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--catalog-output", type=Path, default=DEFAULT_CATALOG_OUTPUT)
    parser.add_argument(
        "--business-context-output",
        type=Path,
        default=DEFAULT_BUSINESS_CONTEXT_OUTPUT,
    )
    parser.add_argument("--manifest-output", type=Path, default=DEFAULT_MANIFEST_OUTPUT)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--lake-bucket", default=DEFAULT_LAKE_BUCKET)
    parser.add_argument("--lake-prefix", default=DEFAULT_LAKE_PREFIX)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plans = build_rename_plans(args.source_dir, args.output_dir)
    write_retail_parquet(plans, force=args.force)
    write_catalog(
        plans=plans,
        output_path=args.catalog_output,
        catalog=args.catalog,
        database=args.database,
        schema=args.schema,
        lake_bucket=args.lake_bucket,
        lake_prefix=args.lake_prefix,
    )
    write_business_context(args.business_context_output)
    write_manifest(
        plans=plans,
        output_path=args.manifest_output,
        catalog=args.catalog,
        database=args.database,
        schema=args.schema,
    )
    print(f"Generated {len(plans)} retail analytics parquet tables in {args.output_dir}")
    print(f"Catalog: {args.catalog_output}")
    print(f"Business context: {args.business_context_output}")
    print(f"Rename manifest: {args.manifest_output}")


def build_rename_plans(source_dir: Path, output_dir: Path) -> list[TableRenamePlan]:
    parquet_files = {path.stem: path for path in sorted(source_dir.glob("*.parquet"))}
    missing = sorted(set(TABLE_RENAMES) - set(parquet_files))
    unexpected = sorted(set(parquet_files) - set(TABLE_RENAMES))
    if missing or unexpected:
        raise ValueError(
            "Source table set does not match the retail analytics rename map: "
            f"missing={missing}, unexpected={unexpected}"
        )

    con = duckdb.connect(":memory:")
    try:
        plans = []
        for source_table in sorted(TABLE_RENAMES):
            source_path = parquet_files[source_table]
            source_columns = describe_parquet_columns(con, source_path)
            target_table = TABLE_RENAMES[source_table]
            target_columns = {
                column: rename_column(source_table, column)
                for column in source_columns
            }
            validate_column_renames(source_table, target_columns)
            plans.append(
                TableRenamePlan(
                    source_table=source_table,
                    target_table=target_table,
                    source_path=source_path,
                    target_path=output_dir / f"{target_table}.parquet",
                    columns=target_columns,
                )
            )
        return plans
    finally:
        con.close()


def describe_parquet_columns(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet({sql_string(path)})"
    ).fetchall()
    return [str(row[0]) for row in rows]


def rename_column(table_name: str, column_name: str) -> str:
    if column_name in PRIMARY_KEY_COLUMNS:
        return PRIMARY_KEY_COLUMNS[column_name]
    if column_name in BUSINESS_CODE_COLUMNS:
        return BUSINESS_CODE_COLUMNS[column_name]

    stem = strip_table_prefix(table_name, column_name)
    if stem.endswith("_sk"):
        return f"{semantic_stem(stem.removesuffix('_sk'))}_ref"
    if stem.endswith("_id"):
        return f"{semantic_stem(stem.removesuffix('_id'))}_code"
    return semantic_stem(stem)


def strip_table_prefix(table_name: str, column_name: str) -> str:
    prefix = TABLE_PREFIXES[table_name]
    if not column_name.startswith(prefix):
        return column_name
    return column_name.removeprefix(prefix)


def semantic_stem(stem: str) -> str:
    if stem in STEM_ALIASES:
        return STEM_ALIASES[stem]

    tokens = stem.split("_")
    output: list[str] = []
    index = 0
    while index < len(tokens):
        for size in (3, 2):
            phrase = "_".join(tokens[index : index + size])
            if phrase in STEM_ALIASES:
                output.extend(STEM_ALIASES[phrase].split("_"))
                index += size
                break
        else:
            output.extend(TOKEN_ALIASES.get(tokens[index], tokens[index]).split("_"))
            index += 1

    return "_".join(output)


def validate_column_renames(table_name: str, columns: dict[str, str]) -> None:
    targets = list(columns.values())
    duplicates = sorted({column for column in targets if targets.count(column) > 1})
    if duplicates:
        raise ValueError(f"Duplicate renamed columns for {table_name}: {duplicates}")

    leaked_suffixes = [
        column
        for column in targets
        if column.endswith("_sk") or column.endswith("_pk") or column.endswith("_id")
    ]
    if leaked_suffixes:
        raise ValueError(f"Technical key suffixes leaked for {table_name}: {leaked_suffixes}")


def write_retail_parquet(plans: list[TableRenamePlan], *, force: bool) -> None:
    con = duckdb.connect(":memory:")
    try:
        for plan in plans:
            if plan.target_path.exists() and not force:
                print(f"Skipping existing {plan.target_path}")
                continue

            plan.target_path.parent.mkdir(parents=True, exist_ok=True)
            select_list = ", ".join(
                f"{quote_identifier(source)} AS {quote_identifier(target)}"
                for source, target in plan.columns.items()
            )
            sql = (
                f"COPY (SELECT {select_list} "
                f"FROM read_parquet({sql_string(plan.source_path)})) "
                f"TO {sql_string(plan.target_path)} "
                "(FORMAT parquet, COMPRESSION zstd)"
            )
            print(f"Writing {plan.target_path}")
            con.execute(sql)
    finally:
        con.close()


def write_catalog(
    *,
    plans: list[TableRenamePlan],
    output_path: Path,
    catalog: str,
    database: str,
    schema: str,
    lake_bucket: str,
    lake_prefix: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    clean_prefix = lake_prefix.strip("/")
    payload = {
        "catalog": catalog,
        "database": database,
        "schema": schema,
        "tables": [
            {
                "name": plan.target_table,
                "path": f"s3://{lake_bucket}/{clean_prefix}/{plan.target_table}.parquet",
                "format": "parquet",
                "description": TABLE_DESCRIPTIONS[plan.target_table],
            }
            for plan in sorted(plans, key=lambda item: item.target_table)
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_business_context(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "text": (
            "Retail analytics schema for a commerce business. The schema studies clients, "
            "addresses, purchases, refunds, merchandise, marketing campaigns, stock "
            "levels, stores, fulfillment centers, website activity, mail-order activity, "
            "delivery methods, calendar dates, time of day, client profiles, household "
            "profiles, and income ranges. This is harness context for validating schema "
            "learning on a retail analytics schema."
        ),
        "table_descriptions": TABLE_DESCRIPTIONS,
        "column_descriptions": {
            "clients": {
                "client_code": "Business-facing client identifier.",
                "current_client_profile_ref": "Links a client to the active demographic profile.",
                "current_household_profile_ref": "Links a client to the active household profile.",
                "current_address_ref": "Links a client to the active address.",
            },
            "addresses": {
                "state": "US state abbreviation for the client address.",
                "city": "City for the client address.",
                "zip": "Postal ZIP code for the client address.",
            },
            "client_profiles": {
                "gender": "Client gender segment.",
                "marital_status": "Client marital status segment.",
                "education_status": "Client education segment.",
            },
            "store_purchases": {
                "client_ref": "Client connected to the store purchase.",
                "merchandise_ref": "Merchandise item sold in the store purchase.",
                "sale_calendar_day_ref": "Calendar day when the store purchase occurred.",
                "net_paid": "Net amount paid by the shopper for the store purchase.",
            },
            "online_purchases": {
                "billing_client_ref": "Client billed for the online purchase.",
                "merchandise_ref": "Merchandise item sold through the online purchase.",
                "sale_calendar_day_ref": "Calendar day when the online purchase occurred.",
                "net_paid": "Net amount paid by the shopper for the online purchase.",
            },
            "calendar_days": {
                "year": "Calendar year.",
                "month_of_year": "Month number within the year.",
                "calendar_day": "Actual calendar date.",
            },
            "merchandise": {
                "category": "Product category.",
                "class": "Product class or grouping.",
                "brand": "Product brand.",
                "product_name": "Product display name.",
            },
        },
        "glossary": {
            "client": "A person or account buying from the business.",
            "merchandise": "A product sold by the business.",
            "campaign": "A marketing offer, discount, or sales activation.",
            "refund": "A returned purchase or returned item.",
            "mail-order": "Commerce through catalog or mailer channels.",
            "online": "Commerce through website or digital channels.",
            "retail location": "A physical store.",
            "stock level": "Available product quantity at a fulfillment center.",
            "profile": "A demographic segment record used to classify clients or households.",
        },
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_manifest(
    *,
    plans: list[TableRenamePlan],
    output_path: Path,
    catalog: str,
    database: str,
    schema: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "catalog": catalog,
        "database": database,
        "schema": schema,
        "tables": {
            plan.source_table: {
                "renamed_to": plan.target_table,
                "columns": plan.columns,
            }
            for plan in sorted(plans, key=lambda item: item.source_table)
        },
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
