#!/usr/bin/env python3
"""Run the Phase 0 retail semantic-context baseline evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.evals.schema_benchmark import (  # noqa: E402
    evaluate_semantic_catalog_baseline,
    load_benchmark_cases,
    validate_benchmark_cases,
    write_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--metadata-descriptions-path", default=str(V2_ROOT / "context" / "retail_analytics_metadata_descriptions.json"))
    parser.add_argument("--semantic-catalog-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-cards", type=int, default=36)
    parser.add_argument("--max-patterns", type=int, default=8)
    parser.add_argument("--recall-ks", default="10,20,40")
    args = parser.parse_args()

    questions_path = Path(args.questions)
    metadata_path = Path(args.metadata_descriptions_path)
    catalog_path = Path(args.semantic_catalog_path)
    output_dir = Path(args.output_dir)
    recall_ks = tuple(
        int(item.strip())
        for item in str(args.recall_ks).split(",")
        if item.strip()
    )

    cases = load_benchmark_cases(questions_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    errors = validate_benchmark_cases(cases, metadata)
    if errors:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_count": len(errors),
                    "errors": errors,
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    report = evaluate_semantic_catalog_baseline(
        cases=cases,
        semantic_catalog_path=catalog_path,
        max_cards=args.max_cards,
        max_patterns=args.max_patterns,
        recall_ks=recall_ks,
    )
    report = type(report)(
        benchmark_path=str(questions_path),
        semantic_catalog_path=report.semantic_catalog_path,
        case_count=report.case_count,
        aggregate_scores=report.aggregate_scores,
        cases=report.cases,
    )
    report_path = write_report(report, output_dir)
    print(
        json.dumps(
            {
                "status": "ok",
                "case_count": report.case_count,
                "report_path": str(report_path),
                "aggregate_scores": report.aggregate_scores,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
