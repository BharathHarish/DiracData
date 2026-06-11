"""Collect table samples and profiles for a selected schema."""

from __future__ import annotations

import csv
from collections.abc import Callable
from io import StringIO
from uuid import uuid4

from diracdata.config.settings import DiracDataSettings
from diracdata.core.sql import quote_identifier
from diracdata.learning.models import (
    BusinessContext,
    ColumnProfile,
    LearningCollection,
    LearningScope,
    TableProfile,
    to_jsonable,
)
from diracdata.learning.paths import learning_artifact_key
from diracdata.query_engines.base import QueryEngine
from diracdata.storage.object_store import ObjectStore


class SchemaLearningCollector:
    """Collect schema samples/profiles and persist learning artifacts."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        query_engine: QueryEngine,
        object_store: ObjectStore,
        sample_limit: int | None = None,
        distinct_limit: int | None = None,
        top_values_limit: int | None = None,
        context_distinct_values_limit: int | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.query_engine = query_engine
        self.object_store = object_store
        self.progress_callback = progress_callback
        self.sample_limit = sample_limit if sample_limit is not None else settings.learning_sample_limit
        self.distinct_limit = (
            distinct_limit if distinct_limit is not None else settings.learning_distinct_limit
        )
        self.top_values_limit = (
            top_values_limit
            if top_values_limit is not None
            else settings.learning_top_values_limit
        )
        self.context_distinct_values_limit = (
            context_distinct_values_limit
            if context_distinct_values_limit is not None
            else settings.learning_context_distinct_values_limit
        )

    def collect(
        self,
        *,
        business_context: BusinessContext,
        run_id: str | None = None,
        tables: list[str] | None = None,
    ) -> LearningCollection:
        run_id = run_id or self.settings.learning_run_id or f"learn-{uuid4().hex[:12]}"
        scope = LearningScope(
            catalog=self.settings.catalog,
            database=self.settings.database,
            schema=self.settings.schema,
        )
        selected_tables = tables or self.query_engine.list_tables()
        table_profiles = []
        for index, table_name in enumerate(selected_tables, start=1):
            self._emit(f"collect table {index}/{len(selected_tables)}: {table_name}")
            table_profiles.append(self._collect_table(table_name=table_name, run_id=run_id))

        profile_key = learning_artifact_key(
            self.settings,
            run_id=run_id,
            relative_path="profiles/table_profiles.json",
        )
        llm_context_key = learning_artifact_key(
            self.settings,
            run_id=run_id,
            relative_path="profiles/llm_context.json",
        )

        profile_payload = {
            "run_id": run_id,
            "scope": to_jsonable(scope),
            "tables": to_jsonable(table_profiles),
        }
        llm_context_payload = _build_llm_context_payload(
            run_id=run_id,
            scope=scope,
            business_context=business_context,
            table_profiles=table_profiles,
            context_distinct_values_limit=self.context_distinct_values_limit,
        )

        self.object_store.write_json(profile_key, profile_payload)
        self.object_store.write_json(llm_context_key, llm_context_payload)

        return LearningCollection(
            run_id=run_id,
            scope=scope,
            table_profiles=table_profiles,
            profile_artifact_key=profile_key,
            llm_context_artifact_key=llm_context_key,
        )

    def _collect_table(self, *, table_name: str, run_id: str) -> TableProfile:
        row_count = self.query_engine.row_count(table_name)
        self._emit(f"  row count {table_name}: {row_count:,}")
        sample_result = self.query_engine.query(
            f"SELECT * FROM {quote_identifier(table_name)}",
            max_rows=self.sample_limit,
        )
        sample_key = learning_artifact_key(
            self.settings,
            run_id=run_id,
            relative_path=f"samples/{table_name}.csv",
        )
        self.object_store.write_text(sample_key, _result_to_csv(sample_result.columns, sample_result.rows))
        self._emit(f"  wrote sample: {sample_key}")

        schema = self.query_engine.describe_table(table_name)
        self._emit(f"  profile columns {table_name}: {len(schema)}")
        columns = []
        for index, column in enumerate(schema, start=1):
            if index == 1 or index == len(schema) or index % 10 == 0:
                self._emit(f"    column {index}/{len(schema)}: {table_name}.{column.name}")
            columns.append(
                self._profile_column(
                    table_name=table_name,
                    column_name=column.name,
                    data_type=column.data_type,
                    row_count=row_count,
                )
            )
        return TableProfile(
            table_name=table_name,
            row_count=row_count,
            sample_artifact_key=sample_key,
            columns=columns,
        )

    def _emit(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    def _profile_column(
        self,
        *,
        table_name: str,
        column_name: str,
        data_type: str,
        row_count: int,
    ) -> ColumnProfile:
        table = quote_identifier(table_name)
        column = quote_identifier(column_name)
        null_count = self.query_engine.query(
            f"SELECT count(*) FROM {table} WHERE {column} IS NULL"
        ).rows[0][0]
        distinct_count = self.query_engine.query(
            f"SELECT count(DISTINCT {column}) FROM {table}"
        ).rows[0][0]
        null_rate = null_count / row_count if row_count else None

        min_value = None
        max_value = None
        if _supports_min_max(data_type):
            min_value, max_value = self.query_engine.query(
                f"SELECT min({column}), max({column}) FROM {table}"
            ).rows[0]

        top_values = [
            {"value": row[0], "count": row[1]}
            for row in self.query_engine.query(
                f"""
                SELECT {column} AS value, count(*) AS count
                FROM {table}
                GROUP BY {column}
                ORDER BY count DESC
                LIMIT {self.top_values_limit}
                """
            ).rows
        ]
        distinct_values = []
        if distinct_count <= self.distinct_limit:
            distinct_values = [
                row[0]
                for row in self.query_engine.query(
                    f"""
                    SELECT DISTINCT {column}
                    FROM {table}
                    ORDER BY {column}
                    LIMIT {self.distinct_limit}
                    """
                ).rows
            ]

        return ColumnProfile(
            table_name=table_name,
            column_name=column_name,
            data_type=data_type,
            null_count=null_count,
            null_rate=null_rate,
            distinct_count=distinct_count,
            min_value=min_value,
            max_value=max_value,
            top_values=top_values,
            distinct_values=distinct_values,
        )


def _result_to_csv(columns: list[str], rows: list[tuple[object, ...]]) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows(rows)
    return buffer.getvalue()


def _supports_min_max(data_type: str) -> bool:
    normalized = data_type.upper()
    return any(
        token in normalized
        for token in [
            "INT",
            "DOUBLE",
            "FLOAT",
            "DECIMAL",
            "DATE",
            "TIME",
            "CHAR",
            "VARCHAR",
            "BOOLEAN",
        ]
    )


def _build_llm_context_payload(
    *,
    run_id: str,
    scope: LearningScope,
    business_context: BusinessContext,
    table_profiles: list[TableProfile],
    context_distinct_values_limit: int,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "scope": to_jsonable(scope),
        "business_context": to_jsonable(business_context),
        "tables": [
            {
                "table_name": table.table_name,
                "row_count": table.row_count,
                "columns": [
                    {
                        "column_name": column.column_name,
                        "data_type": column.data_type,
                        "null_rate": column.null_rate,
                        "distinct_count": column.distinct_count,
                        "min_value": to_jsonable(column.min_value),
                        "max_value": to_jsonable(column.max_value),
                        "top_values": to_jsonable(column.top_values),
                        "distinct_values": to_jsonable(
                            column.distinct_values[:context_distinct_values_limit]
                        ),
                    }
                    for column in table.columns
                ],
            }
            for table in table_profiles
        ],
    }
