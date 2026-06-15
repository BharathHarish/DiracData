#!/usr/bin/env python3
"""Train a RoBERTa-style cross-encoder for column relevance reranking."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.retrieval.roberta_reranker import train_roberta_reranker  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model-name", default="distilroberta-base")
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--validation-fraction", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--max-train-rows", type=int)
    args = parser.parse_args()

    try:
        manifest = train_roberta_reranker(
            pairs_path=Path(args.pairs_path),
            output_dir=Path(args.output_dir),
            model_name=args.model_name,
            local_files_only=not args.allow_model_download,
            max_length=args.max_length,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            validation_fraction=args.validation_fraction,
            seed=args.seed,
            max_train_rows=args.max_train_rows,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "hint": (
                        "If the base model is not cached locally, rerun with "
                        "--allow-model-download after approving network access."
                    ),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps({"status": "ok", "manifest": manifest}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
