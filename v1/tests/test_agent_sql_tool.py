from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.tools.sql_tools import build_sql_tools, validate_read_only_sql
from diracdata.config.settings import DiracDataSettings
from diracdata.query_engines.duckdb_runtime import QueryResult


class RecordingQueryEngine:
    def __init__(self) -> None:
        self.last_max_rows: int | None = None

    def list_tables(self) -> list[str]:
        return ["customer"]

    def describe_table(self, table_name: str) -> list[object]:
        return []

    def row_count(self, table_name: str) -> int:
        return 0

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        self.last_max_rows = max_rows
        return QueryResult(columns=["total"], rows=[(42,)])

    def close(self) -> None:
        pass


class NonThreadSafeQueryEngine(RecordingQueryEngine):
    def __init__(self) -> None:
        super().__init__()
        self._guard = threading.Lock()
        self._inside_engine = False
        self.concurrent_entries = 0

    def list_tables(self) -> list[str]:
        self._enter_engine()
        return ["customer"]

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        self._enter_engine()
        return super().query(sql, max_rows=max_rows)

    def _enter_engine(self) -> None:
        with self._guard:
            if self._inside_engine:
                self.concurrent_entries += 1
            self._inside_engine = True
        try:
            time.sleep(0.01)
        finally:
            with self._guard:
                self._inside_engine = False


class AgentSqlToolTest(unittest.TestCase):
    def test_validate_read_only_sql_accepts_select_and_cte(self) -> None:
        result = validate_read_only_sql(
            "WITH x AS (SELECT * FROM customer) SELECT count(*) FROM x",
            available_tables={"customer"},
            sql_dialect="duckdb",
        )

        self.assertEqual(result["status"], "ok")

    def test_validate_read_only_sql_rejects_writes_and_unknown_tables(self) -> None:
        drop_result = validate_read_only_sql(
            "DROP TABLE customer",
            available_tables={"customer"},
            sql_dialect="duckdb",
        )
        unknown_result = validate_read_only_sql(
            "SELECT * FROM orders",
            available_tables={"customer"},
            sql_dialect="duckdb",
        )
        external_result = validate_read_only_sql(
            "SELECT * FROM read_parquet('/tmp/file.parquet')",
            available_tables={"customer"},
            sql_dialect="duckdb",
        )

        self.assertEqual(drop_result["status"], "error")
        self.assertEqual(unknown_result["status"], "error")
        self.assertEqual(external_result["status"], "error")

    def test_run_sql_tool_uses_env_default_when_max_rows_omitted(self) -> None:
        engine = RecordingQueryEngine()
        settings = DiracDataSettings(agent_sql_max_rows=17)
        run_sql_tool = build_sql_tools(settings=settings, query_engine=engine)[0]

        result = run_sql_tool.invoke({"sql": "SELECT count(*) AS total FROM customer"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(engine.last_max_rows, 17)
        self.assertEqual(result["rows"], [{"total": 42}])

    def test_run_sql_tool_allows_per_call_max_rows(self) -> None:
        engine = RecordingQueryEngine()
        settings = DiracDataSettings(agent_sql_max_rows=17)
        run_sql_tool = build_sql_tools(settings=settings, query_engine=engine)[0]

        result = run_sql_tool.invoke(
            {"sql": "SELECT count(*) AS total FROM customer", "max_rows": 3}
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(engine.last_max_rows, 3)

    def test_run_sql_tool_strips_trailing_semicolons(self) -> None:
        engine = RecordingQueryEngine()
        settings = DiracDataSettings(agent_sql_max_rows=17)
        run_sql_tool = build_sql_tools(settings=settings, query_engine=engine)[0]

        result = run_sql_tool.invoke({"sql": "SELECT count(*) AS total FROM customer;;"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sql"], "SELECT count(*) AS total FROM customer")

    def test_run_sql_tool_returns_probe_metadata(self) -> None:
        engine = RecordingQueryEngine()
        settings = DiracDataSettings(agent_sql_max_rows=17)
        run_sql_tool = build_sql_tools(settings=settings, query_engine=engine)[0]

        result = run_sql_tool.invoke(
            {
                "sql": "SELECT count(*) AS total FROM customer",
                "purpose": "probe",
                "check_name": "base population",
            }
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["purpose"], "probe")
        self.assertEqual(result["check_name"], "base_population")

    def test_run_sql_tool_serializes_shared_query_engine_access(self) -> None:
        engine = NonThreadSafeQueryEngine()
        settings = DiracDataSettings(agent_sql_max_rows=17)
        run_sql_tool = build_sql_tools(settings=settings, query_engine=engine)[0]

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda _: run_sql_tool.invoke(
                        {"sql": "SELECT count(*) AS total FROM customer"}
                    ),
                    range(16),
                )
            )

        self.assertTrue(all(result["status"] == "ok" for result in results))
        self.assertEqual(engine.concurrent_entries, 0)


if __name__ == "__main__":
    unittest.main()
