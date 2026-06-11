"""Build learned context graph and retrieval artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from diracdata.config.settings import DiracDataSettings
from diracdata.learning.models import LearningCollection, to_jsonable
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.learning.query_history import QueryHistoryRecord, load_query_history_csv
from diracdata.storage.object_store import ObjectStore


GROUNDING_JSON_RELATIVE_PATH = "grounding/business_grounding.json"
LIST_SECTIONS = (
    "glossary",
    "definitions",
    "defaults",
    "metrics",
    "sql_templates",
    "ground_truth_sql",
)
SECTION_NODE_TYPES = {
    "glossary": "business_term",
    "definitions": "business_definition",
    "defaults": "default_policy",
    "metrics": "metric",
    "sql_templates": "sql_template",
    "ground_truth_sql": "ground_truth_sql",
}


@dataclass(frozen=True)
class ContextGraphBuildResult:
    """Artifact keys produced by context graph learning."""

    run_id: str
    manifest_artifact_key: str
    active_manifest_artifact_key: str
    nodes_artifact_key: str
    edges_artifact_key: str
    query_patterns_artifact_key: str
    retrieval_documents_artifact_key: str
    bm25_index_artifact_key: str
    node_count: int
    edge_count: int
    query_pattern_count: int
    retrieval_document_count: int


class ContextGraphBuilder:
    """Create structural and retrieval artifacts from learned context inputs."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        object_store: ObjectStore,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.object_store = object_store
        self.progress_callback = progress_callback

    def build(
        self,
        *,
        collection: LearningCollection,
        description_artifact_key: str,
        joinable_pairs_artifact_key: str | None = None,
        business_grounding: dict[str, Any] | None = None,
        query_history_path: str | None = None,
        query_history_records: list[QueryHistoryRecord] | None = None,
    ) -> ContextGraphBuildResult:
        """Build graph, query-pattern, and deterministic retrieval artifacts."""
        descriptions = _as_dict(self.object_store.read_json(description_artifact_key))
        grounding = business_grounding if business_grounding is not None else self._load_active_grounding()
        joinable_pairs = self._load_joinable_pairs(joinable_pairs_artifact_key)
        records = query_history_records
        if records is None and query_history_path is not None:
            records = load_query_history_csv(query_history_path)

        self._emit("context graph: build schema and business nodes")
        nodes = _dedupe_by_id(
            [
                *_schema_nodes(collection=collection, descriptions=descriptions),
                *_business_grounding_nodes(grounding),
                *_join_pair_nodes(joinable_pairs),
            ]
        )
        self._emit("context graph: build graph edges")
        query_patterns = _query_patterns(records or [], collection=collection, joinable_pairs=joinable_pairs)
        edges = _dedupe_edges(
            [
                *_schema_edges(collection),
                *_join_edges(joinable_pairs),
                *_business_grounding_edges(grounding),
                *_query_pattern_edges(query_patterns),
            ]
        )
        nodes = _dedupe_by_id([*nodes, *_query_pattern_nodes(query_patterns)])

        self._emit("context graph: build retrieval documents")
        retrieval_documents = _retrieval_documents(
            collection=collection,
            descriptions=descriptions,
            grounding=grounding,
            joinable_pairs=joinable_pairs,
            query_patterns=query_patterns,
        )
        bm25_index = _bm25_plus_index(
            retrieval_documents,
            k1=self.settings.learning_bm25_k1,
            b=self.settings.learning_bm25_b,
            delta=self.settings.learning_bm25_delta,
        )

        keys = _artifact_keys(self.settings, collection.run_id)
        self._write_jsonl_pair(keys["nodes"], keys["active_nodes"], nodes)
        self._write_jsonl_pair(keys["edges"], keys["active_edges"], edges)
        self._write_jsonl_pair(
            keys["query_patterns"],
            keys["active_query_patterns"],
            query_patterns,
        )
        self._write_jsonl_pair(
            keys["retrieval_documents"],
            keys["active_retrieval_documents"],
            retrieval_documents,
        )
        self.object_store.write_json(keys["bm25_index"], bm25_index)
        self.object_store.write_json(keys["active_bm25_index"], bm25_index)

        cache_info = self._write_graph_cache(keys=keys, nodes=nodes, edges=edges)
        rrf_manifest = {
            "artifact_type": "rrf_manifest",
            "rrf_k": self.settings.learning_rrf_k,
            "ranking_inputs": ["bm25_plus", "vector_embeddings"],
            "rerank_scope": "columns_first",
            "table_retrieval_policy": "tables_are_containers_for_column_results_and_conflict_resolution",
            "bm25_index_artifact_key": keys["bm25_index"],
            "embedding_manifest_policy": (
                "Generated by the separate embedding learning step from retrieval/documents.jsonl."
            ),
        }
        self.object_store.write_json(keys["rrf_manifest"], rrf_manifest)
        self.object_store.write_json(keys["active_rrf_manifest"], rrf_manifest)

        manifest = {
            "artifact_type": "context_graph",
            "catalog": collection.scope.catalog,
            "database": collection.scope.database,
            "schema": collection.scope.schema,
            "run_id": collection.run_id,
            "built_at": datetime.now(UTC).isoformat(),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "query_pattern_count": len(query_patterns),
            "retrieval_document_count": len(retrieval_documents),
            "canonical_artifacts": {
                "nodes_artifact_key": keys["nodes"],
                "edges_artifact_key": keys["edges"],
                "query_patterns_artifact_key": keys["query_patterns"],
                "retrieval_documents_artifact_key": keys["retrieval_documents"],
                "bm25_index_artifact_key": keys["bm25_index"],
                "rrf_manifest_artifact_key": keys["rrf_manifest"],
                "context_graph_cache_artifact_key": cache_info["artifact_key"],
            },
            "active_artifacts": {
                "nodes_artifact_key": keys["active_nodes"],
                "edges_artifact_key": keys["active_edges"],
                "query_patterns_artifact_key": keys["active_query_patterns"],
                "events_artifact_key": keys["active_events"],
                "retrieval_documents_artifact_key": keys["active_retrieval_documents"],
                "bm25_index_artifact_key": keys["active_bm25_index"],
                "rrf_manifest_artifact_key": keys["active_rrf_manifest"],
                "context_graph_cache_artifact_key": cache_info["active_artifact_key"],
            },
            "graph_cache": cache_info,
        }
        self.object_store.write_json(keys["manifest"], manifest)
        self.object_store.write_json(keys["active_manifest"], manifest)
        if not self.object_store.exists(keys["active_events"]):
            self.object_store.write_text(
                keys["active_events"],
                "",
                content_type="application/jsonl",
            )
        self._update_active_manifest(keys=keys, context_manifest=manifest)

        return ContextGraphBuildResult(
            run_id=collection.run_id,
            manifest_artifact_key=keys["manifest"],
            active_manifest_artifact_key=keys["active_manifest"],
            nodes_artifact_key=keys["nodes"],
            edges_artifact_key=keys["edges"],
            query_patterns_artifact_key=keys["query_patterns"],
            retrieval_documents_artifact_key=keys["retrieval_documents"],
            bm25_index_artifact_key=keys["bm25_index"],
            node_count=len(nodes),
            edge_count=len(edges),
            query_pattern_count=len(query_patterns),
            retrieval_document_count=len(retrieval_documents),
        )

    def _load_active_grounding(self) -> dict[str, Any]:
        key = active_learning_artifact_key(self.settings, relative_path=GROUNDING_JSON_RELATIVE_PATH)
        if not self.object_store.exists(key):
            return {}
        return _as_dict(self.object_store.read_json(key))

    def _load_joinable_pairs(self, key: str | None) -> list[dict[str, Any]]:
        if key is None or not self.object_store.exists(key):
            return []
        return [json.loads(line) for line in self.object_store.read_text(key).splitlines() if line.strip()]

    def _write_jsonl_pair(
        self,
        immutable_key: str,
        active_key: str,
        rows: list[dict[str, Any]],
    ) -> None:
        payload = _jsonl(rows)
        self.object_store.write_text(immutable_key, payload, content_type="application/jsonl")
        self.object_store.write_text(active_key, payload, content_type="application/jsonl")

    def _write_graph_cache(
        self,
        *,
        keys: dict[str, str],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        cache_info = {
            "backend": "networkx",
            "available": False,
            "artifact_key": keys["graph_cache"],
            "active_artifact_key": keys["active_graph_cache"],
            "reason": None,
        }
        try:
            import networkx as nx  # type: ignore[import-not-found]
        except ImportError as exc:
            cache_info["reason"] = f"networkx unavailable: {exc}"
            return cache_info

        graph = nx.MultiDiGraph()
        for node in nodes:
            node_id = str(node["id"])
            graph.add_node(node_id, **{key: value for key, value in node.items() if key != "id"})
        for edge in edges:
            graph.add_edge(
                str(edge["source"]),
                str(edge["target"]),
                key=str(edge.get("edge_type", "")),
                **edge,
            )
        payload = pickle.dumps(graph)
        self.object_store.write_bytes(
            keys["graph_cache"],
            payload,
            content_type="application/octet-stream",
        )
        self.object_store.write_bytes(
            keys["active_graph_cache"],
            payload,
            content_type="application/octet-stream",
        )
        cache_info["available"] = True
        cache_info["reason"] = None
        return cache_info

    def _update_active_manifest(self, *, keys: dict[str, str], context_manifest: dict[str, Any]) -> None:
        active_manifest_key = active_learning_artifact_key(self.settings, relative_path="manifest.json")
        if not self.object_store.exists(active_manifest_key):
            return
        manifest = self.object_store.read_json(active_manifest_key)
        if not isinstance(manifest, dict):
            return
        manifest.setdefault("immutable_artifacts", {})["context_graph_manifest_artifact_key"] = (
            keys["manifest"]
        )
        manifest.setdefault("active_artifacts", {})["context_graph_manifest_artifact_key"] = (
            keys["active_manifest"]
        )
        manifest["context_graph"] = {
            "node_count": context_manifest["node_count"],
            "edge_count": context_manifest["edge_count"],
            "query_pattern_count": context_manifest["query_pattern_count"],
            "retrieval_document_count": context_manifest["retrieval_document_count"],
        }
        self.object_store.write_json(active_manifest_key, manifest)

    def _emit(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)


def _schema_nodes(
    *,
    collection: LearningCollection,
    descriptions: dict[str, Any],
) -> list[dict[str, Any]]:
    table_descriptions = _as_dict(descriptions.get("tables"))
    column_descriptions = _as_dict(descriptions.get("columns"))
    nodes: list[dict[str, Any]] = []
    for table in collection.table_profiles:
        table_description = _as_dict(table_descriptions.get(table.table_name))
        nodes.append(
            {
                "id": _table_id(table.table_name),
                "node_type": "table",
                "table_name": table.table_name,
                "row_count": table.row_count,
                "short_description": table_description.get("short_description"),
                "long_description": table_description.get("long_description"),
                "column_names": [column.column_name for column in table.columns],
            }
        )
        table_column_descriptions = _as_dict(column_descriptions.get(table.table_name))
        for column in table.columns:
            column_description = _as_dict(table_column_descriptions.get(column.column_name))
            nodes.append(
                {
                    "id": _column_id(table.table_name, column.column_name),
                    "node_type": "column",
                    "table_name": table.table_name,
                    "column_name": column.column_name,
                    "data_type": column.data_type,
                    "null_rate": column.null_rate,
                    "distinct_count": column.distinct_count,
                    "short_description": column_description.get("short_description"),
                    "long_description": column_description.get("long_description"),
                    "top_values": column.top_values,
                    "distinct_values": column.distinct_values,
                }
            )
    return nodes


def _schema_edges(collection: LearningCollection) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for table in collection.table_profiles:
        for column in table.columns:
            edges.append(
                _edge(
                    source=_table_id(table.table_name),
                    target=_column_id(table.table_name, column.column_name),
                    edge_type="table_has_column",
                    weight=1.0,
                )
            )
    return edges


def _join_pair_nodes(joinable_pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": _join_id(pair),
            "node_type": "join_pair",
            **pair,
        }
        for pair in joinable_pairs
    ]


def _join_edges(joinable_pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for pair in joinable_pairs:
        left = _column_id(str(pair["left_table"]), str(pair["left_column"]))
        right = _column_id(str(pair["right_table"]), str(pair["right_column"]))
        join_id = _join_id(pair)
        confidence = str(pair.get("confidence") or "low")
        weight = {"high": 1.0, "medium": 0.75, "low": 0.5}.get(confidence, 0.5)
        edges.extend(
            [
                _edge(left, right, "column_joins_column", weight, join_id=join_id, confidence=confidence),
                _edge(right, left, "column_joins_column", weight, join_id=join_id, confidence=confidence),
                _edge(join_id, left, "join_uses_column", 1.0),
                _edge(join_id, right, "join_uses_column", 1.0),
            ]
        )
    return edges


def _business_grounding_nodes(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for section in LIST_SECTIONS:
        node_type = SECTION_NODE_TYPES.get(section, section)
        for item in _as_list(grounding.get(section)):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or _stable_id(section, _compact_text(item)))
            nodes.append(
                {
                    "id": _business_id(section, item_id),
                    "node_type": node_type,
                    "section": section,
                    "business_id": item_id,
                    "name": item.get("term") or item.get("name") or item.get("question"),
                    "synonyms": item.get("synonyms") or [],
                    "description": _grounding_description(item),
                    "tables": _as_list(item.get("tables") or item.get("required_tables")),
                    "columns": _as_list(item.get("columns")),
                    "raw": item,
                }
            )
    return nodes


def _business_grounding_edges(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for section in LIST_SECTIONS:
        for item in _as_list(grounding.get(section)):
            if not isinstance(item, dict):
                continue
            item_id = _business_id(section, str(item.get("id") or _stable_id(section, _compact_text(item))))
            for table_name in _as_list(item.get("tables") or item.get("required_tables")):
                edges.append(_edge(item_id, _table_id(str(table_name)), f"{section}_uses_table", 0.85))
            for column_ref in _as_list(item.get("columns")):
                parsed = _parse_column_ref(str(column_ref))
                if parsed is not None:
                    edges.append(_edge(item_id, _column_id(*parsed), f"{section}_uses_column", 0.9))
            field_ref = item.get("field") or item.get("primary_key")
            if isinstance(field_ref, str):
                parsed = _parse_column_ref(field_ref)
                if parsed is not None:
                    edges.append(_edge(item_id, _column_id(*parsed), f"{section}_uses_column", 0.95))
            for left_ref, right_ref in _join_path_refs(item):
                left = _parse_column_ref(left_ref)
                right = _parse_column_ref(right_ref)
                if left is not None and right is not None:
                    edges.append(
                        _edge(
                            item_id,
                            _join_id_from_columns(left[0], left[1], right[0], right[1]),
                            f"{section}_uses_join",
                            0.95,
                        )
                    )
    return edges


def _query_patterns(
    records: list[QueryHistoryRecord],
    *,
    collection: LearningCollection,
    joinable_pairs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scoped_tables = {table.table_name for table in collection.table_profiles}
    seen: set[str] = set()
    patterns: list[dict[str, Any]] = []
    for record in records:
        if record.execution_status.upper() not in {"FINISHED", "SUCCESS", "SUCCEEDED"}:
            continue
        sql = record.statement_text.strip()
        if not sql or sql in seen:
            continue
        seen.add(sql)
        tables = _mentioned_tables(sql, scoped_tables)
        if len(tables) < 2:
            continue
        pattern_id = f"query_pattern:{_stable_id('sql', sql)}"
        used_joins = [
            _join_id(pair)
            for pair in joinable_pairs
            if str(pair.get("left_table")) in tables
            and str(pair.get("right_table")) in tables
            and str(pair.get("left_column")).lower() in sql.lower()
            and str(pair.get("right_column")).lower() in sql.lower()
        ]
        patterns.append(
            {
                "id": pattern_id,
                "node_type": "query_pattern",
                "statement_id": record.statement_id,
                "statement_type": record.statement_type,
                "tables": tables,
                "join_ids": sorted(set(used_joins)),
                "filters": _extract_filter_snippets(sql),
                "sql_hash": _stable_id("sql", sql),
                "statement_text": sql,
            }
        )
    return patterns


def _query_pattern_nodes(query_patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": pattern["id"],
            "node_type": "query_pattern",
            "tables": pattern["tables"],
            "join_ids": pattern["join_ids"],
            "filters": pattern["filters"],
            "sql_hash": pattern["sql_hash"],
        }
        for pattern in query_patterns
    ]


def _query_pattern_edges(query_patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for pattern in query_patterns:
        pattern_id = str(pattern["id"])
        for table_name in _as_list(pattern.get("tables")):
            edges.append(_edge(pattern_id, _table_id(str(table_name)), "query_pattern_used_table", 0.7))
        for join_id in _as_list(pattern.get("join_ids")):
            edges.append(_edge(pattern_id, str(join_id), "query_pattern_used_join", 0.8))
    return edges


def _retrieval_documents(
    *,
    collection: LearningCollection,
    descriptions: dict[str, Any],
    grounding: dict[str, Any],
    joinable_pairs: list[dict[str, Any]],
    query_patterns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    table_descriptions = _as_dict(descriptions.get("tables"))
    column_descriptions = _as_dict(descriptions.get("columns"))
    docs: list[dict[str, Any]] = []
    grounding_by_column = _grounding_text_by_column(grounding)
    for table in collection.table_profiles:
        table_desc = _as_dict(table_descriptions.get(table.table_name))
        docs.append(
            {
                "id": f"retrieval:table:{table.table_name}",
                "retrieval_type": "table_container",
                "source_type": "table",
                "table_name": table.table_name,
                "column_name": None,
                "text_for_bm25": _join_text(
                    table.table_name,
                    table_desc.get("short_description"),
                    table_desc.get("long_description"),
                    " ".join(column.column_name for column in table.columns),
                ),
                "text_for_embedding": None,
                "metadata": {
                    "node_id": _table_id(table.table_name),
                    "column_names": [column.column_name for column in table.columns],
                },
            }
        )
        table_column_descriptions = _as_dict(column_descriptions.get(table.table_name))
        for column in table.columns:
            column_desc = _as_dict(table_column_descriptions.get(column.column_name))
            grounding_text = grounding_by_column.get((table.table_name, column.column_name), "")
            docs.append(
                {
                    "id": f"retrieval:column:{table.table_name}.{column.column_name}",
                    "retrieval_type": "column",
                    "source_type": "column",
                    "table_name": table.table_name,
                    "column_name": column.column_name,
                    "text_for_bm25": _join_text(
                        table.table_name,
                        column.column_name,
                        column.data_type,
                        column_desc.get("short_description"),
                        column_desc.get("long_description"),
                        grounding_text,
                        _values_text(column.distinct_values),
                    ),
                    "text_for_embedding": _join_text(
                        table.table_name,
                        column.column_name,
                        column_desc.get("long_description"),
                        grounding_text,
                    ),
                    "metadata": {
                        "node_id": _column_id(table.table_name, column.column_name),
                        "data_type": column.data_type,
                    },
                }
            )
    docs.extend(_business_retrieval_documents(grounding))
    docs.extend(_join_retrieval_documents(joinable_pairs))
    docs.extend(_query_pattern_retrieval_documents(query_patterns))
    return docs


def _business_retrieval_documents(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for section in LIST_SECTIONS:
        for item in _as_list(grounding.get(section)):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or _stable_id(section, _compact_text(item)))
            docs.append(
                {
                    "id": f"retrieval:{section}:{item_id}",
                    "retrieval_type": section,
                    "source_type": section,
                    "table_name": None,
                    "column_name": None,
                    "text_for_bm25": _compact_text(item),
                    "text_for_embedding": _join_text(
                        item.get("term") or item.get("name") or item.get("question"),
                        _grounding_description(item),
                        " ".join(str(value) for value in _as_list(item.get("synonyms"))),
                    ),
                    "metadata": {"node_id": _business_id(section, item_id), "section": section},
                }
            )
    return docs


def _join_retrieval_documents(joinable_pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs = []
    for pair in joinable_pairs:
        docs.append(
            {
                "id": f"retrieval:{_join_id(pair)}",
                "retrieval_type": "join_pair",
                "source_type": "join_pair",
                "table_name": None,
                "column_name": None,
                "text_for_bm25": _join_text(
                    pair.get("left_table"),
                    pair.get("left_column"),
                    pair.get("right_table"),
                    pair.get("right_column"),
                    pair.get("join_type"),
                    pair.get("confidence"),
                ),
                "text_for_embedding": _join_text(
                    pair.get("left_table"),
                    pair.get("left_column"),
                    pair.get("right_table"),
                    pair.get("right_column"),
                ),
                "metadata": {"node_id": _join_id(pair)},
            }
        )
    return docs


def _query_pattern_retrieval_documents(query_patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"retrieval:{pattern['id']}",
            "retrieval_type": "query_pattern",
            "source_type": "query_pattern",
            "table_name": None,
            "column_name": None,
            "text_for_bm25": _join_text(
                " ".join(str(table) for table in _as_list(pattern.get("tables"))),
                " ".join(str(item) for item in _as_list(pattern.get("filters"))),
                pattern.get("statement_text"),
            ),
            "text_for_embedding": _join_text(
                " ".join(str(table) for table in _as_list(pattern.get("tables"))),
                " ".join(str(item) for item in _as_list(pattern.get("filters"))),
            ),
            "metadata": {
                "node_id": pattern["id"],
                "sql_hash": pattern.get("sql_hash"),
                "join_ids": pattern.get("join_ids", []),
            },
        }
        for pattern in query_patterns
    ]


def _bm25_plus_index(
    documents: list[dict[str, Any]],
    *,
    k1: float,
    b: float,
    delta: float,
) -> dict[str, Any]:
    tokenized_docs = []
    document_frequencies: Counter[str] = Counter()
    for document in documents:
        tokens = _tokens(str(document.get("text_for_bm25") or ""))
        counts = Counter(tokens)
        tokenized_docs.append(
            {
                "id": document["id"],
                "length": len(tokens),
                "term_frequencies": dict(sorted(counts.items())),
            }
        )
        document_frequencies.update(set(tokens))
    doc_count = len(tokenized_docs)
    avgdl = sum(doc["length"] for doc in tokenized_docs) / doc_count if doc_count else 0.0
    idf = {
        term: math.log((doc_count + 1) / (df + 0.5))
        for term, df in sorted(document_frequencies.items())
    }
    return {
        "artifact_type": "bm25_plus_index",
        "algorithm": "bm25_plus",
        "parameters": {"k1": k1, "b": b, "delta": delta},
        "document_count": doc_count,
        "average_document_length": avgdl,
        "documents": tokenized_docs,
        "idf": idf,
    }


def _artifact_keys(settings: DiracDataSettings, run_id: str) -> dict[str, str]:
    relative_paths = {
        "manifest": "context_graph/manifest.json",
        "nodes": "context_graph/nodes.jsonl",
        "edges": "context_graph/edges.jsonl",
        "query_patterns": "context_graph/query_patterns.jsonl",
        "graph_cache": "context_graph/context_graph.pkl",
        "retrieval_documents": "retrieval/documents.jsonl",
        "bm25_index": "retrieval/bm25_plus_index.json",
        "rrf_manifest": "retrieval/rrf_manifest.json",
    }
    keys = {
        name: learning_artifact_key(settings, run_id=run_id, relative_path=path)
        for name, path in relative_paths.items()
    }
    keys.update(
        {
            f"active_{name}": active_learning_artifact_key(settings, relative_path=path)
            for name, path in relative_paths.items()
        }
    )
    keys["active_events"] = active_learning_artifact_key(
        settings,
        relative_path="context_graph/events.jsonl",
    )
    return keys


def _grounding_text_by_column(grounding: dict[str, Any]) -> dict[tuple[str, str], str]:
    values: dict[tuple[str, str], list[str]] = {}
    for section in LIST_SECTIONS:
        for item in _as_list(grounding.get(section)):
            if not isinstance(item, dict):
                continue
            text = _join_text(
                item.get("term") or item.get("name") or item.get("question"),
                _grounding_description(item),
                " ".join(str(value) for value in _as_list(item.get("synonyms"))),
            )
            refs = list(_as_list(item.get("columns")))
            for field_name in ["field", "primary_key"]:
                field_ref = item.get(field_name)
                if isinstance(field_ref, str):
                    refs.append(field_ref)
            for column_ref in refs:
                parsed = _parse_column_ref(str(column_ref))
                if parsed is not None:
                    values.setdefault(parsed, []).append(text)
    return {key: " ".join(parts) for key, parts in values.items()}


def _edge(
    source: str,
    target: str,
    edge_type: str,
    weight: float,
    **metadata: Any,
) -> dict[str, Any]:
    return {
        "id": _stable_id("edge", source, target, edge_type, json.dumps(metadata, sort_keys=True)),
        "source": source,
        "target": target,
        "edge_type": edge_type,
        "weight": weight,
        **metadata,
    }


def _dedupe_by_id(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for row in rows:
        values[str(row["id"])] = row
    return [values[key] for key in sorted(values)]


def _dedupe_edges(edges: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    values: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for edge in edges:
        key = (
            str(edge["source"]),
            str(edge["target"]),
            str(edge["edge_type"]),
            str(edge.get("join_id") or ""),
        )
        if key not in values or float(edge.get("weight", 0)) > float(values[key].get("weight", 0)):
            values[key] = edge
    return sorted(values.values(), key=lambda edge: (edge["source"], edge["target"], edge["edge_type"]))


def _mentioned_tables(sql: str, table_names: set[str]) -> list[str]:
    normalized = sql.lower()
    return sorted(
        table_name
        for table_name in table_names
        if re.search(rf"(?<![a-z0-9_]){re.escape(table_name.lower())}(?![a-z0-9_])", normalized)
    )


def _extract_filter_snippets(sql: str) -> list[str]:
    snippets = re.findall(
        r"\b[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*\s*(?:=|>|<|>=|<=|IN)\s*(?:'[^']*'|\d+|\([^)]*\))",
        sql,
        flags=re.IGNORECASE,
    )
    return sorted(set(snippet.strip() for snippet in snippets))[:25]


def _join_path_refs(item: dict[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for pair in _as_list(item.get("join_path")):
        if isinstance(pair, list) and len(pair) == 2:
            refs.append((str(pair[0]), str(pair[1])))
        elif isinstance(pair, tuple) and len(pair) == 2:
            refs.append((str(pair[0]), str(pair[1])))
    return refs


def _parse_column_ref(value: str) -> tuple[str, str] | None:
    if "." not in value:
        return None
    table_name, column_name = value.split(".", 1)
    table_name = table_name.strip()
    column_name = column_name.strip()
    if not table_name or not column_name:
        return None
    return table_name, column_name


def _grounding_description(item: dict[str, Any]) -> str:
    for key in ["definition", "description", "policy", "calculation", "sql"]:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _compact_text(item)


def _compact_text(value: object) -> str:
    if isinstance(value, dict):
        parts = []
        for key in sorted(value):
            item = value[key]
            if isinstance(item, (str, int, float)):
                parts.append(str(item))
            elif isinstance(item, list):
                parts.append(" ".join(str(entry) for entry in item if isinstance(entry, (str, int, float))))
        return " ".join(part for part in parts if part)
    return str(value)


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(to_jsonable(row), sort_keys=True) for row in rows) + (
        "\n" if rows else ""
    )


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _values_text(values: list[Any]) -> str:
    return " ".join(str(value) for value in values[:25])


def _join_text(*parts: object) -> str:
    return " ".join(str(part).strip() for part in parts if part is not None and str(part).strip())


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _table_id(table_name: str) -> str:
    return f"table:{table_name}"


def _column_id(table_name: str, column_name: str) -> str:
    return f"column:{table_name}.{column_name}"


def _business_id(section: str, item_id: str) -> str:
    return f"{section}:{item_id}"


def _join_id(pair: dict[str, Any]) -> str:
    return _join_id_from_columns(
        str(pair["left_table"]),
        str(pair["left_column"]),
        str(pair["right_table"]),
        str(pair["right_column"]),
    )


def _join_id_from_columns(
    left_table: str,
    left_column: str,
    right_table: str,
    right_column: str,
) -> str:
    first, second = sorted([(left_table, left_column), (right_table, right_column)])
    return f"join:{first[0]}.{first[1]}={second[0]}.{second[1]}"
