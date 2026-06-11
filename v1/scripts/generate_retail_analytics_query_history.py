"""Generate simulated Databricks-style query history for retail analytics."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_OUTPUT_PATH = Path("data/query_history/retail_analytics_query_history.csv")
DEFAULT_COUNT = 150
DEFAULT_SEED = 20260607

WAREHOUSE_ID = "0123-456789-retailwh"
ACCOUNT_ID = "synthetic-account"
WORKSPACE_ID = "synthetic-workspace"
USER_NAMES = [
    "growth_pm@example.com",
    "customer_insights@example.com",
    "marketing_ops@example.com",
    "finance_partner@example.com",
    "supply_chain@example.com",
    "data_quality@example.com",
]
CLIENT_APPS = ["Databricks SQL", "JDBC", "Notebook", "DiracData Simulation"]

QUERY_HISTORY_COLUMNS = [
    "account_id",
    "workspace_id",
    "statement_id",
    "session_id",
    "execution_status",
    "executed_by",
    "executed_by_user_id",
    "statement_text",
    "statement_type",
    "error_message",
    "client_application",
    "total_duration_ms",
    "waiting_for_compute_duration_ms",
    "waiting_at_capacity_duration_ms",
    "execution_duration_ms",
    "compilation_duration_ms",
    "total_task_duration_ms",
    "result_fetch_duration_ms",
    "start_time",
    "end_time",
    "update_time",
    "read_rows",
    "read_bytes",
    "produced_rows",
    "read_files",
    "read_partitions",
    "read_io_cache_percent",
    "from_result_cache",
    "pruned_files",
    "pruned_bytes",
    "statement_parameters",
    "compute",
    "query_source",
    "query_parameters",
    "query_tags",
]

STATES = ["AZ", "CA", "TX", "NY", "WA", "IL", "FL", "OH", "NC", "GA"]
CATEGORIES = ["Jewelry", "Electronics", "Women", "Men", "Shoes", "Home"]
GENDERS = ["F", "M"]
YEARS = [1998, 1999, 2000, 2001, 2002]

TPCDS_TECHNICAL_NAMES = {
    "catalog_sales",
    "catalog_returns",
    "customer_address",
    "customer_demographics",
    "date_dim",
    "inventory",
    "item",
    "promotion",
    "store_sales",
    "web_sales",
    "web_returns",
    "_sk",
    "_pk",
}


@dataclass(frozen=True)
class QueryTemplate:
    name: str
    weight: int
    render: Callable[[random.Random], str]
    failure_status: str | None = None
    error_message: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args()


def generate_records(count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED) -> list[dict[str, object]]:
    """Generate deterministic retail query-history records."""
    if count < len(success_templates()) + 2:
        raise ValueError(f"count must be at least {len(success_templates()) + 2}")

    rng = random.Random(seed)
    start_base = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    templates = query_templates()
    success = success_templates()

    selected: list[QueryTemplate] = [*success, failed_template(), canceled_template()]
    weighted_templates = [template for template in templates if template.name != "canceled_long_query"]
    while len(selected) < count:
        selected.append(
            rng.choices(
                weighted_templates,
                weights=[template.weight for template in weighted_templates],
                k=1,
            )[0]
        )

    rng.shuffle(selected)
    return [
        make_record(
            index=index,
            template=template,
            rng=rng,
            start_base=start_base,
        )
        for index, template in enumerate(selected)
    ]


def write_records(records: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=QUERY_HISTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(records)


def query_templates() -> list[QueryTemplate]:
    return [
        QueryTemplate("online_customer_slice", 14, render_online_customer_slice),
        QueryTemplate("channel_customer_comparison", 10, render_channel_customer_comparison),
        QueryTemplate("address_role_comparison", 8, render_address_role_comparison),
        QueryTemplate("marketing_campaign_performance", 10, render_marketing_campaign_performance),
        QueryTemplate("refunds_by_channel", 9, render_refunds_by_channel),
        QueryTemplate("inventory_health", 8, render_inventory_health),
        QueryTemplate("financial_margin", 9, render_financial_margin),
        QueryTemplate("online_retention", 7, render_online_retention),
        QueryTemplate("household_income_segments", 6, render_household_income_segments),
        QueryTemplate("store_location_sales", 8, render_store_location_sales),
        QueryTemplate("online_property_performance", 6, render_online_property_performance),
        QueryTemplate("mail_order_fulfillment", 6, render_mail_order_fulfillment),
        failed_template(),
        canceled_template(),
    ]


def success_templates() -> list[QueryTemplate]:
    return [template for template in query_templates() if template.failure_status is None]


def failed_template() -> QueryTemplate:
    return QueryTemplate(
        "failed_bad_column",
        2,
        render_failed_bad_column,
        failure_status="FAILED",
        error_message="Column resolution failed during semantic simulation",
    )


def canceled_template() -> QueryTemplate:
    return QueryTemplate(
        "canceled_long_query",
        1,
        render_inventory_health,
        failure_status="CANCELED",
        error_message="Statement canceled by user",
    )


def compact_sql(sql: str) -> str:
    return "\n".join(line.rstrip() for line in sql.strip().splitlines())


def json_cell(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def pick_year(rng: random.Random) -> int:
    return rng.choice(YEARS)


def pick_state(rng: random.Random) -> str:
    return rng.choice(STATES)


def pick_category(rng: random.Random) -> str:
    return rng.choice(CATEGORIES)


def pick_gender(rng: random.Random) -> str:
    return rng.choice(GENDERS)


def render_online_customer_slice(rng: random.Random) -> str:
    year = pick_year(rng)
    state = pick_state(rng)
    category = pick_category(rng)
    gender = pick_gender(rng)
    return compact_sql(
        f"""
        SELECT
          a.state,
          cp.gender,
          m.category,
          cd.year,
          count(DISTINCT op.billing_client_ref) AS online_customers,
          sum(op.net_paid) AS net_paid
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        JOIN clients c ON op.billing_client_ref = c.client_record
        JOIN client_profiles cp ON c.current_client_profile_ref = cp.client_profile_record
        JOIN addresses a ON c.current_address_ref = a.address_record
        JOIN merchandise m ON op.merchandise_ref = m.merchandise_record
        WHERE cd.year = {year}
          AND a.state = '{state}'
          AND cp.gender = '{gender}'
          AND m.category = '{category}'
        GROUP BY a.state, cp.gender, m.category, cd.year
        """
    )


def render_channel_customer_comparison(rng: random.Random) -> str:
    year = pick_year(rng)
    return compact_sql(
        f"""
        WITH channel_customers AS (
          SELECT 'online' AS channel, op.billing_client_ref AS client_ref
          FROM online_purchases op
          JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
          WHERE cd.year = {year}
          UNION ALL
          SELECT 'mail_order' AS channel, mp.billing_client_ref AS client_ref
          FROM mail_order_purchases mp
          JOIN calendar_days cd ON mp.sale_calendar_day_ref = cd.calendar_day_record
          WHERE cd.year = {year}
          UNION ALL
          SELECT 'store' AS channel, sp.client_ref AS client_ref
          FROM store_purchases sp
          JOIN calendar_days cd ON sp.sale_calendar_day_ref = cd.calendar_day_record
          WHERE cd.year = {year}
        )
        SELECT channel, count(DISTINCT client_ref) AS customers
        FROM channel_customers
        WHERE client_ref IS NOT NULL
        GROUP BY channel
        ORDER BY customers DESC
        """
    )


def render_address_role_comparison(rng: random.Random) -> str:
    year = pick_year(rng)
    state = pick_state(rng)
    return compact_sql(
        f"""
        SELECT
          cd.year,
          count(DISTINCT op.billing_client_ref) AS current_state_customers,
          count(DISTINCT CASE WHEN ship_a.state = '{state}' THEN op.shipping_client_ref END)
            AS shipping_state_customers,
          count(DISTINCT CASE WHEN bill_a.state = '{state}' THEN op.billing_client_ref END)
            AS billing_state_customers
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        JOIN clients c ON op.billing_client_ref = c.client_record
        JOIN addresses current_a ON c.current_address_ref = current_a.address_record
        LEFT JOIN addresses ship_a ON op.shipping_address_ref = ship_a.address_record
        LEFT JOIN addresses bill_a ON op.billing_address_ref = bill_a.address_record
        WHERE cd.year = {year}
          AND current_a.state = '{state}'
        GROUP BY cd.year
        """
    )


def render_marketing_campaign_performance(rng: random.Random) -> str:
    year = pick_year(rng)
    category = pick_category(rng)
    return compact_sql(
        f"""
        SELECT
          mc.campaign_name,
          m.category,
          count(DISTINCT op.billing_client_ref) AS customers,
          sum(op.net_paid) AS net_paid
        FROM online_purchases op
        JOIN marketing_campaigns mc ON op.campaign_ref = mc.campaign_record
        JOIN merchandise m ON op.merchandise_ref = m.merchandise_record
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        WHERE cd.year = {year}
          AND m.category = '{category}'
        GROUP BY mc.campaign_name, m.category
        ORDER BY customers DESC
        LIMIT 20
        """
    )


def render_refunds_by_channel(rng: random.Random) -> str:
    year = pick_year(rng)
    category = pick_category(rng)
    return compact_sql(
        f"""
        WITH refunds AS (
          SELECT 'online' AS channel, refunded_client_ref, merchandise_ref, return_calendar_day_ref, return_amount
          FROM online_refunds
          UNION ALL
          SELECT 'mail_order' AS channel, refunded_client_ref, merchandise_ref, return_calendar_day_ref, return_amount
          FROM mail_order_refunds
          UNION ALL
          SELECT 'store' AS channel, client_ref AS refunded_client_ref, merchandise_ref, return_calendar_day_ref, return_amount
          FROM store_refunds
        )
        SELECT
          r.channel,
          m.category,
          count(DISTINCT r.refunded_client_ref) AS refunded_customers,
          sum(r.return_amount) AS refund_amount
        FROM refunds r
        JOIN merchandise m ON r.merchandise_ref = m.merchandise_record
        JOIN calendar_days cd ON r.return_calendar_day_ref = cd.calendar_day_record
        WHERE cd.year = {year}
          AND m.category = '{category}'
        GROUP BY r.channel, m.category
        ORDER BY refund_amount DESC
        """
    )


def render_inventory_health(rng: random.Random) -> str:
    year = pick_year(rng)
    month = rng.randint(1, 12)
    category = pick_category(rng)
    return compact_sql(
        f"""
        SELECT
          fc.warehouse_name,
          m.category,
          cd.year,
          cd.month_of_year,
          sum(sl.quantity_on_hand) AS stock_on_hand
        FROM stock_levels sl
        JOIN merchandise m ON sl.merchandise_ref = m.merchandise_record
        JOIN calendar_days cd ON sl.calendar_day_ref = cd.calendar_day_record
        JOIN fulfillment_centers fc ON sl.fulfillment_center_ref = fc.fulfillment_center_record
        WHERE cd.year = {year}
          AND cd.month_of_year = {month}
          AND m.category = '{category}'
        GROUP BY fc.warehouse_name, m.category, cd.year, cd.month_of_year
        ORDER BY stock_on_hand ASC
        LIMIT 50
        """
    )


def render_financial_margin(rng: random.Random) -> str:
    year = pick_year(rng)
    category = pick_category(rng)
    return compact_sql(
        f"""
        SELECT
          cd.year,
          m.category,
          sum(op.net_paid) AS net_paid,
          sum(op.net_profit) AS net_profit,
          sum(op.net_profit) / nullif(sum(op.net_paid), 0) AS profit_margin
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        JOIN merchandise m ON op.merchandise_ref = m.merchandise_record
        WHERE cd.year = {year}
          AND m.category = '{category}'
        GROUP BY cd.year, m.category
        """
    )


def render_online_retention(rng: random.Random) -> str:
    start_year = rng.choice([1998, 1999, 2000, 2001])
    end_year = start_year + 1
    return compact_sql(
        f"""
        WITH customer_years AS (
          SELECT op.billing_client_ref, cd.year
          FROM online_purchases op
          JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
          WHERE cd.year IN ({start_year}, {end_year})
            AND op.billing_client_ref IS NOT NULL
          GROUP BY op.billing_client_ref, cd.year
        )
        SELECT count(*) AS retained_online_customers
        FROM (
          SELECT billing_client_ref
          FROM customer_years
          GROUP BY billing_client_ref
          HAVING count(DISTINCT year) = 2
        ) retained
        """
    )


def render_household_income_segments(rng: random.Random) -> str:
    state = pick_state(rng)
    return compact_sql(
        f"""
        SELECT
          a.state,
          ir.lower_bound,
          ir.upper_bound,
          count(DISTINCT c.client_record) AS clients
        FROM clients c
        JOIN addresses a ON c.current_address_ref = a.address_record
        JOIN household_profiles hp ON c.current_household_profile_ref = hp.household_profile_record
        JOIN income_ranges ir ON hp.income_range_ref = ir.income_range_record
        WHERE a.state = '{state}'
        GROUP BY a.state, ir.lower_bound, ir.upper_bound
        ORDER BY clients DESC
        """
    )


def render_store_location_sales(rng: random.Random) -> str:
    year = pick_year(rng)
    state = pick_state(rng)
    category = pick_category(rng)
    return compact_sql(
        f"""
        SELECT
          rl.state,
          rl.store_name,
          m.category,
          sum(sp.net_paid) AS net_paid,
          count(DISTINCT sp.client_ref) AS store_customers
        FROM store_purchases sp
        JOIN retail_locations rl ON sp.retail_location_ref = rl.retail_location_record
        JOIN merchandise m ON sp.merchandise_ref = m.merchandise_record
        JOIN calendar_days cd ON sp.sale_calendar_day_ref = cd.calendar_day_record
        WHERE cd.year = {year}
          AND rl.state = '{state}'
          AND m.category = '{category}'
        GROUP BY rl.state, rl.store_name, m.category
        ORDER BY net_paid DESC
        LIMIT 25
        """
    )


def render_online_property_performance(rng: random.Random) -> str:
    year = pick_year(rng)
    return compact_sql(
        f"""
        SELECT
          op2.name AS online_property,
          sp.page_type,
          count(DISTINCT op.billing_client_ref) AS customers,
          sum(op.net_paid) AS net_paid
        FROM online_purchases op
        JOIN online_properties op2 ON op.online_property_ref = op2.online_property_record
        JOIN site_pages sp ON op.site_page_ref = sp.site_page_record
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        WHERE cd.year = {year}
        GROUP BY op2.name, sp.page_type
        ORDER BY net_paid DESC
        LIMIT 20
        """
    )


def render_mail_order_fulfillment(rng: random.Random) -> str:
    year = pick_year(rng)
    return compact_sql(
        f"""
        SELECT
          fc.warehouse_name,
          dm.carrier,
          sc.name AS support_center,
          count(DISTINCT mp.order_number) AS orders,
          sum(mp.net_paid_with_delivery) AS net_paid_with_delivery
        FROM mail_order_purchases mp
        JOIN fulfillment_centers fc ON mp.fulfillment_center_ref = fc.fulfillment_center_record
        JOIN delivery_methods dm ON mp.delivery_method_ref = dm.delivery_method_record
        JOIN support_centers sc ON mp.support_center_ref = sc.support_center_record
        JOIN calendar_days cd ON mp.sale_calendar_day_ref = cd.calendar_day_record
        WHERE cd.year = {year}
        GROUP BY fc.warehouse_name, dm.carrier, sc.name
        ORDER BY net_paid_with_delivery DESC
        LIMIT 20
        """
    )


def render_failed_bad_column(rng: random.Random) -> str:
    year = pick_year(rng)
    return compact_sql(
        f"""
        SELECT cd.year, sum(op.revenue_that_does_not_exist) AS broken_revenue
        FROM online_purchases op
        JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
        WHERE cd.year = {year}
        GROUP BY cd.year
        """
    )


def make_record(
    *,
    index: int,
    template: QueryTemplate,
    rng: random.Random,
    start_base: datetime,
) -> dict[str, object]:
    statement_text = template.render(rng)
    status = template.failure_status or "FINISHED"
    error_message = template.error_message

    start_time = start_base + timedelta(minutes=index * rng.randint(3, 20))
    duration_ms = rng.randint(350, 80_000)
    compilation_ms = rng.randint(20, 4_000)
    waiting_compute_ms = rng.randint(0, 2_500)
    waiting_capacity_ms = rng.randint(0, 1_500)
    result_fetch_ms = 0 if status != "FINISHED" else rng.randint(5, 8_000)
    execution_ms = max(
        0,
        duration_ms - compilation_ms - waiting_compute_ms - waiting_capacity_ms - result_fetch_ms,
    )
    end_time = start_time + timedelta(milliseconds=duration_ms)
    produced_rows = 0 if status != "FINISHED" else rng.randint(1, 1_000)
    read_rows = 0 if status == "FAILED" else rng.randint(10_000, 6_000_000)
    read_bytes = read_rows * rng.randint(24, 180)

    executed_by = rng.choice(USER_NAMES)
    statement_id = f"retail-stmt-{index + 1:06d}"

    return {
        "account_id": ACCOUNT_ID,
        "workspace_id": WORKSPACE_ID,
        "statement_id": statement_id,
        "session_id": f"retail-session-{rng.randint(1, 80):04d}",
        "execution_status": status,
        "executed_by": executed_by,
        "executed_by_user_id": f"user-{abs(hash(executed_by)) % 100000:05d}",
        "statement_text": statement_text,
        "statement_type": "SELECT",
        "error_message": error_message,
        "client_application": rng.choice(CLIENT_APPS),
        "total_duration_ms": duration_ms,
        "waiting_for_compute_duration_ms": waiting_compute_ms,
        "waiting_at_capacity_duration_ms": waiting_capacity_ms,
        "execution_duration_ms": execution_ms,
        "compilation_duration_ms": compilation_ms,
        "total_task_duration_ms": execution_ms * rng.randint(1, 8),
        "result_fetch_duration_ms": result_fetch_ms,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "update_time": end_time.isoformat(),
        "read_rows": read_rows,
        "read_bytes": read_bytes,
        "produced_rows": produced_rows,
        "read_files": rng.randint(1, 500),
        "read_partitions": rng.randint(1, 96),
        "read_io_cache_percent": round(rng.random() * 100, 2),
        "from_result_cache": rng.random() < 0.08,
        "pruned_files": rng.randint(0, 300),
        "pruned_bytes": rng.randint(0, read_bytes),
        "statement_parameters": json_cell(
            {
                "source": "synthetic_retail_analytics",
                "template": template.name,
            }
        ),
        "compute": json_cell(
            {
                "type": "WAREHOUSE",
                "cluster_id": None,
                "warehouse_id": WAREHOUSE_ID,
            }
        ),
        "query_source": json_cell(
            {
                "dashboard_id": f"retail-dash-{rng.randint(1, 30):03d}"
                if rng.random() < 0.35
                else None,
                "job_info": None,
                "notebook_info": None,
            }
        ),
        "query_parameters": json_cell({}),
        "query_tags": json_cell(
            {
                "pod": "retail_analytics",
                "template": template.name,
                "simulated": "true",
            }
        ),
    }


def main() -> None:
    args = parse_args()
    records = generate_records(count=args.count, seed=args.seed)
    write_records(records, args.output_path)
    successful_count = sum(1 for record in records if record["execution_status"] == "FINISHED")
    print(
        f"Wrote {len(records)} query history records "
        f"({successful_count} successful) to {args.output_path}"
    )


if __name__ == "__main__":
    main()
