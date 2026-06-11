"""Joinable-pair discovery for learned schema context."""

from __future__ import annotations

import csv
import json
import re
import tempfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import duckdb

from diracdata.config.settings import DiracDataSettings
from diracdata.core.sql import quote_identifier, sql_string
from diracdata.learning.models import (
    ColumnProfile,
    JoinConfidence,
    JoinDiscoverySource,
    JoinablePair,
    LearningCollection,
    LearningScope,
    TableProfile,
    to_jsonable,
)
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.learning.query_history import QueryHistoryRecord, load_query_history_csv
from diracdata.llms import ChatModelClient, ChatModelMessage
from diracdata.storage.object_store import ObjectStore


PROMPT_PATH = Path(__file__).parent / "prompts" / "join_history_extraction.md"
SUCCESS_STATUSES = {"FINISHED", "SUCCESS", "SUCCEEDED"}
MEASURE_TOKENS = {
    "amount",
    "amt",
    "cost",
    "count",
    "cnt",
    "desc",
    "discount",
    "fee",
    "name",
    "paid",
    "price",
    "profit",
    "quantity",
    "rate",
    "sales",
    "tax",
    "value",
}
GENERIC_KEY_TOKENS = {"id", "key", "number", "sk"}


@dataclass(frozen=True)
class JoinDiscoveryResult:
    run_id: str
    joinable_pairs_artifact_key: str
    active_joinable_pairs_artifact_key: str
    pair_count: int
    query_history_unique_success_count: int
    query_history_llm_batch_count: int
    profile_sample_candidate_count: int


@dataclass(frozen=True)
class JoinCandidate:
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    source: JoinDiscoverySource


@dataclass(frozen=True)
class _RankedJoinablePair:
    pair: JoinablePair
    score: float


@dataclass(frozen=True)
class _ColumnEvidence:
    table_name: str
    column_name: str
    data_type: str
    row_count: int
    null_rate: float | None
    distinct_count: int | None
    distinct_values: set[str]
    top_values: set[str]
    sample_values: set[str]


class JoinablePairDiscovery:
    """Discover and publish joinable table-column pairs."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        object_store: ObjectStore,
        llm_client: ChatModelClient | None = None,
        prompt_path: Path = PROMPT_PATH,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.object_store = object_store
        self.llm_client = llm_client
        self.prompt_template = prompt_path.read_text(encoding="utf-8")
        self.progress_callback = progress_callback

    def discover(
        self,
        *,
        collection: LearningCollection,
        query_history_path: str | Path | None = None,
        query_history_records: list[QueryHistoryRecord] | None = None,
    ) -> JoinDiscoveryResult:
        records = query_history_records
        if records is None and query_history_path is not None:
            records = load_query_history_csv(query_history_path)

        index = _ProfileIndex.from_collection(collection, self.object_store)
        self._emit("step 1/2: query-history join extraction")
        history_candidates, unique_success_count, llm_batch_count = self._history_candidates(
            records=records or [],
            index=index,
        )
        self._emit("step 2/2: semantic profile/sample join discovery")
        sample_candidates = self._profile_sample_candidates(index)
        self._emit(
            f"join candidates: history={len(history_candidates)}, "
            f"profile_sample={len(sample_candidates)}"
        )
        ranked_pairs = self._validate_and_rank(
            candidates=[*history_candidates, *sample_candidates],
            index=index,
        )
        pairs = [ranked_pair.pair for ranked_pair in ranked_pairs]
        self._emit(f"validated joinable pairs: {len(pairs)}")

        run_key = learning_artifact_key(
            self.settings,
            run_id=collection.run_id,
            relative_path="joins/joinable_pairs.jsonl",
        )
        active_key = active_learning_artifact_key(
            self.settings,
            relative_path="joins/joinable_pairs.jsonl",
        )
        payload = _jsonl([to_jsonable(pair) for pair in pairs])
        self.object_store.write_text(run_key, payload, content_type="application/jsonl")
        self.object_store.write_text(active_key, payload, content_type="application/jsonl")
        self._update_active_manifest(
            run_id=collection.run_id,
            run_key=run_key,
            active_key=active_key,
            pair_count=len(pairs),
        )

        return JoinDiscoveryResult(
            run_id=collection.run_id,
            joinable_pairs_artifact_key=run_key,
            active_joinable_pairs_artifact_key=active_key,
            pair_count=len(pairs),
            query_history_unique_success_count=unique_success_count,
            query_history_llm_batch_count=llm_batch_count,
            profile_sample_candidate_count=len(sample_candidates),
        )

    def _history_candidates(
        self,
        *,
        records: list[QueryHistoryRecord],
        index: "_ProfileIndex",
    ) -> tuple[list[JoinCandidate], int, int]:
        if not records:
            return [], 0, 0
        if self.llm_client is None:
            raise ValueError("llm_client is required when query history is provided")

        unique_records = _dedupe_successful_queries(
            records,
            scoped_table_names=set(index.tables),
        )
        candidates: list[JoinCandidate] = []
        batches = _batches(unique_records, self.settings.join_history_llm_batch_size)
        for batch_index, batch in enumerate(batches, start=1):
            self._emit(
                f"extract query-history joins batch {batch_index}/{len(batches)}: "
                f"{len(batch)} exact-unique successful queries"
            )
            context = {
                "scope": to_jsonable(index.scope),
                "tables": index.schema_context(),
                "successful_queries": [
                    {
                        "statement_text": record["statement_text"],
                    }
                    for record in batch
                ],
            }
            prompt = self.prompt_template.replace(
                "{{join_history_context_json}}",
                json.dumps(context, indent=2, sort_keys=True),
            )
            response = self.llm_client.complete([ChatModelMessage(role="user", content=prompt)])
            candidates.extend(_parse_join_candidates(response))
        return candidates, len(unique_records), len(batches)

    def _profile_sample_candidates(self, index: "_ProfileIndex") -> list[JoinCandidate]:
        candidates: list[JoinCandidate] = []
        table_names = sorted(index.tables)
        for left_table_index, left_table in enumerate(table_names):
            for right_table in table_names[left_table_index + 1 :]:
                for left_column in index.tables[left_table].columns:
                    left = index.column(left_table, left_column.column_name)
                    if _is_measure_like(left.column_name):
                        continue
                    for right_column in index.tables[right_table].columns:
                        right = index.column(right_table, right_column.column_name)
                        if _is_measure_like(right.column_name):
                            continue
                        if not _type_compatible(left.data_type, right.data_type):
                            continue
                        name_similarity = _name_similarity(left.column_name, right.column_name)
                        if name_similarity < self.settings.join_name_similarity_min:
                            continue
                        candidates.append(
                            JoinCandidate(
                                left_table=left_table,
                                left_column=left.column_name,
                                right_table=right_table,
                                right_column=right.column_name,
                                source=JoinDiscoverySource.PROFILE_SAMPLE,
                            )
                        )
        return candidates

    def _validate_and_rank(
        self,
        *,
        candidates: list[JoinCandidate],
        index: "_ProfileIndex",
    ) -> list[_RankedJoinablePair]:
        grouped: dict[tuple[str, str, str, str], list[JoinCandidate]] = defaultdict(list)
        for candidate in candidates:
            if not index.has_column(candidate.left_table, candidate.left_column):
                continue
            if not index.has_column(candidate.right_table, candidate.right_column):
                continue
            left = index.column(candidate.left_table, candidate.left_column)
            right = index.column(candidate.right_table, candidate.right_column)
            if not _type_compatible(left.data_type, right.data_type):
                continue
            grouped[_canonical_key(candidate)].append(candidate)

        pairs: list[_RankedJoinablePair] = []
        for candidate_group in grouped.values():
            candidate = candidate_group[0]
            left = index.column(candidate.left_table, candidate.left_column)
            right = index.column(candidate.right_table, candidate.right_column)
            overlap = _value_overlap(left, right)
            name_similarity = _name_similarity(left.column_name, right.column_name)
            sources = sorted({item.source for item in candidate_group}, key=str)
            has_history = JoinDiscoverySource.QUERY_HISTORY in sources
            oriented_left, oriented_right = _orient_join(
                left,
                right,
                unique_tolerance=self.settings.join_key_unique_tolerance,
            )
            sample_join = _sample_join_evidence(
                object_store=self.object_store,
                left=oriented_left,
                right=oriented_right,
                left_sample_key=index.tables[oriented_left.table_name].sample_artifact_key,
                right_sample_key=index.tables[oriented_right.table_name].sample_artifact_key,
            )
            verified_by_sample = sample_join["sample_join_match_count"] >= (
                self.settings.join_sample_match_min
            )
            if not has_history and not verified_by_sample:
                continue
            join_type = _join_type(
                oriented_left,
                oriented_right,
                unique_tolerance=self.settings.join_key_unique_tolerance,
            )
            if not has_history and join_type == "many_to_many":
                continue

            score = _score_join(
                has_history=has_history,
                name_similarity=name_similarity,
                overlap=overlap,
                verified_by_sample=verified_by_sample,
                cardinality_bonus=_cardinality_bonus(
                    left,
                    right,
                    unique_tolerance=self.settings.join_key_unique_tolerance,
                ),
            )
            if score < self.settings.join_min_score:
                continue

            confidence = _confidence(score, has_history, verified_by_sample)
            pairs.append(
                _RankedJoinablePair(
                    pair=JoinablePair(
                        left_table=oriented_left.table_name,
                        left_column=oriented_left.column_name,
                        right_table=oriented_right.table_name,
                        right_column=oriented_right.column_name,
                        join_type=join_type,
                        confidence=confidence,
                    ),
                    score=round(score, 4),
                )
            )

        return sorted(
            pairs,
            key=lambda ranked_pair: (
                -ranked_pair.score,
                ranked_pair.pair.left_table,
                ranked_pair.pair.left_column,
                ranked_pair.pair.right_table,
                ranked_pair.pair.right_column,
            ),
        )

    def _update_active_manifest(
        self,
        *,
        run_id: str,
        run_key: str,
        active_key: str,
        pair_count: int,
    ) -> None:
        manifest_key = active_learning_artifact_key(self.settings, relative_path="manifest.json")
        if not self.object_store.exists(manifest_key):
            return
        manifest = self.object_store.read_json(manifest_key)
        if not isinstance(manifest, dict):
            return
        manifest.setdefault("immutable_artifacts", {})["joinable_pairs_artifact_key"] = run_key
        manifest.setdefault("active_artifacts", {})["joinable_pairs_artifact_key"] = active_key
        manifest["joinable_pair_count"] = pair_count
        manifest["active_run_id"] = run_id
        self.object_store.write_json(manifest_key, manifest)
        self._update_context_artifact(
            learning_artifact_key(
                self.settings,
                run_id=run_id,
                relative_path="contexts/learned_context.json",
            ),
            run_key=run_key,
            active_key=active_key,
            pair_count=pair_count,
        )
        self._update_context_artifact(
            active_learning_artifact_key(
                self.settings,
                relative_path="contexts/learned_context.json",
            ),
            run_key=run_key,
            active_key=active_key,
            pair_count=pair_count,
        )

    def _update_context_artifact(
        self,
        context_key: str,
        *,
        run_key: str,
        active_key: str,
        pair_count: int,
    ) -> None:
        if not self.object_store.exists(context_key):
            return
        context = self.object_store.read_json(context_key)
        if not isinstance(context, dict):
            return
        context["joinable_pairs_artifact_key"] = run_key
        metadata = context.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["joinable_pairs_artifact_key"] = run_key
            metadata["active_joinable_pairs_artifact_key"] = active_key
            metadata["joinable_pair_count"] = pair_count
        self.object_store.write_json(context_key, context)

    def _emit(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)


@dataclass(frozen=True)
class _ProfileIndex:
    scope: LearningScope
    tables: dict[str, TableProfile]
    columns: dict[tuple[str, str], _ColumnEvidence]

    @classmethod
    def from_collection(
        cls,
        collection: LearningCollection,
        object_store: ObjectStore,
    ) -> "_ProfileIndex":
        tables = {table.table_name: table for table in collection.table_profiles}
        columns = {}
        sample_values = _sample_values_by_column(collection, object_store)
        for table in collection.table_profiles:
            for column in table.columns:
                columns[(table.table_name, column.column_name)] = _ColumnEvidence(
                    table_name=table.table_name,
                    column_name=column.column_name,
                    data_type=column.data_type,
                    row_count=table.row_count,
                    null_rate=column.null_rate,
                    distinct_count=column.distinct_count,
                    distinct_values={_clean_value(value) for value in column.distinct_values},
                    top_values={_clean_value(item.get("value")) for item in column.top_values},
                    sample_values=sample_values.get((table.table_name, column.column_name), set()),
                )
        return cls(scope=collection.scope, tables=tables, columns=columns)

    def has_column(self, table_name: str, column_name: str) -> bool:
        return (table_name, column_name) in self.columns

    def column(self, table_name: str, column_name: str) -> _ColumnEvidence:
        return self.columns[(table_name, column_name)]

    def schema_context(self) -> list[dict[str, object]]:
        return [
            {
                "table_name": table.table_name,
                "columns": [
                    {
                        "column_name": column.column_name,
                        "data_type": column.data_type,
                    }
                    for column in table.columns
                ],
            }
            for table in sorted(self.tables.values(), key=lambda item: item.table_name)
        ]


def learning_collection_from_profile_artifact(
    *,
    object_store: ObjectStore,
    profile_artifact_key: str,
) -> LearningCollection:
    payload = object_store.read_json(profile_artifact_key)
    if not isinstance(payload, dict):
        raise ValueError("profile artifact must be a JSON object")
    scope_payload = payload["scope"]
    scope = LearningScope(
        catalog=scope_payload["catalog"],
        database=scope_payload["database"],
        schema=scope_payload["schema"],
    )
    table_profiles = [
        TableProfile(
            table_name=table["table_name"],
            row_count=int(table["row_count"]),
            sample_artifact_key=table["sample_artifact_key"],
            columns=[
                ColumnProfile(
                    table_name=column["table_name"],
                    column_name=column["column_name"],
                    data_type=column["data_type"],
                    null_count=column.get("null_count"),
                    null_rate=column.get("null_rate"),
                    distinct_count=column.get("distinct_count"),
                    min_value=column.get("min_value"),
                    max_value=column.get("max_value"),
                    top_values=column.get("top_values", []),
                    distinct_values=column.get("distinct_values", []),
                )
                for column in table["columns"]
            ],
        )
        for table in payload["tables"]
    ]
    run_id = str(payload["run_id"])
    return LearningCollection(
        run_id=run_id,
        scope=scope,
        table_profiles=table_profiles,
        profile_artifact_key=profile_artifact_key,
        llm_context_artifact_key=learning_artifact_key(
            DiracDataSettings(
                catalog=scope.catalog,
                database=scope.database,
                schema=scope.schema,
            ),
            run_id=run_id,
            relative_path="profiles/llm_context.json",
        ),
    )


def _dedupe_successful_queries(
    records: list[QueryHistoryRecord],
    *,
    scoped_table_names: set[str],
) -> list[dict[str, object]]:
    grouped: dict[str, None] = {}
    for record in records:
        if record.execution_status.upper() not in SUCCESS_STATUSES:
            continue
        statement_text = record.statement_text
        if not statement_text.strip():
            continue
        if _scoped_table_mention_count(statement_text, scoped_table_names) < 2:
            continue
        grouped.setdefault(statement_text, None)
    return [
        {
            "statement_text": statement_text,
        }
        for statement_text in grouped
    ]


def _parse_join_candidates(text: str) -> list[JoinCandidate]:
    payload = _parse_json_object(text)
    if not isinstance(payload, dict) or not isinstance(payload.get("join_candidates"), list):
        raise ValueError("join extraction response must contain join_candidates list")
    candidates = []
    for item in payload["join_candidates"]:
        if not isinstance(item, dict):
            continue
        candidates.append(
            JoinCandidate(
                left_table=str(item.get("left_table", "")),
                left_column=str(item.get("left_column", "")),
                right_table=str(item.get("right_table", "")),
                right_column=str(item.get("right_column", "")),
                source=JoinDiscoverySource.QUERY_HISTORY,
            )
        )
    return candidates


def _scoped_table_mention_count(sql: str, scoped_table_names: set[str]) -> int:
    normalized = sql.lower()
    return sum(
        1
        for table_name in scoped_table_names
        if re.search(rf"(?<![a-z0-9_]){re.escape(table_name.lower())}(?![a-z0-9_])", normalized)
    )


def _sample_values_by_column(
    collection: LearningCollection,
    object_store: ObjectStore,
) -> dict[tuple[str, str], set[str]]:
    values: dict[tuple[str, str], set[str]] = defaultdict(set)
    for table in collection.table_profiles:
        sample_text = object_store.read_text(table.sample_artifact_key)
        reader = csv.DictReader(sample_text.splitlines())
        for row in reader:
            for column_name, value in row.items():
                clean = _clean_value(value)
                if clean != "":
                    values[(table.table_name, column_name)].add(clean)
    return values


def _sample_join_evidence(
    *,
    object_store: ObjectStore,
    left: _ColumnEvidence,
    right: _ColumnEvidence,
    left_sample_key: str,
    right_sample_key: str,
) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as tmpdir:
        left_path = Path(tmpdir) / "left.csv"
        right_path = Path(tmpdir) / "right.csv"
        left_path.write_text(object_store.read_text(left_sample_key), encoding="utf-8")
        right_path.write_text(object_store.read_text(right_sample_key), encoding="utf-8")
        con = duckdb.connect(":memory:")
        try:
            con.execute(
                "CREATE VIEW left_sample AS "
                f"SELECT * FROM read_csv_auto({sql_string(left_path)}, header=true, all_varchar=true)"
            )
            con.execute(
                "CREATE VIEW right_sample AS "
                f"SELECT * FROM read_csv_auto({sql_string(right_path)}, header=true, all_varchar=true)"
            )
            join_count = con.execute(
                f"""
                SELECT count(*)
                FROM left_sample l
                JOIN right_sample r
                  ON l.{quote_identifier(left.column_name)} = r.{quote_identifier(right.column_name)}
                WHERE l.{quote_identifier(left.column_name)} IS NOT NULL
                  AND r.{quote_identifier(right.column_name)} IS NOT NULL
                  AND l.{quote_identifier(left.column_name)} != ''
                  AND r.{quote_identifier(right.column_name)} != ''
                """
            ).fetchone()[0]
            left_non_null = con.execute(
                f"""
                SELECT count(*)
                FROM left_sample
                WHERE {quote_identifier(left.column_name)} IS NOT NULL
                  AND {quote_identifier(left.column_name)} != ''
                """
            ).fetchone()[0]
            right_non_null = con.execute(
                f"""
                SELECT count(*)
                FROM right_sample
                WHERE {quote_identifier(right.column_name)} IS NOT NULL
                  AND {quote_identifier(right.column_name)} != ''
                """
            ).fetchone()[0]
        finally:
            con.close()
    return {
        "sample_join_match_count": int(join_count),
        "left_sample_non_null_count": int(left_non_null),
        "right_sample_non_null_count": int(right_non_null),
    }


def _value_overlap(left: _ColumnEvidence, right: _ColumnEvidence) -> float:
    left_values = left.distinct_values or left.top_values or left.sample_values
    right_values = right.distinct_values or right.top_values or right.sample_values
    if not left_values or not right_values:
        return 0.0
    return len(left_values & right_values) / min(len(left_values), len(right_values))


def _name_similarity(left: str, right: str) -> float:
    left_token_list = _name_tokens(left)
    right_token_list = _name_tokens(right)
    left_tokens = set(left_token_list)
    right_tokens = set(right_token_list)
    if not left_tokens or not right_tokens:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    sequence = SequenceMatcher(
        None,
        "_".join(left_token_list),
        "_".join(right_token_list),
    ).ratio()
    suffix = 1.0 if left_token_list[-1] == right_token_list[-1] else 0.0
    return (0.55 * jaccard) + (0.35 * sequence) + (0.10 * suffix)


def _name_tokens(name: str) -> list[str]:
    tokens = [token for token in name.lower().split("_") if token]
    if len(tokens) > 2 and len(tokens[0]) <= 4:
        tokens = tokens[1:]
    return [token for token in tokens if token not in {"current"}]


def _is_measure_like(column_name: str) -> bool:
    tokens = set(_name_tokens(column_name))
    if tokens & MEASURE_TOKENS:
        return True
    if tokens <= GENERIC_KEY_TOKENS:
        return False
    return False


def _type_compatible(left: str, right: str) -> bool:
    return _type_family(left) == _type_family(right)


def _type_family(data_type: str) -> str:
    normalized = data_type.upper()
    if any(token in normalized for token in ["INT", "DECIMAL", "DOUBLE", "FLOAT", "NUMBER"]):
        return "numeric"
    if "DATE" in normalized and "TIME" not in normalized:
        return "date"
    if "TIME" in normalized:
        return "time"
    if any(token in normalized for token in ["CHAR", "TEXT", "STRING", "VARCHAR"]):
        return "string"
    if "BOOL" in normalized:
        return "boolean"
    return normalized


def _cardinality_bonus(
    left: _ColumnEvidence,
    right: _ColumnEvidence,
    *,
    unique_tolerance: float,
) -> float:
    if _is_unique_like(left, unique_tolerance=unique_tolerance) or _is_unique_like(
        right,
        unique_tolerance=unique_tolerance,
    ):
        return 1.0
    return 0.0


def _is_unique_like(column: _ColumnEvidence, *, unique_tolerance: float) -> bool:
    if not column.distinct_count or not column.row_count:
        return False
    unique_ratio = column.distinct_count / column.row_count
    return unique_ratio >= 1.0 - unique_tolerance


def _orient_join(
    left: _ColumnEvidence,
    right: _ColumnEvidence,
    *,
    unique_tolerance: float,
) -> tuple[_ColumnEvidence, _ColumnEvidence]:
    left_unique = _is_unique_like(left, unique_tolerance=unique_tolerance)
    right_unique = _is_unique_like(right, unique_tolerance=unique_tolerance)
    if left_unique and not right_unique:
        return right, left
    return left, right


def _join_type(
    left: _ColumnEvidence,
    right: _ColumnEvidence,
    *,
    unique_tolerance: float,
) -> str:
    left_unique = _is_unique_like(left, unique_tolerance=unique_tolerance)
    right_unique = _is_unique_like(right, unique_tolerance=unique_tolerance)
    if left_unique and right_unique:
        return "one_to_one"
    if not left_unique and right_unique:
        return "many_to_one"
    if left_unique and not right_unique:
        return "one_to_many"
    return "many_to_many"


def _score_join(
    *,
    has_history: bool,
    name_similarity: float,
    overlap: float,
    verified_by_sample: bool,
    cardinality_bonus: float,
) -> float:
    history_score = 0.45 if has_history else 0.0
    sample_score = 0.15 if verified_by_sample else 0.0
    return min(
        1.0,
        history_score
        + (0.20 * name_similarity)
        + (0.10 * min(overlap, 1.0))
        + (0.10 * cardinality_bonus)
        + sample_score,
    )


def _confidence(score: float, has_history: bool, verified_by_sample: bool) -> JoinConfidence:
    if score >= 0.75 and (has_history or verified_by_sample):
        return JoinConfidence.HIGH
    if score >= 0.55:
        return JoinConfidence.MEDIUM
    return JoinConfidence.LOW


def _canonical_key(candidate: JoinCandidate) -> tuple[str, str, str, str]:
    left = (candidate.left_table, candidate.left_column)
    right = (candidate.right_table, candidate.right_column)
    first, second = sorted([left, right])
    return first[0], first[1], second[0], second[1]


def _clean_value(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _batches(items: list[dict[str, object]], batch_size: int) -> list[list[dict[str, object]]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


def _parse_json_object(text: str) -> object:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}") + 1
        if start < 0 or end <= start:
            raise
        return json.loads(stripped[start:end])


def _jsonl(rows: list[object]) -> str:
    if not rows:
        return ""
    return "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
