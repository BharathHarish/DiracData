#!/usr/bin/env python3
"""Compile a question into a compact semantic-catalog context packet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.semantic_catalog import SemanticCatalogCompiler  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-catalog", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--max-cards", type=int, default=24)
    parser.add_argument("--max-patterns", type=int, default=6)
    args = parser.parse_args()

    compiler = SemanticCatalogCompiler.from_file(Path(args.semantic_catalog))
    packet = compiler.compile(
        args.question,
        max_cards=args.max_cards,
        max_patterns=args.max_patterns,
    )
    print(json.dumps(packet.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
