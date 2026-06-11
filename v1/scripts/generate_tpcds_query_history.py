"""Generate simulated Databricks-style query history for the local TPC-DS schema."""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_OUTPUT_PATH = Path("data/query_history/tpcds_query_history.csv")
DEFAULT_COUNT = 750

WAREHOUSE_ID = "0123-456789-tpcdswh"
ACCOUNT_ID = "synthetic-account"
WORKSPACE_ID = "synthetic-workspace"
USER_NAMES = [
    "growth_pm@example.com",
    "commerce_analyst@example.com",
    "marketing_ops@example.com",
    "finance_partner@example.com",
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def compact_sql(sql: str) -> str:
    return "\n".join(line.rstrip() for line in sql.strip().splitlines())


def json_cell(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def pick_year(rng: random.Random) -> int:
    return rng.choice([1998, 1999, 2000, 2001, 2002])


def pick_month(rng: random.Random) -> int:
    return rng.randint(1, 12)


def pick_state(rng: random.Random) -> str:
    return rng.choice(["CA", "TX", "NY", "WA", "IL", "FL", "OH", "NC", "GA", "MI"])


def query_templates() -> list[tuple[str, str, list[str]]]:
    return [
        (
            "sales_by_channel_year",
            "SELECT",
            [
                "store_sales.ss_sold_date_sk = date_dim.d_date_sk",
                "web_sales.ws_sold_date_sk = date_dim.d_date_sk",
                "catalog_sales.cs_sold_date_sk = date_dim.d_date_sk",
            ],
        ),
        (
            "store_sales_by_state",
            "SELECT",
            [
                "store_sales.ss_sold_date_sk = date_dim.d_date_sk",
                "store_sales.ss_customer_sk = customer.c_customer_sk",
                "customer.c_current_addr_sk = customer_address.ca_address_sk",
            ],
        ),
        (
            "top_item_categories",
            "SELECT",
            [
                "store_sales.ss_item_sk = item.i_item_sk",
                "store_sales.ss_sold_date_sk = date_dim.d_date_sk",
            ],
        ),
        (
            "promotion_lift",
            "SELECT",
            [
                "store_sales.ss_promo_sk = promotion.p_promo_sk",
                "store_sales.ss_sold_date_sk = date_dim.d_date_sk",
            ],
        ),
        (
            "return_rate_by_category",
            "SELECT",
            [
                "store_returns.sr_item_sk = item.i_item_sk",
                "store_returns.sr_returned_date_sk = date_dim.d_date_sk",
            ],
        ),
        (
            "customer_segment_sales",
            "SELECT",
            [
                "store_sales.ss_customer_sk = customer.c_customer_sk",
                "customer.c_current_cdemo_sk = customer_demographics.cd_demo_sk",
                "store_sales.ss_sold_date_sk = date_dim.d_date_sk",
            ],
        ),
        (
            "inventory_health",
            "SELECT",
            [
                "inventory.inv_item_sk = item.i_item_sk",
                "inventory.inv_date_sk = date_dim.d_date_sk",
                "inventory.inv_warehouse_sk = warehouse.w_warehouse_sk",
            ],
        ),
        (
            "web_sales_by_site",
            "SELECT",
            [
                "web_sales.ws_web_site_sk = web_site.web_site_sk",
                "web_sales.ws_sold_date_sk = date_dim.d_date_sk",
            ],
        ),
        (
            "freshness_check",
            "SELECT",
            [
                "store_sales.ss_sold_date_sk = date_dim.d_date_sk",
            ],
        ),
        (
            "failed_bad_column",
            "SELECT",
            [
                "store_sales.ss_sold_date_sk = date_dim.d_date_sk",
            ],
        ),
    ]


def render_query(template_name: str, rng: random.Random) -> str:
    year = pick_year(rng)
    month = pick_month(rng)
    state = pick_state(rng)

    if template_name == "sales_by_channel_year":
        return compact_sql(
            f"""
            WITH channel_sales AS (
                SELECT 'store' AS channel, d.d_year, sum(ss.ss_net_paid) AS net_paid
                FROM store_sales ss
                JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
                WHERE d.d_year = {year}
                GROUP BY d.d_year
                UNION ALL
                SELECT 'web' AS channel, d.d_year, sum(ws.ws_net_paid) AS net_paid
                FROM web_sales ws
                JOIN date_dim d ON ws.ws_sold_date_sk = d.d_date_sk
                WHERE d.d_year = {year}
                GROUP BY d.d_year
                UNION ALL
                SELECT 'catalog' AS channel, d.d_year, sum(cs.cs_net_paid) AS net_paid
                FROM catalog_sales cs
                JOIN date_dim d ON cs.cs_sold_date_sk = d.d_date_sk
                WHERE d.d_year = {year}
                GROUP BY d.d_year
            )
            SELECT channel, d_year, net_paid
            FROM channel_sales
            ORDER BY net_paid DESC
            """
        )

    if template_name == "store_sales_by_state":
        return compact_sql(
            f"""
            SELECT ca.ca_state, d.d_year, d.d_moy, sum(ss.ss_net_paid) AS net_paid
            FROM store_sales ss
            JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
            JOIN customer c ON ss.ss_customer_sk = c.c_customer_sk
            JOIN customer_address ca ON c.c_current_addr_sk = ca.ca_address_sk
            WHERE d.d_year = {year}
              AND d.d_moy = {month}
              AND ca.ca_state = '{state}'
            GROUP BY ca.ca_state, d.d_year, d.d_moy
            """
        )

    if template_name == "top_item_categories":
        return compact_sql(
            f"""
            SELECT i.i_category, sum(ss.ss_quantity) AS units, sum(ss.ss_net_paid) AS revenue
            FROM store_sales ss
            JOIN item i ON ss.ss_item_sk = i.i_item_sk
            JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
            WHERE d.d_year = {year}
            GROUP BY i.i_category
            ORDER BY revenue DESC
            LIMIT 20
            """
        )

    if template_name == "promotion_lift":
        return compact_sql(
            f"""
            SELECT p.p_channel_email, p.p_channel_event, avg(ss.ss_net_paid) AS avg_order_value
            FROM store_sales ss
            JOIN promotion p ON ss.ss_promo_sk = p.p_promo_sk
            JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
            WHERE d.d_year = {year}
            GROUP BY p.p_channel_email, p.p_channel_event
            """
        )

    if template_name == "return_rate_by_category":
        return compact_sql(
            f"""
            SELECT i.i_category, count(*) AS returns, sum(sr.sr_return_amt) AS return_amount
            FROM store_returns sr
            JOIN item i ON sr.sr_item_sk = i.i_item_sk
            JOIN date_dim d ON sr.sr_returned_date_sk = d.d_date_sk
            WHERE d.d_year = {year}
            GROUP BY i.i_category
            ORDER BY return_amount DESC
            LIMIT 20
            """
        )

    if template_name == "customer_segment_sales":
        return compact_sql(
            f"""
            SELECT cd.cd_gender, cd.cd_marital_status, count(*) AS orders, sum(ss.ss_net_paid) AS revenue
            FROM store_sales ss
            JOIN customer c ON ss.ss_customer_sk = c.c_customer_sk
            JOIN customer_demographics cd ON c.c_current_cdemo_sk = cd.cd_demo_sk
            JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
            WHERE d.d_year = {year}
            GROUP BY cd.cd_gender, cd.cd_marital_status
            ORDER BY revenue DESC
            """
        )

    if template_name == "inventory_health":
        return compact_sql(
            f"""
            SELECT w.w_warehouse_name, i.i_category, avg(inv.inv_quantity_on_hand) AS avg_on_hand
            FROM inventory inv
            JOIN warehouse w ON inv.inv_warehouse_sk = w.w_warehouse_sk
            JOIN item i ON inv.inv_item_sk = i.i_item_sk
            JOIN date_dim d ON inv.inv_date_sk = d.d_date_sk
            WHERE d.d_year = {year}
              AND d.d_moy = {month}
            GROUP BY w.w_warehouse_name, i.i_category
            ORDER BY avg_on_hand ASC
            LIMIT 50
            """
        )

    if template_name == "web_sales_by_site":
        return compact_sql(
            f"""
            SELECT ws2.web_name, d.d_year, sum(ws.ws_net_paid) AS net_paid
            FROM web_sales ws
            JOIN web_site ws2 ON ws.ws_web_site_sk = ws2.web_site_sk
            JOIN date_dim d ON ws.ws_sold_date_sk = d.d_date_sk
            WHERE d.d_year = {year}
            GROUP BY ws2.web_name, d.d_year
            ORDER BY net_paid DESC
            """
        )

    if template_name == "freshness_check":
        return compact_sql(
            """
            SELECT max(d.d_date) AS latest_store_sales_date, count(*) AS rows_on_latest_date
            FROM store_sales ss
            JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
            WHERE d.d_date = (
                SELECT max(d2.d_date)
                FROM store_sales ss2
                JOIN date_dim d2 ON ss2.ss_sold_date_sk = d2.d_date_sk
            )
            """
        )

    return compact_sql(
        f"""
        SELECT d.d_year, sum(ss.ss_nonexistent_metric) AS broken_metric
        FROM store_sales ss
        JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
        WHERE d.d_year = {year}
        GROUP BY d.d_year
        """
    )


def make_record(index: int, rng: random.Random, start_base: datetime) -> dict[str, object]:
    templates = query_templates()
    template_name, statement_type, _joins = rng.choices(
        templates,
        weights=[14, 12, 12, 9, 8, 10, 7, 8, 5, 2],
        k=1,
    )[0]
    statement_text = render_query(template_name, rng)
    is_failed = template_name == "failed_bad_column" or rng.random() < 0.025
    is_canceled = not is_failed and rng.random() < 0.015

    if is_failed:
        status = "FAILED"
        error_message = "Column resolution failed during semantic simulation"
    elif is_canceled:
        status = "CANCELED"
        error_message = "Statement canceled by user"
    else:
        status = "FINISHED"
        error_message = None

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
    statement_id = f"stmt-{index + 1:06d}"

    return {
        "account_id": ACCOUNT_ID,
        "workspace_id": WORKSPACE_ID,
        "statement_id": statement_id,
        "session_id": f"session-{rng.randint(1, 80):04d}",
        "execution_status": status,
        "executed_by": executed_by,
        "executed_by_user_id": f"user-{abs(hash(executed_by)) % 100000:05d}",
        "statement_text": statement_text,
        "statement_type": statement_type,
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
                "source": "synthetic_tpcds",
                "template": template_name,
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
                "dashboard_id": f"dash-{rng.randint(1, 30):03d}" if rng.random() < 0.35 else None,
                "job_info": None,
                "notebook_info": None,
            }
        ),
        "query_parameters": json_cell({}),
        "query_tags": json_cell(
            {
                "pod": "tpcds_commerce",
                "template": template_name,
                "simulated": "true",
            }
        ),
    }


def main() -> None:
    args = parse_args()
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    start_base = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)

    records = [make_record(index, rng, start_base) for index in range(args.count)]
    with args.output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=QUERY_HISTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(records)

    print(f"Wrote {len(records)} query history records to {args.output_path}")


if __name__ == "__main__":
    main()

