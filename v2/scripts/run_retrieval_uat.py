#!/usr/bin/env python3
"""Run v2 retrieval tools without an LLM for quick UAT."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.tools import CandidateSearchService, SQLPatternSearchService, SchemaInfoService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument(
        "--metadata-descriptions-path",
        default=str(V2_ROOT / "context" / "retail_analytics_metadata_descriptions.json"),
    )
    parser.add_argument(
        "--schema-ast-path",
        default=str(V2_ROOT / "learning" / "artifacts" / "retail_analytics_v2_20260610" / "schema_ast.json"),
    )
    parser.add_argument(
        "--sql-library-path",
        default=str(V2_ROOT / "learning" / "artifacts" / "retail_analytics_patterns_v2_20260610" / "sql_library.json"),
    )
    parser.add_argument(
        "--retrieval-documents-path",
        default=str(
            ROOT
            / ".diracdata"
            / "artifacts"
            / "artifacts"
            / "learning"
            / "retail_pod"
            / "analytics"
            / "retail_analytics"
            / "active"
            / "retrieval"
            / "documents.jsonl"
        ),
    )
    parser.add_argument(
        "--column-embeddings-path",
        default=str(
            ROOT
            / ".diracdata"
            / "artifacts"
            / "artifacts"
            / "learning"
            / "retail_pod"
            / "analytics"
            / "retail_analytics"
            / "active"
            / "embeddings"
            / "column_embeddings.jsonl"
        ),
    )
    parser.add_argument("--search-term", action="append", dest="search_terms")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    pattern_service = SQLPatternSearchService.from_file(Path(args.sql_library_path))
    candidate_service = CandidateSearchService.from_files(
        schema_ast_path=Path(args.schema_ast_path),
        metadata_descriptions_path=Path(args.metadata_descriptions_path),
        retrieval_documents_path=Path(args.retrieval_documents_path) if args.retrieval_documents_path else None,
        column_embeddings_path=Path(args.column_embeddings_path) if args.column_embeddings_path else None,
    )
    schema_service = SchemaInfoService.from_file(Path(args.metadata_descriptions_path))
    result = {
        "query": args.query,
        "patterns": pattern_service.search(
            query=args.query,
            search_terms=args.search_terms,
            top_k=args.top_k,
        ),
        "candidates": candidate_service.search(
            query=args.query,
            search_terms=args.search_terms,
            top_k=args.top_k,
        ),
        "tables_preview": schema_service.get_tables()["tables"][:10],
    }
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
