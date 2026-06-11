"""Generate simulated Databricks-style query history for fintech_schema."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_OUTPUT_PATH = Path("data/query_history/fintech_schema_query_history.csv")
DEFAULT_COUNT = 750
DEFAULT_UNIQUE_SUCCESS_SQL = 60
DEFAULT_SEED = 20260607

WAREHOUSE_ID = "0123-456789-fintechwh"
ACCOUNT_ID = "synthetic-account"
WORKSPACE_ID = "synthetic-workspace"
USER_NAMES = [
    "growth_pm@example.com",
    "payments_pm@example.com",
    "risk_ops@example.com",
    "finance_partner@example.com",
    "merchant_success@example.com",
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

RAILS = ["UPI", "CC", "DC", "NEFT", "IMPS", "NETBANKING", "WALLET"]
STATES = ["Maharashtra", "Karnataka", "Delhi", "Tamil Nadu", "Telangana", "Gujarat", "Rajasthan"]
GENDERS = ["F", "M", "Other"]
PRODUCT_AREAS = ["payments", "subscriptions", "payouts", "checkout", "marketplace"]
SURFACES = ["web", "mobile_sdk", "api", "dashboard", "plugin"]
RISK_BANDS = ["low", "medium", "high"]
MONTH_STARTS = ["2025-12-01", "2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01"]


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
    parser.add_argument("--unique-success-sql", type=int, default=DEFAULT_UNIQUE_SUCCESS_SQL)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = generate_records(
        count=args.count,
        unique_success_sql=args.unique_success_sql,
        seed=args.seed,
    )
    write_records(records, args.output_path)
    print(
        json.dumps(
            {
                "status": "generated",
                "output_path": str(args.output_path),
                "record_count": len(records),
                "unique_success_sql": len(
                    {
                        row["statement_text"]
                        for row in records
                        if row["execution_status"] == "FINISHED"
                    }
                ),
            },
            indent=2,
        ),
        flush=True,
    )


def generate_records(
    *,
    count: int = DEFAULT_COUNT,
    unique_success_sql: int = DEFAULT_UNIQUE_SUCCESS_SQL,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, object]]:
    """Generate deterministic, repeated query-history records."""
    if count < 20:
        raise ValueError("count must be at least 20")
    if unique_success_sql < len(success_templates()):
        raise ValueError(f"unique_success_sql must be at least {len(success_templates())}")

    rng = random.Random(seed)
    start_base = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    sql_pool = success_sql_pool(unique_success_sql=unique_success_sql, seed=seed)
    selected_sql = [rng.choice(sql_pool) for _ in range(count - 2)]
    records = [
        make_success_record(index=index, sql=sql, rng=rng, start_base=start_base)
        for index, sql in enumerate(selected_sql)
    ]
    records.append(make_non_success_record(len(records), failed_template(), rng, start_base))
    records.append(make_non_success_record(len(records), canceled_template(), rng, start_base))
    rng.shuffle(records)
    return records


def success_sql_pool(*, unique_success_sql: int, seed: int = DEFAULT_SEED) -> list[str]:
    rng = random.Random(seed)
    pool: list[str] = []
    templates = success_templates()
    for template in templates:
        _append_unique(pool, template.render(rng))
    while len(pool) < unique_success_sql:
        template = rng.choices(templates, weights=[item.weight for item in templates], k=1)[0]
        _append_unique(pool, template.render(rng))
    return pool[:unique_success_sql]


def write_records(records: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=QUERY_HISTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(records)


def success_templates() -> list[QueryTemplate]:
    return [
        QueryTemplate("tpv_by_rail_month", 12, render_tpv_by_rail_month),
        QueryTemplate("psr_by_rail", 12, render_psr_by_rail),
        QueryTemplate("active_users_by_state", 10, render_active_users_by_state),
        QueryTemplate("retention_window", 8, render_retention_window),
        QueryTemplate("churn_by_segment", 8, render_churn_by_segment),
        QueryTemplate("order_payment_funnel", 9, render_order_payment_funnel),
        QueryTemplate("rail_risk_performance", 8, render_rail_risk_performance),
        QueryTemplate("kyc_success_rate", 6, render_kyc_success_rate),
        QueryTemplate("checkout_surface_volume", 7, render_checkout_surface_volume),
        QueryTemplate("payment_metrics_by_user_segment_and_auth", 12, render_payment_metrics_by_user_segment_and_auth),
        QueryTemplate("merchant_plan_performance", 6, render_merchant_plan_performance),
    ]


def failed_template() -> QueryTemplate:
    return QueryTemplate(
        "failed_bad_column",
        1,
        lambda _rng: "SELECT count(*) FROM payments WHERE fake_status = 'SUCCESS'",
        failure_status="FAILED",
        error_message="Column resolution failed during semantic simulation",
    )


def canceled_template() -> QueryTemplate:
    return QueryTemplate(
        "canceled_long_query",
        1,
        render_tpv_by_rail_month,
        failure_status="CANCELED",
        error_message="Statement canceled by user",
    )


def render_tpv_by_rail_month(rng: random.Random) -> str:
    month = pick_month(rng)
    return compact_sql(
        f"""
        SELECT
          date_trunc('month', p.payment_time) AS month,
          pa.rail_type,
          sum(p.amount) AS tpv
        FROM payments p
        JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref
        WHERE p.payment_status = 'SUCCESS'
          AND p.payment_time >= TIMESTAMP '{month}'
          AND p.payment_time < TIMESTAMP '{next_month(month)}'
        GROUP BY month, pa.rail_type
        ORDER BY month, pa.rail_type
        """
    )


def render_psr_by_rail(rng: random.Random) -> str:
    rail = pick_rail(rng)
    month = pick_month(rng)
    return compact_sql(
        f"""
        SELECT
          pa.rail_type,
          sum(CASE WHEN p.payment_status = 'SUCCESS' THEN 1 ELSE 0 END)::DOUBLE / count(*) AS psr,
          count(*) AS attempts
        FROM payments p
        JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref
        WHERE pa.rail_type = '{rail}'
          AND p.payment_time >= TIMESTAMP '{month}'
          AND p.payment_time < TIMESTAMP '{next_month(month)}'
        GROUP BY pa.rail_type
        """
    )


def render_active_users_by_state(rng: random.Random) -> str:
    state = pick_state(rng)
    month = pick_month(rng)
    return compact_sql(
        f"""
        SELECT
          ua.state,
          count(DISTINCT p.user_ref) AS active_users
        FROM payments p
        JOIN users u ON p.user_ref = u.user_ref
        JOIN user_attributes ua ON u.user_ref = ua.user_ref
        WHERE p.payment_status = 'SUCCESS'
          AND ua.state = '{state}'
          AND p.payment_time >= TIMESTAMP '{month}'
          AND p.payment_time < TIMESTAMP '{next_month(month)}'
        GROUP BY ua.state
        """
    )


def render_retention_window(rng: random.Random) -> str:
    start_month = rng.choice(["2026-03-01", "2026-04-01"])
    end_month = "2026-06-01" if start_month == "2026-03-01" else "2026-07-01"
    return compact_sql(
        f"""
        WITH txns AS (
          SELECT
            p.user_ref,
            date_trunc('month', p.payment_time) AS month,
            count(*) AS successful_txns
          FROM payments p
          JOIN users u ON p.user_ref = u.user_ref
          WHERE p.payment_status = 'SUCCESS'
            AND p.payment_time >= TIMESTAMP '{start_month}'
            AND p.payment_time < TIMESTAMP '{end_month}'
          GROUP BY p.user_ref, month
        )
        SELECT count(*) AS retained_users
        FROM (
          SELECT user_ref
          FROM txns
          GROUP BY user_ref
          HAVING sum(successful_txns) >= 10
             AND count(DISTINCT month) = 3
        ) retained
        """
    )


def render_churn_by_segment(rng: random.Random) -> str:
    risk = pick_risk_band(rng)
    return compact_sql(
        f"""
        WITH prior_users AS (
          SELECT DISTINCT p.user_ref
          FROM payments p
          WHERE p.payment_status = 'SUCCESS'
            AND p.payment_time < TIMESTAMP '2026-06-01' - INTERVAL 15 DAY
        ),
        recent_users AS (
          SELECT DISTINCT p.user_ref
          FROM payments p
          WHERE p.payment_status = 'SUCCESS'
            AND p.payment_time >= TIMESTAMP '2026-06-01' - INTERVAL 15 DAY
        )
        SELECT ua.risk_band, count(*) AS churned_users
        FROM prior_users pu
        JOIN user_attributes ua ON pu.user_ref = ua.user_ref
        LEFT JOIN recent_users ru ON pu.user_ref = ru.user_ref
        WHERE ru.user_ref IS NULL
          AND ua.risk_band = '{risk}'
        GROUP BY ua.risk_band
        """
    )


def render_order_payment_funnel(rng: random.Random) -> str:
    product_area = pick_product_area(rng)
    month = pick_month(rng)
    return compact_sql(
        f"""
        SELECT
          o.product_area,
          count(DISTINCT o.order_ref) AS orders_created,
          count(DISTINCT p.payment_ref) AS payment_attempts,
          count(DISTINCT CASE WHEN p.payment_status = 'SUCCESS' THEN p.payment_ref END) AS successful_payments
        FROM orders o
        LEFT JOIN payments p ON o.order_ref = p.order_ref
        WHERE o.product_area = '{product_area}'
          AND o.order_time >= TIMESTAMP '{month}'
          AND o.order_time < TIMESTAMP '{next_month(month)}'
        GROUP BY o.product_area
        """
    )


def render_rail_risk_performance(rng: random.Random) -> str:
    rail = pick_rail(rng)
    risk = pick_risk_band(rng)
    return compact_sql(
        f"""
        SELECT
          pa.rail_type,
          ua.risk_band,
          sum(CASE WHEN p.payment_status = 'SUCCESS' THEN p.amount ELSE 0 END) AS successful_volume,
          count(*) AS attempts
        FROM payments p
        JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref
        JOIN user_attributes ua ON p.user_ref = ua.user_ref
        WHERE pa.rail_type = '{rail}'
          AND ua.risk_band = '{risk}'
        GROUP BY pa.rail_type, ua.risk_band
        """
    )


def render_kyc_success_rate(rng: random.Random) -> str:
    state = pick_state(rng)
    return compact_sql(
        f"""
        SELECT
          ua.kyc_status,
          sum(CASE WHEN p.payment_status = 'SUCCESS' THEN 1 ELSE 0 END)::DOUBLE / count(*) AS psr
        FROM payments p
        JOIN users u ON p.user_ref = u.user_ref
        JOIN user_attributes ua ON u.user_ref = ua.user_ref
        WHERE ua.state = '{state}'
        GROUP BY ua.kyc_status
        ORDER BY ua.kyc_status
        """
    )


def render_checkout_surface_volume(rng: random.Random) -> str:
    surface = pick_surface(rng)
    return compact_sql(
        f"""
        SELECT
          o.checkout_surface,
          pa.rail_type,
          sum(CASE WHEN p.payment_status = 'SUCCESS' THEN p.amount ELSE 0 END) AS tpv
        FROM orders o
        JOIN payments p ON o.order_ref = p.order_ref
        JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref
        WHERE o.checkout_surface = '{surface}'
        GROUP BY o.checkout_surface, pa.rail_type
        """
    )


def render_payment_metrics_by_user_segment_and_auth(rng: random.Random) -> str:
    state = pick_state(rng)
    risk = pick_risk_band(rng)
    month = pick_month(rng)
    return compact_sql(
        f"""
        SELECT
          o.checkout_surface,
          pa.authentication_mode,
          sum(CASE WHEN p.payment_status = 'SUCCESS' THEN p.amount ELSE 0 END) AS tpv,
          sum(CASE WHEN p.payment_status = 'SUCCESS' THEN 1 ELSE 0 END) AS successful_payments,
          count(*) AS total_payments,
          sum(CASE WHEN p.payment_status = 'SUCCESS' THEN 1 ELSE 0 END)::DOUBLE / nullif(count(*), 0) AS psr
        FROM payments p
        JOIN orders o ON p.order_ref = o.order_ref
        JOIN user_attributes ua ON p.user_ref = ua.user_ref
        JOIN users u ON p.user_ref = u.user_ref
        JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref
        WHERE p.payment_time >= TIMESTAMP '{month}'
          AND p.payment_time < TIMESTAMP '{next_month(month)}'
          AND ua.risk_band = '{risk}'
          AND ua.state = '{state}'
          AND ua.kyc_status = 'verified'
          AND u.account_state = 'active'
        GROUP BY o.checkout_surface, pa.authentication_mode
        HAVING count(*) >= 5
        ORDER BY tpv DESC
        """
    )


def render_merchant_plan_performance(rng: random.Random) -> str:
    gender = pick_gender(rng)
    return compact_sql(
        f"""
        SELECT
          u.platform_plan,
          ua.gender,
          count(DISTINCT p.user_ref) AS active_users,
          sum(CASE WHEN p.payment_status = 'SUCCESS' THEN p.amount ELSE 0 END) AS tpv
        FROM users u
        JOIN user_attributes ua ON u.user_ref = ua.user_ref
        JOIN payments p ON u.user_ref = p.user_ref
        WHERE ua.gender = '{gender}'
        GROUP BY u.platform_plan, ua.gender
        ORDER BY tpv DESC
        """
    )


def make_success_record(
    *,
    index: int,
    sql: str,
    rng: random.Random,
    start_base: datetime,
) -> dict[str, object]:
    return make_record(
        index=index,
        statement_text=sql,
        execution_status="FINISHED",
        error_message=None,
        rng=rng,
        start_base=start_base,
    )


def make_non_success_record(
    index: int,
    template: QueryTemplate,
    rng: random.Random,
    start_base: datetime,
) -> dict[str, object]:
    return make_record(
        index=index,
        statement_text=template.render(rng),
        execution_status=template.failure_status or "FAILED",
        error_message=template.error_message,
        rng=rng,
        start_base=start_base,
    )


def make_record(
    *,
    index: int,
    statement_text: str,
    execution_status: str,
    error_message: str | None,
    rng: random.Random,
    start_base: datetime,
) -> dict[str, object]:
    duration = rng.randint(180, 12000)
    start_time = start_base + timedelta(minutes=index * rng.randint(1, 7))
    end_time = start_time + timedelta(milliseconds=duration)
    produced_rows = rng.randint(1, 500)
    read_rows = rng.randint(1000, 250000)
    return {
        "account_id": ACCOUNT_ID,
        "workspace_id": WORKSPACE_ID,
        "statement_id": f"fintech_stmt_{index:06d}",
        "session_id": f"fintech_session_{index // 12:05d}",
        "execution_status": execution_status,
        "executed_by": rng.choice(USER_NAMES),
        "executed_by_user_id": f"user-{rng.randint(1000, 9999)}",
        "statement_text": statement_text,
        "statement_type": "SELECT",
        "error_message": error_message,
        "client_application": rng.choice(CLIENT_APPS),
        "total_duration_ms": duration,
        "waiting_for_compute_duration_ms": rng.randint(10, 500),
        "waiting_at_capacity_duration_ms": rng.randint(0, 100),
        "execution_duration_ms": max(duration - rng.randint(10, 300), 1),
        "compilation_duration_ms": rng.randint(5, 120),
        "total_task_duration_ms": duration + rng.randint(0, 500),
        "result_fetch_duration_ms": rng.randint(5, 200),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "update_time": end_time.isoformat(),
        "read_rows": read_rows,
        "read_bytes": read_rows * rng.randint(40, 160),
        "produced_rows": produced_rows,
        "read_files": rng.randint(1, 10),
        "read_partitions": rng.randint(1, 16),
        "read_io_cache_percent": round(rng.random() * 100, 2),
        "from_result_cache": rng.choice([True, False]),
        "pruned_files": rng.randint(0, 5),
        "pruned_bytes": rng.randint(0, 500000),
        "statement_parameters": json_cell({}),
        "compute": json_cell({"type": "WAREHOUSE", "warehouse_id": WAREHOUSE_ID}),
        "query_source": json_cell({"source": "synthetic_fintech_query_history"}),
        "query_parameters": json_cell({}),
        "query_tags": json_cell({"schema": "fintech_schema", "generator": "diracdata"}),
    }


def compact_sql(sql: str) -> str:
    return "\n".join(line.rstrip() for line in sql.strip().splitlines())


def json_cell(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def pick_rail(rng: random.Random) -> str:
    return rng.choice(RAILS)


def pick_state(rng: random.Random) -> str:
    return rng.choice(STATES)


def pick_gender(rng: random.Random) -> str:
    return rng.choice(GENDERS)


def pick_product_area(rng: random.Random) -> str:
    return rng.choice(PRODUCT_AREAS)


def pick_surface(rng: random.Random) -> str:
    return rng.choice(SURFACES)


def pick_risk_band(rng: random.Random) -> str:
    return rng.choice(RISK_BANDS)


def pick_month(rng: random.Random) -> str:
    return rng.choice(MONTH_STARTS)


def next_month(month: str) -> str:
    year, month_number, _day = [int(part) for part in month.split("-")]
    if month_number == 12:
        return f"{year + 1}-01-01"
    return f"{year}-{month_number + 1:02d}-01"


def _append_unique(pool: list[str], sql: str) -> None:
    if sql not in pool:
        pool.append(sql)


if __name__ == "__main__":
    main()
