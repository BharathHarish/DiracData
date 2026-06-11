"""Generate a compact fintech analytics schema for cheap learning/UAT runs."""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


DEFAULT_OUTPUT_DIR = Path("data/fintech_schema/parquet")
DEFAULT_LOCAL_CATALOG_OUTPUT = Path("conf/catalogs/fintech_schema.local.json")
DEFAULT_MINIO_CATALOG_OUTPUT = Path("conf/catalogs/fintech_schema.minio.json")
DEFAULT_BUSINESS_CONTEXT_OUTPUT = Path("conf/business_contexts/fintech_schema.json")
DEFAULT_BUSINESS_GROUNDING_OUTPUT = Path(
    "conf/business_grounding/fintech_pod.analytics.fintech_schema.yaml"
)
DEFAULT_MANIFEST_OUTPUT = Path("data/fintech_schema/manifest.json")
DEFAULT_CATALOG = "fintech_pod"
DEFAULT_DATABASE = "analytics"
DEFAULT_SCHEMA = "fintech_schema"
DEFAULT_LAKE_BUCKET = "lake"
DEFAULT_LAKE_PREFIX = "fintech_schema"
DEFAULT_USER_COUNT = 1000
DEFAULT_ORDER_COUNT = 15000
DEFAULT_PAYMENT_COUNT = 18000
DEFAULT_PAYMENT_ATTRIBUTE_COUNT = 1000
DEFAULT_SEED = 20260607
ANCHOR_DATE = date(2026, 6, 1)


TABLE_DESCRIPTIONS = {
    "users": "Razorpay merchant or user accounts that initiate orders and payments.",
    "user_attributes": "User demographic, location, risk, and KYC attributes for segmentation.",
    "orders": "Checkout orders created by users before payment attempts are made.",
    "payments": "Payment transaction attempts, including amount, status, rail, and event time.",
    "payment_attributes": "Payment rail and routing attributes such as UPI, cards, NEFT, and IMPS.",
}


@dataclass(frozen=True)
class GeneratedTable:
    name: str
    rows: int
    path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--local-catalog-output", type=Path, default=DEFAULT_LOCAL_CATALOG_OUTPUT)
    parser.add_argument("--minio-catalog-output", type=Path, default=DEFAULT_MINIO_CATALOG_OUTPUT)
    parser.add_argument("--business-context-output", type=Path, default=DEFAULT_BUSINESS_CONTEXT_OUTPUT)
    parser.add_argument("--business-grounding-output", type=Path, default=DEFAULT_BUSINESS_GROUNDING_OUTPUT)
    parser.add_argument("--manifest-output", type=Path, default=DEFAULT_MANIFEST_OUTPUT)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--lake-bucket", default=DEFAULT_LAKE_BUCKET)
    parser.add_argument("--lake-prefix", default=DEFAULT_LAKE_PREFIX)
    parser.add_argument("--users", type=int, default=DEFAULT_USER_COUNT)
    parser.add_argument("--orders", type=int, default=DEFAULT_ORDER_COUNT)
    parser.add_argument("--payments", type=int, default=DEFAULT_PAYMENT_COUNT)
    parser.add_argument("--payment-attributes", type=int, default=DEFAULT_PAYMENT_ATTRIBUTE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not args.force and any(args.output_dir.glob("*.parquet")):
        raise FileExistsError(f"{args.output_dir} already contains parquet files; use --force")

    frames = generate_frames(
        rng=rng,
        user_count=args.users,
        order_count=args.orders,
        payment_count=args.payments,
        payment_attribute_count=args.payment_attributes,
    )
    generated = write_parquet(frames=frames, output_dir=args.output_dir)
    write_catalog(
        output_path=args.local_catalog_output,
        catalog=args.catalog,
        database=args.database,
        schema=args.schema,
        table_paths={table.name: str(table.path) for table in generated},
    )
    write_catalog(
        output_path=args.minio_catalog_output,
        catalog=args.catalog,
        database=args.database,
        schema=args.schema,
        table_paths={
            table.name: f"s3://{args.lake_bucket}/{args.lake_prefix.strip('/')}/{table.name}.parquet"
            for table in generated
        },
    )
    write_business_context(args.business_context_output)
    write_business_grounding(
        output_path=args.business_grounding_output,
        catalog=args.catalog,
        database=args.database,
        schema=args.schema,
    )
    write_manifest(
        output_path=args.manifest_output,
        catalog=args.catalog,
        database=args.database,
        schema=args.schema,
        generated=generated,
        seed=args.seed,
    )
    print(
        json.dumps(
            {
                "status": "generated",
                "schema": args.schema,
                "tables": {table.name: table.rows for table in generated},
                "output_dir": str(args.output_dir),
                "local_catalog": str(args.local_catalog_output),
                "minio_catalog": str(args.minio_catalog_output),
                "business_context": str(args.business_context_output),
                "business_grounding": str(args.business_grounding_output),
            },
            indent=2,
        ),
        flush=True,
    )


def generate_frames(
    *,
    rng: random.Random,
    user_count: int,
    order_count: int,
    payment_count: int,
    payment_attribute_count: int,
) -> dict[str, pd.DataFrame]:
    users = generate_users(rng=rng, count=user_count)
    user_attributes = generate_user_attributes(rng=rng, user_refs=users["user_ref"].tolist())
    payment_attributes = generate_payment_attributes(rng=rng, count=payment_attribute_count)
    orders = generate_orders(rng=rng, user_refs=users["user_ref"].tolist(), count=order_count)
    payments = generate_payments(
        rng=rng,
        orders=orders,
        rail_refs=payment_attributes["rail_ref"].tolist(),
        count=payment_count,
    )
    return {
        "users": users,
        "user_attributes": user_attributes,
        "orders": orders,
        "payments": payments,
        "payment_attributes": payment_attributes,
    }


def generate_users(*, rng: random.Random, count: int) -> pd.DataFrame:
    merchant_types = ["startup", "smb", "mid_market", "enterprise", "platform"]
    acquisition_channels = ["self_serve", "sales", "partner", "marketplace", "referral"]
    platform_plans = ["starter", "growth", "scale", "enterprise"]
    rows = []
    for index in range(1, count + 1):
        rows.append(
            {
                "user_ref": f"user_{index:05d}",
                "signup_time": _random_datetime(rng, ANCHOR_DATE - timedelta(days=540), ANCHOR_DATE),
                "merchant_type": rng.choice(merchant_types),
                "acquisition_channel": rng.choice(acquisition_channels),
                "platform_plan": rng.choice(platform_plans),
                "account_state": rng.choices(
                    ["active", "restricted", "paused", "closed"],
                    weights=[88, 5, 5, 2],
                    k=1,
                )[0],
                "country": "India",
            }
        )
    return pd.DataFrame(rows)


def generate_user_attributes(*, rng: random.Random, user_refs: list[str]) -> pd.DataFrame:
    cities_by_state = {
        "Maharashtra": ["Mumbai", "Pune", "Nagpur"],
        "Karnataka": ["Bengaluru", "Mysuru", "Mangaluru"],
        "Delhi": ["New Delhi", "Dwarka", "Rohini"],
        "Tamil Nadu": ["Chennai", "Coimbatore", "Madurai"],
        "Telangana": ["Hyderabad", "Warangal", "Secunderabad"],
        "Gujarat": ["Ahmedabad", "Surat", "Vadodara"],
        "Rajasthan": ["Jaipur", "Udaipur", "Jodhpur"],
    }
    rows = []
    for user_ref in user_refs:
        state = rng.choice(list(cities_by_state))
        rows.append(
            {
                "user_ref": user_ref,
                "age": rng.randint(19, 68),
                "gender": rng.choices(["F", "M", "Other"], weights=[42, 56, 2], k=1)[0],
                "city": rng.choice(cities_by_state[state]),
                "state": state,
                "risk_band": rng.choices(["low", "medium", "high"], weights=[72, 22, 6], k=1)[0],
                "kyc_status": rng.choices(["verified", "pending", "rejected"], weights=[86, 11, 3], k=1)[0],
            }
        )
    return pd.DataFrame(rows)


def generate_payment_attributes(*, rng: random.Random, count: int) -> pd.DataFrame:
    rails = ["UPI", "CC", "DC", "NEFT", "IMPS", "NETBANKING", "WALLET"]
    issuers = ["HDFC", "ICICI", "SBI", "Axis", "Kotak", "YesBank", "NPCI", "RBL"]
    partners = ["rzp_router_a", "rzp_router_b", "rzp_router_c", "rzp_router_d"]
    rows = []
    for index in range(1, count + 1):
        rail_type = rng.choices(rails, weights=[45, 18, 12, 8, 8, 6, 3], k=1)[0]
        rows.append(
            {
                "rail_ref": f"rail_{index:04d}",
                "rail_type": rail_type,
                "issuer_name": rng.choice(issuers),
                "route_partner": rng.choice(partners),
                "settlement_speed": _settlement_speed(rail_type),
                "authentication_mode": _authentication_mode(rail_type),
                "risk_band": rng.choices(["low", "medium", "high"], weights=[68, 26, 6], k=1)[0],
            }
        )
    return pd.DataFrame(rows)


def generate_orders(*, rng: random.Random, user_refs: list[str], count: int) -> pd.DataFrame:
    product_areas = ["payments", "subscriptions", "payouts", "checkout", "marketplace"]
    surfaces = ["web", "mobile_sdk", "api", "dashboard", "plugin"]
    rows = []
    start = ANCHOR_DATE - timedelta(days=182)
    for index in range(1, count + 1):
        amount = round(rng.lognormvariate(3.8, 0.9) * 100, 2)
        rows.append(
            {
                "order_ref": f"order_{index:06d}",
                "user_ref": rng.choice(user_refs),
                "order_time": _random_datetime(rng, start, ANCHOR_DATE),
                "order_amount": min(amount, 250000.0),
                "order_state": rng.choices(
                    ["created", "paid", "cancelled", "expired"],
                    weights=[10, 78, 7, 5],
                    k=1,
                )[0],
                "checkout_surface": rng.choice(surfaces),
                "product_area": rng.choice(product_areas),
            }
        )
    return pd.DataFrame(rows)


def generate_payments(
    *,
    rng: random.Random,
    orders: pd.DataFrame,
    rail_refs: list[str],
    count: int,
) -> pd.DataFrame:
    order_rows = orders.to_dict("records")
    statuses = ["SUCCESS", "FAILED", "AUTHORIZED", "REFUNDED"]
    rows = []
    for index in range(1, count + 1):
        order = rng.choice(order_rows)
        payment_time = order["order_time"] + timedelta(minutes=rng.randint(0, 1440))
        status = rng.choices(statuses, weights=[78, 15, 5, 2], k=1)[0]
        amount_multiplier = rng.choice([1.0, 1.0, 1.0, 0.5])
        rows.append(
            {
                "payment_ref": f"pay_{index:07d}",
                "order_ref": order["order_ref"],
                "user_ref": order["user_ref"],
                "rail_ref": rng.choice(rail_refs),
                "payment_time": payment_time,
                "amount": round(float(order["order_amount"]) * amount_multiplier, 2),
                "payment_status": status,
            }
        )
    return pd.DataFrame(rows).sort_values("payment_time").reset_index(drop=True)


def write_parquet(*, frames: dict[str, pd.DataFrame], output_dir: Path) -> list[GeneratedTable]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    for table_name, frame in frames.items():
        output_path = output_dir / f"{table_name}.parquet"
        frame.to_parquet(output_path, index=False, compression="zstd")
        generated.append(GeneratedTable(table_name, len(frame), output_path))
    return generated


def write_catalog(
    *,
    output_path: Path,
    catalog: str,
    database: str,
    schema: str,
    table_paths: dict[str, str],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "catalog": catalog,
        "database": database,
        "schema": schema,
        "tables": [
            {
                "name": table_name,
                "path": table_paths[table_name],
                "format": "parquet",
                "description": TABLE_DESCRIPTIONS[table_name],
            }
            for table_name in sorted(table_paths)
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_business_context(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "text": (
            "Fintech analytics schema for Razorpay, one of India's largest payment platforms. "
            "The schema supports payment success, total payment volume, active users, retained "
            "users, churned users, payment rails such as UPI/cards/NEFT/IMPS, user demographics, "
            "and order-to-payment funnel analysis."
        ),
        "table_descriptions": TABLE_DESCRIPTIONS,
        "column_descriptions": {
            "payments": {
                "payment_ref": "Payment transaction attempt reference.",
                "order_ref": "Order reference that the payment attempt belongs to.",
                "user_ref": "User or merchant account that made the payment attempt.",
                "rail_ref": "Payment route or rail used by the transaction.",
                "payment_time": "Timestamp when the payment attempt happened.",
                "amount": "Transaction amount used for TPV when the payment is successful.",
                "payment_status": "Lifecycle state such as SUCCESS, FAILED, AUTHORIZED, or REFUNDED.",
            },
            "payment_attributes": {
                "rail_type": "Payment rail such as UPI, credit card, NEFT, IMPS, netbanking, or wallet.",
                "route_partner": "Razorpay route or processing partner for the payment.",
            },
            "user_attributes": {
                "state": "Indian state for geographic slicing.",
                "risk_band": "Risk segment for transaction monitoring.",
                "kyc_status": "KYC verification state for the user.",
            },
        },
        "glossary": {
            "TPV": "Total payment volume, calculated as the sum of successful payment amounts.",
            "PSR": "Payment success rate, calculated as successful payments divided by all payment attempts.",
            "rail": "Payment method or network, such as UPI, credit card, NEFT, or IMPS.",
            "active user": "A user with qualifying payment activity in a period.",
            "retained user": "A user with repeated monthly payment activity across the retention window.",
            "churned user": "A previously active user with no recent payment activity.",
        },
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_business_grounding(
    *,
    output_path: Path,
    catalog: str,
    database: str,
    schema: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": 1,
        "scope": {"catalog": catalog, "database": database, "schema": schema},
        "glossary": [
            {
                "id": "active_user",
                "term": "Active user",
                "synonyms": ["active merchant", "active account", "active payer"],
                "definition": "A user who has done at least one successful payment in the calendar month.",
                "tables": ["payments"],
                "columns": ["payments.user_ref", "payments.payment_time", "payments.payment_status"],
            },
            {
                "id": "dau",
                "term": "DAU",
                "synonyms": ["daily active users", "daily transacting users"],
                "definition": "A user who has done at least one successful payment on that calendar day.",
                "tables": ["payments"],
                "columns": ["payments.user_ref", "payments.payment_time", "payments.payment_status"],
            },
            {
                "id": "mau",
                "term": "MAU",
                "synonyms": ["monthly active users", "monthly transacting users"],
                "definition": "A user who has completed at least three successful payments in the calendar month.",
                "tables": ["payments"],
                "columns": ["payments.user_ref", "payments.payment_time", "payments.payment_status"],
            },
            {
                "id": "retained_user",
                "term": "Retained user",
                "synonyms": ["retained merchant", "repeat transacting user"],
                "definition": "A user with at least 10 successful transactions in the past 3 months and at least one successful transaction in every month of that window.",
                "tables": ["payments"],
                "columns": ["payments.user_ref", "payments.payment_time", "payments.payment_status"],
            },
            {
                "id": "churned_user",
                "term": "Churned user",
                "synonyms": ["lapsed user", "inactive user"],
                "definition": "A user who has not done any successful payment in the past 15 days.",
                "tables": ["payments"],
                "columns": ["payments.user_ref", "payments.payment_time", "payments.payment_status"],
            },
        ],
        "definitions": [
            {
                "id": "successful_payment",
                "name": "Successful payment",
                "definition": "A payment attempt whose payment_status is SUCCESS.",
                "tables": ["payments"],
                "columns": ["payments.payment_status"],
            },
            {
                "id": "payment_rail",
                "name": "Payment rail",
                "synonyms": ["payment method", "rail", "route", "UPI", "CC", "NEFT", "IMPS"],
                "definition": "The payment network or method used for a transaction, resolved through payments.rail_ref to payment_attributes.rail_ref.",
                "tables": ["payments", "payment_attributes"],
                "columns": ["payments.rail_ref", "payment_attributes.rail_ref", "payment_attributes.rail_type"],
            },
        ],
        "defaults": [
            {
                "id": "metrics_use_successful_payments",
                "applies_to": ["TPV", "active users", "DAU", "MAU", "retained users", "churned users"],
                "policy": "Use payment_status = 'SUCCESS' unless the user explicitly asks for all payment attempts.",
                "field": "payments.payment_status",
            },
            {
                "id": "calendar_month_from_payment_time",
                "applies_to": ["calendar month", "monthly", "MAU", "retention"],
                "policy": "Use date_trunc('month', payments.payment_time) for calendar-month grouping.",
                "field": "payments.payment_time",
            },
            {
                "id": "rail_slice_uses_payment_attributes",
                "applies_to": ["UPI", "credit card", "CC", "NEFT", "IMPS", "payment rail"],
                "policy": "Join payments to payment_attributes on rail_ref and slice by payment_attributes.rail_type.",
                "field": "payment_attributes.rail_type",
            },
        ],
        "metrics": [
            {
                "id": "tpv",
                "name": "TPV",
                "synonyms": ["total payment volume", "payment volume", "transaction volume"],
                "description": "Total payment volume is the sum of successful payment amounts for the requested period or slice.",
                "calculation": "SUM(payments.amount) where payments.payment_status = 'SUCCESS'",
                "parameterized_sql": {
                    "description": (
                        "Canonical governed SQL for TPV. Bind the parameters instead of "
                        "rewriting the metric definition."
                    ),
                    "parameters": [
                        {
                            "name": "start_time",
                            "type": "timestamp",
                            "required": True,
                            "description": "Inclusive lower bound on payments.payment_time.",
                        },
                        {
                            "name": "end_time",
                            "type": "timestamp",
                            "required": True,
                            "description": "Exclusive upper bound on payments.payment_time.",
                        },
                    ],
                    "sql": (
                        "SELECT\n"
                        "  SUM(CASE WHEN p.payment_status = 'SUCCESS' THEN p.amount ELSE 0 END) AS tpv\n"
                        "FROM payments p\n"
                        "WHERE p.payment_time >= {{ start_time }}\n"
                        "  AND p.payment_time < {{ end_time }}"
                    ),
                    "result_columns": [
                        {
                            "name": "tpv",
                            "semantic_type": "metric",
                            "description": "Sum of successful payment amounts in the requested time window.",
                        },
                    ],
                    "required_tables": ["payments"],
                    "required_columns": [
                        "payments.amount",
                        "payments.payment_status",
                        "payments.payment_time",
                    ],
                    "sql_contract": {
                        "aggregate": "sum",
                        "measure": "payments.amount",
                        "condition": {
                            "column": "payments.payment_status",
                            "operator": "=",
                            "value": "SUCCESS",
                        },
                        "time_column": "payments.payment_time",
                    },
                },
                "tables": ["payments"],
                "columns": ["payments.amount", "payments.payment_status", "payments.payment_time"],
            },
            {
                "id": "psr",
                "name": "PSR",
                "synonyms": ["payment success rate", "success rate"],
                "description": "Payment success rate is successful payment attempts divided by total payment attempts in the requested period.",
                "calculation": "COUNT_IF(payment_status = 'SUCCESS') / COUNT(*)",
                "parameterized_sql": {
                    "description": (
                        "Canonical governed SQL for payment success rate. Bind the parameters "
                        "instead of rewriting the metric definition."
                    ),
                    "parameters": [
                        {
                            "name": "start_time",
                            "type": "timestamp",
                            "required": True,
                            "description": "Inclusive lower bound on payments.payment_time.",
                        },
                        {
                            "name": "end_time",
                            "type": "timestamp",
                            "required": True,
                            "description": "Exclusive upper bound on payments.payment_time.",
                        },
                    ],
                    "sql": (
                        "SELECT\n"
                        "  SUM(CASE WHEN p.payment_status = 'SUCCESS' THEN 1 ELSE 0 END) AS successful_payments,\n"
                        "  COUNT(*) AS total_payments,\n"
                        "  SUM(CASE WHEN p.payment_status = 'SUCCESS' THEN 1 ELSE 0 END)::DOUBLE / NULLIF(COUNT(*), 0) AS psr\n"
                        "FROM payments p\n"
                        "WHERE p.payment_time >= {{ start_time }}\n"
                        "  AND p.payment_time < {{ end_time }}"
                    ),
                    "result_columns": [
                        {
                            "name": "successful_payments",
                            "semantic_type": "numerator",
                            "description": "Number of successful payment attempts in the requested time window.",
                        },
                        {
                            "name": "total_payments",
                            "semantic_type": "denominator",
                            "description": "Number of all payment attempts in the requested time window.",
                        },
                        {
                            "name": "psr",
                            "semantic_type": "metric",
                            "description": "Successful payment attempts divided by all payment attempts.",
                        },
                    ],
                    "required_tables": ["payments"],
                    "required_columns": [
                        "payments.payment_status",
                        "payments.payment_time",
                    ],
                    "sql_contract": {
                        "numerator": {
                            "aggregate": "count",
                            "condition": {
                                "column": "payments.payment_status",
                                "operator": "=",
                                "value": "SUCCESS",
                            },
                        },
                        "denominator": {
                            "aggregate": "count",
                            "grain": "payments.payment_ref",
                            "forbidden_base_filters": [
                                {
                                    "column": "payments.payment_status",
                                    "reason": (
                                        "PSR denominator is all payment attempts after user filters; "
                                        "do not filter payment_status in WHERE or base CTEs. "
                                        "Apply SUCCESS only inside numerator aggregates."
                                    ),
                                }
                            ],
                        },
                        "time_column": "payments.payment_time",
                    },
                },
                "tables": ["payments"],
                "columns": ["payments.payment_status", "payments.payment_time"],
            },
            {
                "id": "dau",
                "name": "DAU",
                "description": "Distinct users with at least one successful payment on the calendar day.",
                "calculation": "COUNT(DISTINCT user_ref) grouped by CAST(payment_time AS DATE)",
                "tables": ["payments"],
                "columns": ["payments.user_ref", "payments.payment_time", "payments.payment_status"],
            },
            {
                "id": "mau",
                "name": "MAU",
                "description": "Distinct users with at least three successful payments in the calendar month.",
                "calculation": "Count users whose successful payment count in a calendar month is at least 3.",
                "tables": ["payments"],
                "columns": ["payments.user_ref", "payments.payment_time", "payments.payment_status"],
            },
        ],
        "sql_templates": [
            {
                "id": "tpv_by_period_and_rail",
                "name": "TPV by period and rail",
                "description": "Use for total payment volume sliced by payment rail and time grain.",
                "required_tables": ["payments", "payment_attributes"],
                "join_path": [["payments.rail_ref", "payment_attributes.rail_ref"]],
                "sql": (
                    "SELECT date_trunc('{{ grain }}', p.payment_time) AS period, pa.rail_type, "
                    "SUM(p.amount) AS tpv "
                    "FROM payments p JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref "
                    "WHERE p.payment_status = 'SUCCESS' "
                    "AND p.payment_time >= {{ start_time }} AND p.payment_time < {{ end_time }} "
                    "GROUP BY period, pa.rail_type ORDER BY period, pa.rail_type"
                ),
            },
            {
                "id": "psr_by_period_and_rail",
                "name": "PSR by period and rail",
                "description": "Use for payment success rate sliced by UPI, CC, NEFT, IMPS, or other rails.",
                "required_tables": ["payments", "payment_attributes"],
                "join_path": [["payments.rail_ref", "payment_attributes.rail_ref"]],
                "sql": (
                    "SELECT date_trunc('{{ grain }}', p.payment_time) AS period, pa.rail_type, "
                    "SUM(CASE WHEN p.payment_status = 'SUCCESS' THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS psr, "
                    "SUM(CASE WHEN p.payment_status = 'SUCCESS' THEN 1 ELSE 0 END) AS successful_payments, "
                    "COUNT(*) AS total_payments "
                    "FROM payments p JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref "
                    "WHERE p.payment_time >= {{ start_time }} AND p.payment_time < {{ end_time }} "
                    "GROUP BY period, pa.rail_type ORDER BY period, pa.rail_type"
                ),
            },
            {
                "id": "mau_calendar_month",
                "name": "MAU calendar month",
                "description": "Use for monthly active users with at least three successful payments in a calendar month.",
                "required_tables": ["payments"],
                "sql": (
                    "WITH monthly_users AS ("
                    "SELECT date_trunc('month', payment_time) AS month, user_ref, COUNT(*) AS successful_txns "
                    "FROM payments WHERE payment_status = 'SUCCESS' "
                    "GROUP BY month, user_ref) "
                    "SELECT month, COUNT(*) AS mau FROM monthly_users "
                    "WHERE successful_txns >= 3 GROUP BY month ORDER BY month"
                ),
            },
            {
                "id": "retained_users_past_3_months",
                "name": "Retained users past 3 months",
                "description": "Use for users with at least 10 successful transactions in the last 3 months and activity in every month.",
                "required_tables": ["payments"],
                "sql": (
                    "WITH txns AS ("
                    "SELECT user_ref, date_trunc('month', payment_time) AS month, COUNT(*) AS txns "
                    "FROM payments WHERE payment_status = 'SUCCESS' "
                    "AND payment_time >= {{ start_month }} AND payment_time < {{ end_month }} "
                    "GROUP BY user_ref, month) "
                    "SELECT COUNT(*) AS retained_users FROM ("
                    "SELECT user_ref, SUM(txns) AS total_txns, COUNT(DISTINCT month) AS active_months "
                    "FROM txns GROUP BY user_ref "
                    "HAVING SUM(txns) >= 10 AND COUNT(DISTINCT month) = 3) retained"
                ),
            },
            {
                "id": "churned_users_15_days",
                "name": "Churned users past 15 days",
                "description": "Use for users with prior successful payment activity but no successful payment in the last 15 days.",
                "required_tables": ["payments"],
                "sql": (
                    "WITH prior_users AS ("
                    "SELECT DISTINCT user_ref FROM payments "
                    "WHERE payment_status = 'SUCCESS' AND payment_time < {{ anchor_time }} - INTERVAL 15 DAY), "
                    "recent_users AS ("
                    "SELECT DISTINCT user_ref FROM payments "
                    "WHERE payment_status = 'SUCCESS' AND payment_time >= {{ anchor_time }} - INTERVAL 15 DAY) "
                    "SELECT COUNT(*) AS churned_users FROM prior_users p "
                    "LEFT JOIN recent_users r ON p.user_ref = r.user_ref "
                    "WHERE r.user_ref IS NULL"
                ),
            },
        ],
        "ground_truth_sql": [],
    }
    output_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")


def write_manifest(
    *,
    output_path: Path,
    catalog: str,
    database: str,
    schema: str,
    generated: list[GeneratedTable],
    seed: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "catalog": catalog,
        "database": database,
        "schema": schema,
        "seed": seed,
        "anchor_date": ANCHOR_DATE.isoformat(),
        "tables": [
            {"name": table.name, "rows": table.rows, "path": str(table.path)}
            for table in generated
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _random_datetime(rng: random.Random, start: date, end: date) -> datetime:
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.min)
    seconds = int((end_dt - start_dt).total_seconds())
    return start_dt + timedelta(seconds=rng.randint(0, max(seconds, 1)))


def _settlement_speed(rail_type: str) -> str:
    if rail_type in {"UPI", "IMPS", "WALLET"}:
        return "instant"
    if rail_type in {"NEFT", "NETBANKING"}:
        return "same_day"
    return "t_plus_1"


def _authentication_mode(rail_type: str) -> str:
    if rail_type == "UPI":
        return "upi_pin"
    if rail_type in {"CC", "DC"}:
        return "otp_or_3ds"
    if rail_type in {"NEFT", "IMPS", "NETBANKING"}:
        return "bank_auth"
    return "wallet_auth"


if __name__ == "__main__":
    main()
