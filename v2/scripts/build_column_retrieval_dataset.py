#!/usr/bin/env python3
"""Build NL-to-column relevance pairs for schema-aware retrieval."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.evals.schema_benchmark import load_benchmark_cases  # noqa: E402
from diracdata_v2.retrieval import build_column_retrieval_pairs, column_cards_from_catalog, write_column_retrieval_pairs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--semantic-catalog-path", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--negatives-per-positive", type=int, default=4)
    parser.add_argument("--bm25-pool-size", type=int, default=80)
    args = parser.parse_args()

    cases = load_benchmark_cases(Path(args.questions))
    catalog = json.loads(Path(args.semantic_catalog_path).read_text(encoding="utf-8"))
    column_cards = column_cards_from_catalog(catalog)
    rows = build_column_retrieval_pairs(
        cases=cases,
        column_cards=column_cards,
        negatives_per_positive=args.negatives_per_positive,
        bm25_pool_size=args.bm25_pool_size,
    )
    output_path = write_column_retrieval_pairs(rows, Path(args.output_path))
    positives = sum(1 for row in rows if row.label == 1)
    negatives = sum(1 for row in rows if row.label == 0)
    print(
        json.dumps(
            {
                "status": "ok",
                "cases": len(cases),
                "column_cards": len(column_cards),
                "rows": len(rows),
                "positives": positives,
                "negatives": negatives,
                "output_path": str(output_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
