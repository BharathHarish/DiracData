#!/usr/bin/env python3
"""Evaluate NL-to-column recall for the retrieval layer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.retrieval.evaluator import evaluate_column_retrieval_from_files, write_column_retrieval_report  # noqa: E402
from diracdata_v2.retrieval.roberta_reranker import TransformersColumnReranker  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--semantic-catalog-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--top-ks", default="10,20,40")
    parser.add_argument("--candidate-pool-size", type=int, default=100)
    parser.add_argument("--reranker-path")
    parser.add_argument("--allow-model-download", action="store_true")
    args = parser.parse_args()

    top_ks = tuple(int(item.strip()) for item in str(args.top_ks).split(",") if item.strip())
    reranker = None
    if args.reranker_path:
        reranker = TransformersColumnReranker(
            model_path=Path(args.reranker_path),
            local_files_only=not args.allow_model_download,
        )
    report = evaluate_column_retrieval_from_files(
        questions_path=Path(args.questions),
        semantic_catalog_path=Path(args.semantic_catalog_path),
        top_ks=top_ks,
        candidate_pool_size=args.candidate_pool_size,
        reranker=reranker,
    )
    report_path = write_column_retrieval_report(report, Path(args.output_dir))
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
