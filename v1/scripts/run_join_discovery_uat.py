"""Run join discovery UAT from an existing learning profile artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env
from diracdata.learning import JoinablePairDiscovery, learning_collection_from_profile_artifact
from diracdata.learning.paths import learning_artifact_key
from diracdata.llms import chat_model_client_from_settings
from diracdata.storage import object_store_from_settings


DEFAULT_EXPECTED_PAIRS_BY_SCHEMA = {
    "main": [
        "store_sales.ss_sold_date_sk=date_dim.d_date_sk",
        "store_sales.ss_item_sk=item.i_item_sk",
        "store_sales.ss_customer_sk=customer.c_customer_sk",
        "customer.c_current_addr_sk=customer_address.ca_address_sk",
        "inventory.inv_item_sk=item.i_item_sk",
        "inventory.inv_warehouse_sk=warehouse.w_warehouse_sk",
        "web_sales.ws_web_site_sk=web_site.web_site_sk",
    ],
    "retail_analytics": [
        "online_purchases.billing_client_ref=clients.client_record",
        "online_purchases.sale_calendar_day_ref=calendar_days.calendar_day_record",
        "online_purchases.merchandise_ref=merchandise.merchandise_record",
        "clients.current_address_ref=addresses.address_record",
        "clients.current_client_profile_ref=client_profiles.client_profile_record",
        "marketing_campaigns.campaign_record=online_purchases.campaign_ref",
        "store_purchases.sale_calendar_day_ref=calendar_days.calendar_day_record",
        "store_purchases.merchandise_ref=merchandise.merchandise_record",
        "store_purchases.retail_location_ref=retail_locations.retail_location_record",
        "stock_levels.fulfillment_center_ref=fulfillment_centers.fulfillment_center_record",
        "stock_levels.merchandise_ref=merchandise.merchandise_record",
        "stock_levels.calendar_day_ref=calendar_days.calendar_day_record",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="uat_full_schema_20260606")
    parser.add_argument("--profile-artifact", default=None)
    parser.add_argument("--query-history-path", type=Path, default=None)
    parser.add_argument(
        "--expected-pair",
        action="append",
        default=None,
        help="Expected pair as table.column=table.column. May be repeated.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = settings_from_env(".env")
    object_store = object_store_from_settings(settings, create_bucket_if_missing=True)
    profile_artifact = args.profile_artifact or learning_artifact_key(
        settings,
        run_id=args.run_id,
        relative_path="profiles/table_profiles.json",
    )
    collection = learning_collection_from_profile_artifact(
        object_store=object_store,
        profile_artifact_key=profile_artifact,
    )
    discovery = JoinablePairDiscovery(
        settings=settings,
        object_store=object_store,
        llm_client=chat_model_client_from_settings(settings) if args.query_history_path else None,
        progress_callback=_progress,
    )

    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "profile_artifact": profile_artifact,
                "query_history_path": str(args.query_history_path) if args.query_history_path else None,
            },
            indent=2,
        ),
        flush=True,
    )
    result = discovery.discover(
        collection=collection,
        query_history_path=args.query_history_path,
    )
    rows = _read_jsonl(object_store.read_text(result.joinable_pairs_artifact_key))
    active_context_key = (
        f"artifacts/learning/{settings.catalog}/{settings.database}/{settings.schema}/"
        "active/contexts/learned_context.json"
    )
    active_context = object_store.read_json(active_context_key)
    if active_context.get("joinable_pairs_artifact_key") != result.joinable_pairs_artifact_key:
        raise AssertionError("Active context does not reference immutable joinable pairs artifact")
    expected_pairs = args.expected_pair or _default_expected_pairs(settings.schema)
    missing_expected = [
        pair
        for pair in expected_pairs
        if _canonical_pair_text(pair) not in {_row_pair_key(row) for row in rows}
    ]
    if missing_expected:
        raise AssertionError(f"Missing expected join pairs: {missing_expected}")

    summary = {
        "status": "passed",
        "run_id": result.run_id,
        "joinable_pair_count": result.pair_count,
        "query_history_unique_success_count": result.query_history_unique_success_count,
        "query_history_llm_batch_count": result.query_history_llm_batch_count,
        "profile_sample_candidate_count": result.profile_sample_candidate_count,
        "joinable_pairs_artifact": result.joinable_pairs_artifact_key,
        "active_joinable_pairs_artifact": result.active_joinable_pairs_artifact_key,
        "active_context_artifact": active_context_key,
        "expected_pairs_verified": expected_pairs,
        "sample_pairs": rows[:20],
    }
    print(json.dumps(summary, indent=2), flush=True)


def _read_jsonl(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _progress(message: str) -> None:
    print(f"[joins] {message}", flush=True)


def _default_expected_pairs(schema: str) -> list[str]:
    return DEFAULT_EXPECTED_PAIRS_BY_SCHEMA.get(
        schema,
        DEFAULT_EXPECTED_PAIRS_BY_SCHEMA["main"],
    )


def _row_pair_key(row: dict[str, object]) -> str:
    return _canonical_pair(
        str(row["left_table"]),
        str(row["left_column"]),
        str(row["right_table"]),
        str(row["right_column"]),
    )


def _canonical_pair_text(pair: str) -> str:
    left, right = pair.split("=", 1)
    left_table, left_column = left.split(".", 1)
    right_table, right_column = right.split(".", 1)
    return _canonical_pair(left_table, left_column, right_table, right_column)


def _canonical_pair(
    left_table: str,
    left_column: str,
    right_table: str,
    right_column: str,
) -> str:
    first, second = sorted([(left_table, left_column), (right_table, right_column)])
    return f"{first[0]}.{first[1]}={second[0]}.{second[1]}"


if __name__ == "__main__":
    main()
