"""Run a small learning flow against the configured catalog."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env
from diracdata.llms import chat_model_client_from_settings
from diracdata.learning import (
    BusinessContext,
    LearningPipeline,
)
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import object_store_from_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="learn_smoke")
    parser.add_argument(
        "--tables",
        default="",
        help="Comma-separated table names to profile. Empty or 'all' profiles the full catalog scope.",
    )
    parser.add_argument("--sample-limit", type=int, default=None)
    parser.add_argument("--distinct-limit", type=int, default=None)
    parser.add_argument("--description-column-batch-size", type=int, default=None)
    parser.add_argument("--query-history-path", type=Path, default=None)
    parser.add_argument(
        "--business-context-file",
        type=Path,
        default=None,
        help="JSON file with text, table_descriptions, column_descriptions, and glossary.",
    )
    parser.add_argument(
        "--business-context",
        default=(
            "Commerce analytics pod for sales, customers, items, dates, promotions, "
            "returns, and inventory analysis."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = settings_from_env(".env")
    object_store = object_store_from_settings(settings, create_bucket_if_missing=True)
    query_engine = query_engine_from_settings(settings)
    tables = _parse_tables(args.tables)
    business_context = _load_business_context(
        context_file=args.business_context_file,
        fallback_text=args.business_context,
    )

    try:
        pipeline = LearningPipeline(
            settings=settings,
            query_engine=query_engine,
            object_store=object_store,
            llm_client=chat_model_client_from_settings(settings),
            sample_limit=args.sample_limit,
            distinct_limit=args.distinct_limit,
            description_column_batch_size=args.description_column_batch_size,
        )
        result = pipeline.run(
            business_context=business_context,
            run_id=args.run_id,
            tables=tables,
            query_history_path=args.query_history_path,
        )
    finally:
        query_engine.close()

    print(f"Learning run: {result.collection.run_id}")
    print(f"Profile artifact: {result.collection.profile_artifact_key}")
    print(f"LLM context artifact: {result.collection.llm_context_artifact_key}")
    print(f"Metadata descriptions artifact: {result.description_artifact_key}")
    print(f"Joinable pairs artifact: {result.joinable_pairs_artifact_key}")
    print(f"Learned context artifact: {result.context.context_artifact_key}")
    for table in result.collection.table_profiles:
        print(f"{table.table_name}: {table.row_count:,} rows, sample={table.sample_artifact_key}")


def _parse_tables(raw_tables: str) -> list[str] | None:
    if raw_tables.strip() == "" or raw_tables.strip().lower() == "all":
        return None
    return [table.strip() for table in raw_tables.split(",") if table.strip()]


def _load_business_context(
    *,
    context_file: Path | None,
    fallback_text: str,
) -> BusinessContext:
    if context_file is not None:
        return BusinessContext.from_json_file(context_file)
    return BusinessContext(fallback_text)


if __name__ == "__main__":
    main()
