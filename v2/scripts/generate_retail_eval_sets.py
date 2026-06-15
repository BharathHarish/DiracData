#!/usr/bin/env python3
"""Generate retail NL-SQL gold and benchmark datasets.

The generated files are intentionally retail-specific eval artifacts. The
generator uses DuckDB to pull valid categorical values from the local parquet
tables, then emits explicit NL, SQL, tables, columns, and join edges.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.semantic_catalog.sql_analysis import analyze_sql_references  # noqa: E402


@dataclass(frozen=True)
class Template:
    key: str
    category: str
    difficulty: str
    builder: Callable[[dict[str, Any]], tuple[str, str, str]]
    param_grid: dict[str, list[Any]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=str(V2_ROOT / "data" / "retail_analytics" / "parquet" / "sf1"))
    parser.add_argument("--metadata-descriptions-path", default=str(V2_ROOT / "context" / "retail_analytics_metadata_descriptions.json"))
    parser.add_argument("--output-dir", default=str(V2_ROOT / "evals"))
    parser.add_argument("--gold-count", type=int, default=120)
    parser.add_argument("--benchmark-count", type=int, default=300)
    args = parser.parse_args()

    metadata = json.loads(Path(args.metadata_descriptions_path).read_text(encoding="utf-8"))
    table_columns = {
        str(table): sorted(map(str, columns.keys()))
        for table, columns in metadata.get("columns", {}).items()
    }
    values = _load_values(Path(args.data_root))
    templates = _templates(values)
    gold_variants = max(1, args.gold_count // max(1, len(templates)))
    benchmark_variants = max(1, args.benchmark_count // max(1, len(templates)))
    gold_rows = _generate_rows(
        templates=templates,
        table_columns=table_columns,
        variants_per_template=gold_variants,
    )[: args.gold_count]
    benchmark_rows = _generate_rows(
        templates=templates,
        table_columns=table_columns,
        variants_per_template=benchmark_variants,
    )[: args.benchmark_count]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    gold_path = output_dir / "Goldset_retail_queries.csv"
    benchmark_path = output_dir / "Benchmark_retail_customer_history.csv"
    _write_csv(gold_path, gold_rows, id_field="case_id", prefix="gold")
    _write_csv(benchmark_path, benchmark_rows, id_field="history_id", prefix="history")

    print(
        json.dumps(
            {
                "status": "ok",
                "gold_path": str(gold_path),
                "benchmark_path": str(benchmark_path),
                "gold_rows": len(gold_rows),
                "benchmark_rows": len(benchmark_rows),
                "gold_coverage": _coverage(gold_rows),
                "benchmark_coverage": _coverage(benchmark_rows),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _load_values(data_root: Path) -> dict[str, list[Any]]:
    import duckdb

    con = duckdb.connect(":memory:")
    for path in sorted(data_root.glob("*.parquet")):
        con.execute(f"CREATE VIEW {path.stem} AS SELECT * FROM read_parquet('{path.as_posix()}')")

    specs = {
        "address_states": ("addresses", "state", 12),
        "address_cities": ("addresses", "city", 10),
        "address_location_types": ("addresses", "location_type", 6),
        "client_genders": ("client_profiles", "gender", 4),
        "marital_statuses": ("client_profiles", "marital_status", 5),
        "education_statuses": ("client_profiles", "education_status", 6),
        "credit_ratings": ("client_profiles", "credit_rating", 6),
        "birth_countries": ("clients", "birth_country", 8),
        "preferred_flags": ("clients", "preferred_client_flag", 2),
        "calendar_years": ("calendar_days", "year", 8),
        "calendar_months": ("calendar_days", "month_of_year", 12),
        "day_names": ("calendar_days", "day_name", 7),
        "weekend_flags": ("calendar_days", "weekend", 2),
        "clock_shifts": ("clock_times", "shift", 8),
        "clock_sub_shifts": ("clock_times", "sub_shift", 10),
        "meal_times": ("clock_times", "meal_clock_time", 4),
        "delivery_carriers": ("delivery_methods", "carrier", 8),
        "delivery_types": ("delivery_methods", "type", 6),
        "warehouse_names": ("fulfillment_centers", "warehouse_name", 5),
        "warehouse_states": ("fulfillment_centers", "state", 5),
        "buy_potentials": ("household_profiles", "buy_potential", 6),
        "campaign_purposes": ("marketing_campaigns", "purpose", 8),
        "campaign_names": ("marketing_campaigns", "campaign_name", 8),
        "campaign_discount_flags": ("marketing_campaigns", "discount_active", 2),
        "merch_categories": ("merchandise", "category", 12),
        "merch_classes": ("merchandise", "class", 12),
        "merch_brands": ("merchandise", "brand", 12),
        "merch_manufacturers": ("merchandise", "manufacturer", 12),
        "merch_colors": ("merchandise", "color", 10),
        "merch_sizes": ("merchandise", "size", 10),
        "online_property_names": ("online_properties", "name", 8),
        "online_property_classes": ("online_properties", "class", 6),
        "online_market_classes": ("online_properties", "market_class", 6),
        "online_property_states": ("online_properties", "state", 8),
        "retail_store_names": ("retail_locations", "store_name", 8),
        "retail_states": ("retail_locations", "state", 8),
        "retail_geography_classes": ("retail_locations", "geography_class", 6),
        "return_reasons": ("return_reasons", "reason_description", 10),
        "site_page_types": ("site_pages", "type", 8),
        "site_auto_flags": ("site_pages", "auto_generated_flag", 2),
        "mailer_departments": ("mailer_pages", "department", 8),
        "mailer_types": ("mailer_pages", "type", 8),
        "support_center_names": ("support_centers", "name", 6),
        "support_center_classes": ("support_centers", "class", 6),
    }
    output: dict[str, list[Any]] = {}
    for key, (table, column, limit) in specs.items():
        rows = con.execute(
            f"""
            SELECT {column}
            FROM {table}
            WHERE {column} IS NOT NULL
            GROUP BY {column}
            ORDER BY COUNT(*) DESC, {column}
            LIMIT {int(limit)}
            """
        ).fetchall()
        values = [row[0] for row in rows if row[0] is not None]
        output[key] = values or ["UNKNOWN"]
    output["years"] = [2001, 2002, 2003, 2004]
    output["months"] = list(range(1, 13))
    return output


def _templates(v: dict[str, list[Any]]) -> list[Template]:
    return [
        Template("current_customer_profile", "customer", "easy", _current_customer_profile, _grid(v, state="address_states", gender="client_genders", year="years")),
        Template("current_customer_lifecycle", "customer", "medium", _current_customer_lifecycle, _grid(v, country="birth_countries", flag="preferred_flags", year="years")),
        Template("customer_income_profile", "customer", "medium", _customer_income_profile, _grid(v, education="education_statuses", rating="credit_ratings", potential="buy_potentials")),
        Template("online_customer_slice", "online_sales", "medium", _online_customer_slice, _grid(v, state="address_states", gender="client_genders", category="merch_categories", year="years")),
        Template("online_income_slice", "online_sales", "hard", _online_income_slice, _grid(v, state="address_states", category="merch_categories", year="years")),
        Template("online_billing_shipping", "online_sales", "hard", _online_billing_shipping, _grid(v, category="merch_categories", year="years")),
        Template("online_shipping_customer", "online_sales", "hard", _online_shipping_customer, _grid(v, state="address_states", year="years")),
        Template("online_property_page", "web_site", "medium", _online_property_page, _grid(v, property_name="online_property_names", page_type="site_page_types", year="years")),
        Template("online_property_market", "web_site", "medium", _online_property_market, _grid(v, market_class="online_market_classes", state="online_property_states", year="years")),
        Template("site_page_content", "web_site", "easy", _site_page_content, _grid(v, page_type="site_page_types", auto_flag="site_auto_flags")),
        Template("online_campaign_sales", "marketing", "medium", _online_campaign_sales, _grid(v, purpose="campaign_purposes", category="merch_categories", year="years")),
        Template("campaign_channel_mix", "marketing", "easy", _campaign_channel_mix, _grid(v, purpose="campaign_purposes", discount="campaign_discount_flags")),
        Template("campaign_duration", "marketing", "medium", _campaign_duration, _grid(v, purpose="campaign_purposes", year="years")),
        Template("store_sales_location", "store_sales", "medium", _store_sales_location, _grid(v, store_state="retail_states", category="merch_categories", year="years", month="months")),
        Template("store_customer_demographics", "store_sales", "hard", _store_customer_demographics, _grid(v, gender="client_genders", category="merch_categories", year="years")),
        Template("store_campaign_income", "store_sales", "hard", _store_campaign_income, _grid(v, purpose="campaign_purposes", year="years")),
        Template("store_clock_sales", "store_sales", "medium", _store_clock_sales, _grid(v, shift="clock_shifts", store_state="retail_states", year="years")),
        Template("store_refunds_reason", "returns", "medium", _store_refunds_reason, _grid(v, reason="return_reasons", category="merch_categories", year="years")),
        Template("store_refunds_profile", "returns", "hard", _store_refunds_profile, _grid(v, gender="client_genders", reason="return_reasons", year="years")),
        Template("online_refunds_reason", "returns", "medium", _online_refunds_reason, _grid(v, reason="return_reasons", category="merch_categories", year="years")),
        Template("online_refunds_site", "returns", "hard", _online_refunds_site, _grid(v, page_type="site_page_types", reason="return_reasons", year="years")),
        Template("mail_order_support", "mail_order", "medium", _mail_order_support, _grid(v, carrier="delivery_carriers", center="support_center_names", year="years")),
        Template("mail_order_warehouse", "mail_order", "medium", _mail_order_warehouse, _grid(v, warehouse="warehouse_names", carrier="delivery_carriers", year="years")),
        Template("mail_order_shipping", "mail_order", "hard", _mail_order_shipping, _grid(v, state="address_states", carrier="delivery_carriers", year="years")),
        Template("mail_order_mailer", "mailer", "medium", _mail_order_mailer, _grid(v, department="mailer_departments", mailer_type="mailer_types", year="years")),
        Template("mailer_page_calendar", "mailer", "easy", _mailer_page_calendar, _grid(v, department="mailer_departments", mailer_type="mailer_types", year="years")),
        Template("mail_refunds_ops", "returns", "hard", _mail_refunds_ops, _grid(v, reason="return_reasons", carrier="delivery_carriers", year="years")),
        Template("inventory_stock", "inventory", "medium", _inventory_stock, _grid(v, warehouse="warehouse_names", category="merch_categories", year="years", month="months")),
        Template("inventory_demand", "inventory", "hard", _inventory_demand, _grid(v, warehouse="warehouse_names", category="merch_categories", year="years")),
        Template("product_catalog", "product", "easy", _product_catalog, _grid(v, brand="merch_brands", color="merch_colors", size="merch_sizes")),
    ]


def _grid(values: dict[str, list[Any]], **keys: str) -> dict[str, list[Any]]:
    return {name: values[source] for name, source in keys.items()}


def _generate_rows(
    *,
    templates: list[Template],
    table_columns: dict[str, list[str]],
    variants_per_template: int,
) -> list[dict[str, Any]]:
    rows = []
    for template in templates:
        names = list(template.param_grid)
        combinations = list(itertools.product(*(template.param_grid[name] for name in names)))
        if not combinations:
            continue
        for variant_index in range(1, variants_per_template + 1):
            combo = combinations[(variant_index - 1) % len(combinations)]
            params = dict(zip(names, combo, strict=True))
            question, sql, notes = template.builder(params)
            analysis = analyze_sql_references(sql, table_columns)
            rows.append(
                {
                    "category": template.category,
                    "difficulty": template.difficulty,
                    "nl_query": question,
                    "sql": sql,
                    "tables_used": ";".join(analysis.tables),
                    "columns_used": ";".join(analysis.columns),
                    "join_edges": ";".join(pair.sql_condition for pair in analysis.join_pairs),
                    "expected_ambiguities": "",
                    "source_template": template.key,
                    "template_variant": variant_index,
                    "notes": notes,
                }
            )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], *, id_field: str, prefix: str) -> None:
    fieldnames = [
        id_field,
        "category",
        "difficulty",
        "nl_query",
        "sql",
        "tables_used",
        "columns_used",
        "join_edges",
        "expected_ambiguities",
        "source_template",
        "template_variant",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            writer.writerow({id_field: f"{prefix}_{index:03d}", **row})


def _coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tables = set()
    columns = set()
    joins = set()
    templates = set()
    for row in rows:
        tables.update(filter(None, str(row["tables_used"]).split(";")))
        columns.update(filter(None, str(row["columns_used"]).split(";")))
        joins.update(filter(None, str(row["join_edges"]).split(";")))
        templates.add(row["source_template"])
    return {
        "tables": len(tables),
        "columns": len(columns),
        "join_edges": len(joins),
        "templates": len(templates),
        "table_names": sorted(tables),
    }


def _q(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return "'" + str(value).replace("'", "''") + "'"


def _current_customer_profile(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Count current {p['gender']} customers from {p['state']} whose first sale year is {p['year']}.",
        f"""
        SELECT cp.gender, a.state, cd.year, COUNT(DISTINCT c.client_record) AS customers
        FROM clients c
        JOIN client_profiles cp ON c.current_client_profile_ref = cp.client_profile_record
        JOIN addresses a ON c.current_address_ref = a.address_record
        JOIN calendar_days cd ON c.first_sale_calendar_day_ref = cd.calendar_day_record
        WHERE cp.gender = {_q(p['gender'])} AND a.state = {_q(p['state'])} AND cd.year = {int(p['year'])}
        GROUP BY cp.gender, a.state, cd.year
        """,
        "Current customer profile and geography.",
    )


def _current_customer_lifecycle(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Show preferred-customer counts by first sale year for customers born in {p['country']} with preferred flag {p['flag']}.",
        f"""
        SELECT c.birth_country, c.preferred_client_flag, cd.year, COUNT(DISTINCT c.client_record) AS customers
        FROM clients c
        JOIN calendar_days cd ON c.first_sale_calendar_day_ref = cd.calendar_day_record
        WHERE c.birth_country = {_q(p['country'])} AND c.preferred_client_flag = {_q(p['flag'])}
        GROUP BY c.birth_country, c.preferred_client_flag, cd.year
        ORDER BY cd.year
        """,
        "Customer lifecycle and preferred flag.",
    )


def _customer_income_profile(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Profile current customers with {p['education']} education and {p['rating']} credit rating by income band and buy potential {p['potential']}.",
        f"""
        SELECT cp.education_status, cp.credit_rating, hp.buy_potential, ir.lower_bound, ir.upper_bound, COUNT(DISTINCT c.client_record) AS customers
        FROM clients c
        JOIN client_profiles cp ON c.current_client_profile_ref = cp.client_profile_record
        JOIN household_profiles hp ON c.current_household_profile_ref = hp.household_profile_record
        JOIN income_ranges ir ON hp.income_range_ref = ir.income_range_record
        WHERE cp.education_status = {_q(p['education'])} AND cp.credit_rating = {_q(p['rating'])} AND hp.buy_potential = {_q(p['potential'])}
        GROUP BY cp.education_status, cp.credit_rating, hp.buy_potential, ir.lower_bound, ir.upper_bound
        """,
        "Current profile plus household income.",
    )


def _online_customer_slice(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"How many {p['gender']} online shoppers from {p['state']} bought {p['category']} in {p['year']}?",
        f"""
        SELECT a.state, cp.gender, m.category, cd.year, COUNT(DISTINCT op.billing_client_ref) AS customers
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        JOIN merchandise m ON op.merchandise_ref = m.merchandise_record
        JOIN addresses a ON op.billing_address_ref = a.address_record
        JOIN client_profiles cp ON op.billing_client_profile_ref = cp.client_profile_record
        WHERE a.state = {_q(p['state'])} AND cp.gender = {_q(p['gender'])} AND m.category = {_q(p['category'])} AND cd.year = {int(p['year'])}
        GROUP BY a.state, cp.gender, m.category, cd.year
        """,
        "Transaction-time online customer geography and profile.",
    )


def _online_income_slice(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Online {p['category']} customers from {p['state']} in {p['year']} by household income band.",
        f"""
        SELECT a.state, m.category, ir.lower_bound, ir.upper_bound, COUNT(DISTINCT op.billing_client_ref) AS customers
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        JOIN merchandise m ON op.merchandise_ref = m.merchandise_record
        JOIN addresses a ON op.billing_address_ref = a.address_record
        JOIN household_profiles hp ON op.billing_household_profile_ref = hp.household_profile_record
        JOIN income_ranges ir ON hp.income_range_ref = ir.income_range_record
        WHERE a.state = {_q(p['state'])} AND m.category = {_q(p['category'])} AND cd.year = {int(p['year'])}
        GROUP BY a.state, m.category, ir.lower_bound, ir.upper_bound
        """,
        "Online transaction-time household profile.",
    )


def _online_billing_shipping(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Compare billing and shipping states for online {p['category']} purchases in {p['year']}.",
        f"""
        SELECT ba.state AS billing_state, sa.state AS shipping_state, COUNT(*) AS purchase_rows, SUM(op.net_paid) AS net_paid
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        JOIN merchandise m ON op.merchandise_ref = m.merchandise_record
        JOIN addresses ba ON op.billing_address_ref = ba.address_record
        JOIN addresses sa ON op.shipping_address_ref = sa.address_record
        WHERE m.category = {_q(p['category'])} AND cd.year = {int(p['year'])}
        GROUP BY ba.state, sa.state
        """,
        "Same dimension table joined twice with billing and shipping roles.",
    )


def _online_shipping_customer(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"How many online purchases in {p['year']} shipped to a different customer than billed for billing state {p['state']}?",
        f"""
        SELECT a.state, cd.year, COUNT(*) AS purchase_rows
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        JOIN addresses a ON op.billing_address_ref = a.address_record
        JOIN clients bill_c ON op.billing_client_ref = bill_c.client_record
        JOIN clients ship_c ON op.shipping_client_ref = ship_c.client_record
        WHERE cd.year = {int(p['year'])} AND a.state = {_q(p['state'])} AND bill_c.client_record <> ship_c.client_record
        GROUP BY a.state, cd.year
        """,
        "Same client table joined for billing and shipping roles.",
    )


def _online_property_page(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Online net paid in {p['year']} for property {p['property_name']} by site page type {p['page_type']}.",
        f"""
        SELECT web.name, sp.type, cd.year, SUM(op.net_paid) AS net_paid, COUNT(DISTINCT op.billing_client_ref) AS customers
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        JOIN online_properties web ON op.online_property_ref = web.online_property_record
        JOIN site_pages sp ON op.site_page_ref = sp.site_page_record
        WHERE web.name = {_q(p['property_name'])} AND sp.type = {_q(p['page_type'])} AND cd.year = {int(p['year'])}
        GROUP BY web.name, sp.type, cd.year
        """,
        "Web site and page performance.",
    )


def _online_property_market(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Online property performance for market class {p['market_class']} in property state {p['state']} during {p['year']}.",
        f"""
        SELECT web.market_class, web.state, cd.year, SUM(op.net_profit) AS net_profit, COUNT(*) AS purchase_rows
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        JOIN online_properties web ON op.online_property_ref = web.online_property_record
        WHERE web.market_class = {_q(p['market_class'])} AND web.state = {_q(p['state'])} AND cd.year = {int(p['year'])}
        GROUP BY web.market_class, web.state, cd.year
        """,
        "Online property geography, not customer geography.",
    )


def _site_page_content(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Summarize site pages of type {p['page_type']} with auto-generated flag {p['auto_flag']} by content counts.",
        f"""
        SELECT sp.type, sp.auto_generated_flag, AVG(sp.char_count) AS avg_chars, AVG(sp.link_count) AS avg_links, AVG(sp.image_count) AS avg_images, AVG(sp.max_ad_count) AS avg_ads
        FROM site_pages sp
        WHERE sp.type = {_q(p['page_type'])} AND sp.auto_generated_flag = {_q(p['auto_flag'])}
        GROUP BY sp.type, sp.auto_generated_flag
        """,
        "Site page dimension content fields.",
    )


def _online_campaign_sales(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Which {p['purpose']} campaigns drove online {p['category']} net paid sales in {p['year']}?",
        f"""
        SELECT mc.purpose, mc.campaign_name, m.category, cd.year, SUM(op.net_paid) AS net_paid
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        JOIN marketing_campaigns mc ON op.campaign_ref = mc.campaign_record
        JOIN merchandise m ON op.merchandise_ref = m.merchandise_record
        WHERE mc.purpose = {_q(p['purpose'])} AND m.category = {_q(p['category'])} AND cd.year = {int(p['year'])}
        GROUP BY mc.purpose, mc.campaign_name, m.category, cd.year
        """,
        "Online campaign attribution.",
    )


def _campaign_channel_mix(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Show marketing campaign channel mix for {p['purpose']} campaigns with discount flag {p['discount']}.",
        f"""
        SELECT mc.purpose, mc.discount_active, COUNT(*) AS campaigns, SUM(mc.cost) AS total_cost, SUM(mc.response_target) AS response_target,
               SUM(CASE WHEN mc.channel_email = 'Y' THEN 1 ELSE 0 END) AS email_campaigns,
               SUM(CASE WHEN mc.channel_tv = 'Y' THEN 1 ELSE 0 END) AS tv_campaigns,
               SUM(CASE WHEN mc.channel_radio = 'Y' THEN 1 ELSE 0 END) AS radio_campaigns
        FROM marketing_campaigns mc
        WHERE mc.purpose = {_q(p['purpose'])} AND mc.discount_active = {_q(p['discount'])}
        GROUP BY mc.purpose, mc.discount_active
        """,
        "Campaign dimension channel flags.",
    )


def _campaign_duration(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Campaign duration and target response for {p['purpose']} campaigns starting in {p['year']}.",
        f"""
        SELECT mc.purpose, start_day.year, AVG(end_day.calendar_day_record - start_day.calendar_day_record) AS avg_duration_days, SUM(mc.response_target) AS response_target
        FROM marketing_campaigns mc
        JOIN calendar_days start_day ON mc.start_calendar_day_ref = start_day.calendar_day_record
        JOIN calendar_days end_day ON mc.end_calendar_day_ref = end_day.calendar_day_record
        WHERE mc.purpose = {_q(p['purpose'])} AND start_day.year = {int(p['year'])}
        GROUP BY mc.purpose, start_day.year
        """,
        "Campaign start and end date roles.",
    )


def _store_sales_location(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Store net sales in {p['store_state']} for {p['category']} during month {p['month']} of {p['year']}.",
        f"""
        SELECT rl.state, m.category, cd.year, cd.month_of_year, SUM(sp.net_paid) AS net_paid
        FROM store_purchases sp
        JOIN calendar_days cd ON sp.sale_calendar_day_ref = cd.calendar_day_record
        JOIN retail_locations rl ON sp.retail_location_ref = rl.retail_location_record
        JOIN merchandise m ON sp.merchandise_ref = m.merchandise_record
        WHERE rl.state = {_q(p['store_state'])} AND m.category = {_q(p['category'])} AND cd.year = {int(p['year'])} AND cd.month_of_year = {int(p['month'])}
        GROUP BY rl.state, m.category, cd.year, cd.month_of_year
        """,
        "Store geography and product.",
    )


def _store_customer_demographics(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Store {p['category']} customers in {p['year']} by {p['gender']} purchase-time profile and income band.",
        f"""
        SELECT cp.gender, ir.lower_bound, ir.upper_bound, m.category, cd.year, COUNT(DISTINCT sp.client_ref) AS customers
        FROM store_purchases sp
        JOIN calendar_days cd ON sp.sale_calendar_day_ref = cd.calendar_day_record
        JOIN client_profiles cp ON sp.client_profile_ref = cp.client_profile_record
        JOIN household_profiles hp ON sp.household_profile_ref = hp.household_profile_record
        JOIN income_ranges ir ON hp.income_range_ref = ir.income_range_record
        JOIN merchandise m ON sp.merchandise_ref = m.merchandise_record
        WHERE cp.gender = {_q(p['gender'])} AND m.category = {_q(p['category'])} AND cd.year = {int(p['year'])}
        GROUP BY cp.gender, ir.lower_bound, ir.upper_bound, m.category, cd.year
        """,
        "Store transaction-time demographics.",
    )


def _store_campaign_income(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Store campaign sales for {p['purpose']} campaigns by household income band in {p['year']}.",
        f"""
        SELECT mc.purpose, ir.lower_bound, ir.upper_bound, cd.year, SUM(sp.net_paid) AS net_paid, COUNT(DISTINCT sp.client_ref) AS customers
        FROM store_purchases sp
        JOIN calendar_days cd ON sp.sale_calendar_day_ref = cd.calendar_day_record
        JOIN marketing_campaigns mc ON sp.campaign_ref = mc.campaign_record
        JOIN household_profiles hp ON sp.household_profile_ref = hp.household_profile_record
        JOIN income_ranges ir ON hp.income_range_ref = ir.income_range_record
        WHERE mc.purpose = {_q(p['purpose'])} AND cd.year = {int(p['year'])}
        GROUP BY mc.purpose, ir.lower_bound, ir.upper_bound, cd.year
        """,
        "Store campaign and household income.",
    )


def _store_clock_sales(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Store sales during {p['shift']} shift in {p['store_state']} stores for {p['year']}.",
        f"""
        SELECT ct.shift, rl.state, cd.year, SUM(sp.net_paid) AS net_paid, COUNT(*) AS rows
        FROM store_purchases sp
        JOIN clock_times ct ON sp.sale_clock_time_ref = ct.clock_time_record
        JOIN calendar_days cd ON sp.sale_calendar_day_ref = cd.calendar_day_record
        JOIN retail_locations rl ON sp.retail_location_ref = rl.retail_location_record
        WHERE ct.shift = {_q(p['shift'])} AND rl.state = {_q(p['store_state'])} AND cd.year = {int(p['year'])}
        GROUP BY ct.shift, rl.state, cd.year
        """,
        "Clock time sales analysis.",
    )


def _store_refunds_reason(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Store refund amount for {p['category']} products with return reason {p['reason']} in {p['year']}.",
        f"""
        SELECT rr.reason_description, m.category, cd.year, SUM(sr.return_amount) AS return_amount
        FROM store_refunds sr
        JOIN calendar_days cd ON sr.return_calendar_day_ref = cd.calendar_day_record
        JOIN return_reasons rr ON sr.return_reason_ref = rr.return_reason_record
        JOIN merchandise m ON sr.merchandise_ref = m.merchandise_record
        WHERE rr.reason_description = {_q(p['reason'])} AND m.category = {_q(p['category'])} AND cd.year = {int(p['year'])}
        GROUP BY rr.reason_description, m.category, cd.year
        """,
        "Store returns by reason.",
    )


def _store_refunds_profile(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Store refunds by {p['gender']} customer profile and reason {p['reason']} in {p['year']}.",
        f"""
        SELECT cp.gender, rr.reason_description, cd.year, SUM(sr.return_amount) AS return_amount
        FROM store_refunds sr
        JOIN calendar_days cd ON sr.return_calendar_day_ref = cd.calendar_day_record
        JOIN client_profiles cp ON sr.client_profile_ref = cp.client_profile_record
        JOIN return_reasons rr ON sr.return_reason_ref = rr.return_reason_record
        WHERE cp.gender = {_q(p['gender'])} AND rr.reason_description = {_q(p['reason'])} AND cd.year = {int(p['year'])}
        GROUP BY cp.gender, rr.reason_description, cd.year
        """,
        "Store refund transaction-time customer profile.",
    )


def _online_refunds_reason(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Online refund amount for {p['category']} products with reason {p['reason']} in {p['year']}.",
        f"""
        SELECT rr.reason_description, m.category, cd.year, SUM(orx.return_amount) AS return_amount
        FROM online_refunds orx
        JOIN calendar_days cd ON orx.return_calendar_day_ref = cd.calendar_day_record
        JOIN return_reasons rr ON orx.return_reason_ref = rr.return_reason_record
        JOIN merchandise m ON orx.merchandise_ref = m.merchandise_record
        WHERE rr.reason_description = {_q(p['reason'])} AND m.category = {_q(p['category'])} AND cd.year = {int(p['year'])}
        GROUP BY rr.reason_description, m.category, cd.year
        """,
        "Online returns by reason.",
    )


def _online_refunds_site(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Online refunds from site pages of type {p['page_type']} with reason {p['reason']} in {p['year']}.",
        f"""
        SELECT sp.type, rr.reason_description, cd.year, SUM(orx.return_amount) AS return_amount
        FROM online_refunds orx
        JOIN calendar_days cd ON orx.return_calendar_day_ref = cd.calendar_day_record
        JOIN site_pages sp ON orx.site_page_ref = sp.site_page_record
        JOIN return_reasons rr ON orx.return_reason_ref = rr.return_reason_record
        WHERE sp.type = {_q(p['page_type'])} AND rr.reason_description = {_q(p['reason'])} AND cd.year = {int(p['year'])}
        GROUP BY sp.type, rr.reason_description, cd.year
        """,
        "Online refund site-page attribution.",
    )


def _mail_order_support(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Mail order net paid with delivery for support center {p['center']} and carrier {p['carrier']} in {p['year']}.",
        f"""
        SELECT sc.name, dm.carrier, cd.year, SUM(mp.net_paid_with_delivery) AS net_paid_with_delivery
        FROM mail_order_purchases mp
        JOIN calendar_days cd ON mp.sale_calendar_day_ref = cd.calendar_day_record
        JOIN support_centers sc ON mp.support_center_ref = sc.support_center_record
        JOIN delivery_methods dm ON mp.delivery_method_ref = dm.delivery_method_record
        WHERE sc.name = {_q(p['center'])} AND dm.carrier = {_q(p['carrier'])} AND cd.year = {int(p['year'])}
        GROUP BY sc.name, dm.carrier, cd.year
        """,
        "Mail order support center and carrier.",
    )


def _mail_order_warehouse(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Mail order delivery cost by warehouse {p['warehouse']} and carrier {p['carrier']} in {p['year']}.",
        f"""
        SELECT fc.warehouse_name, dm.carrier, cd.year, SUM(mp.extended_delivery_cost) AS delivery_cost
        FROM mail_order_purchases mp
        JOIN calendar_days cd ON mp.sale_calendar_day_ref = cd.calendar_day_record
        JOIN fulfillment_centers fc ON mp.fulfillment_center_ref = fc.fulfillment_center_record
        JOIN delivery_methods dm ON mp.delivery_method_ref = dm.delivery_method_record
        WHERE fc.warehouse_name = {_q(p['warehouse'])} AND dm.carrier = {_q(p['carrier'])} AND cd.year = {int(p['year'])}
        GROUP BY fc.warehouse_name, dm.carrier, cd.year
        """,
        "Mail order warehouse and delivery.",
    )


def _mail_order_shipping(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Mail order customers shipped to {p['state']} by carrier {p['carrier']} in {p['year']}.",
        f"""
        SELECT a.state, dm.carrier, cd.year, COUNT(DISTINCT mp.billing_client_ref) AS customers
        FROM mail_order_purchases mp
        JOIN calendar_days cd ON mp.sale_calendar_day_ref = cd.calendar_day_record
        JOIN addresses a ON mp.shipping_address_ref = a.address_record
        JOIN delivery_methods dm ON mp.delivery_method_ref = dm.delivery_method_record
        WHERE a.state = {_q(p['state'])} AND dm.carrier = {_q(p['carrier'])} AND cd.year = {int(p['year'])}
        GROUP BY a.state, dm.carrier, cd.year
        """,
        "Mail order shipping geography.",
    )


def _mail_order_mailer(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Mail order net sales by mailer department {p['department']} and mailer type {p['mailer_type']} in {p['year']}.",
        f"""
        SELECT ml.department, ml.type, cd.year, SUM(mp.net_paid) AS net_paid
        FROM mail_order_purchases mp
        JOIN calendar_days cd ON mp.sale_calendar_day_ref = cd.calendar_day_record
        JOIN mailer_pages ml ON mp.mailer_page_ref = ml.mailer_page_record
        WHERE ml.department = {_q(p['department'])} AND ml.type = {_q(p['mailer_type'])} AND cd.year = {int(p['year'])}
        GROUP BY ml.department, ml.type, cd.year
        """,
        "Mail order mailer page attribution.",
    )


def _mailer_page_calendar(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Mailer pages in department {p['department']} of type {p['mailer_type']} that started in {p['year']}.",
        f"""
        SELECT ml.department, ml.type, start_day.year, COUNT(*) AS pages
        FROM mailer_pages ml
        JOIN calendar_days start_day ON ml.start_calendar_day_ref = start_day.calendar_day_record
        JOIN calendar_days end_day ON ml.end_calendar_day_ref = end_day.calendar_day_record
        WHERE ml.department = {_q(p['department'])} AND ml.type = {_q(p['mailer_type'])} AND start_day.year = {int(p['year'])}
        GROUP BY ml.department, ml.type, start_day.year
        """,
        "Mailer page start and end date roles.",
    )


def _mail_refunds_ops(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Mail order refunds with reason {p['reason']} by carrier {p['carrier']} in {p['year']}.",
        f"""
        SELECT rr.reason_description, dm.carrier, cd.year, SUM(mr.return_amount) AS return_amount
        FROM mail_order_refunds mr
        JOIN calendar_days cd ON mr.return_calendar_day_ref = cd.calendar_day_record
        JOIN return_reasons rr ON mr.return_reason_ref = rr.return_reason_record
        JOIN delivery_methods dm ON mr.delivery_method_ref = dm.delivery_method_record
        WHERE rr.reason_description = {_q(p['reason'])} AND dm.carrier = {_q(p['carrier'])} AND cd.year = {int(p['year'])}
        GROUP BY rr.reason_description, dm.carrier, cd.year
        """,
        "Mail order refund operations.",
    )


def _inventory_stock(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Positive stock for {p['category']} at warehouse {p['warehouse']} during month {p['month']} of {p['year']}.",
        f"""
        SELECT fc.warehouse_name, m.category, cd.year, cd.month_of_year, SUM(sl.quantity_on_hand) AS quantity_on_hand
        FROM stock_levels sl
        JOIN calendar_days cd ON sl.calendar_day_ref = cd.calendar_day_record
        JOIN fulfillment_centers fc ON sl.fulfillment_center_ref = fc.fulfillment_center_record
        JOIN merchandise m ON sl.merchandise_ref = m.merchandise_record
        WHERE fc.warehouse_name = {_q(p['warehouse'])} AND m.category = {_q(p['category'])} AND cd.year = {int(p['year'])} AND cd.month_of_year = {int(p['month'])} AND sl.quantity_on_hand > 0
        GROUP BY fc.warehouse_name, m.category, cd.year, cd.month_of_year
        """,
        "Inventory stock by warehouse and item.",
    )


def _inventory_demand(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Compare online demand and stock for {p['category']} at warehouse {p['warehouse']} in {p['year']}.",
        f"""
        SELECT fc.warehouse_name, m.category, cd.year, SUM(op.quantity) AS online_units, SUM(sl.quantity_on_hand) AS stock_units
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        JOIN merchandise m ON op.merchandise_ref = m.merchandise_record
        JOIN fulfillment_centers fc ON op.fulfillment_center_ref = fc.fulfillment_center_record
        JOIN stock_levels sl ON sl.calendar_day_ref = cd.calendar_day_record AND sl.merchandise_ref = m.merchandise_record AND sl.fulfillment_center_ref = fc.fulfillment_center_record
        WHERE fc.warehouse_name = {_q(p['warehouse'])} AND m.category = {_q(p['category'])} AND cd.year = {int(p['year'])}
        GROUP BY fc.warehouse_name, m.category, cd.year
        """,
        "Demand to stock same-day join.",
    )


def _product_catalog(p: dict[str, Any]) -> tuple[str, str, str]:
    return (
        f"Summarize merchandise for brand {p['brand']}, color {p['color']}, and size {p['size']}.",
        f"""
        SELECT m.brand, m.color, m.size, m.category, m.class, m.manufacturer, AVG(m.current_price) AS avg_current_price, AVG(m.wholesale_cost) AS avg_wholesale_cost, COUNT(*) AS products
        FROM merchandise m
        WHERE m.brand = {_q(p['brand'])} AND m.color = {_q(p['color'])} AND m.size = {_q(p['size'])}
        GROUP BY m.brand, m.color, m.size, m.category, m.class, m.manufacturer
        """,
        "Product catalog attributes.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
