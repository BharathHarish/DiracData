"""EngineContract: the assertions EVERY QueryEngine must satisfy. Subclass the mixin, set
`self.engine` (+ `table`/`expected_columns`) in setup. Run here against DuckDBEngine; each new
connector (Postgres/MySQL/Trino) reuses this same mixin so conformance is one contract, tested once.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from _engine_fixture import make_duckdb_source  # noqa: E402


class EngineContract:
    """Mixin (not a TestCase itself, so it is not collected standalone)."""

    engine = None
    table = "t"
    expected_columns = {"user_id", "seg", "amount", "note"}

    def test_identity(self):
        self.assertTrue(self.engine.name)
        self.assertTrue(self.engine.dialect)
        self.assertIsInstance(self.engine.read_only, bool)

    def test_list_tables(self):
        self.assertIn(self.table, self.engine.list_tables())

    def test_list_columns(self):
        self.assertTrue(self.expected_columns <= set(self.engine.list_columns(self.table)))

    def test_describe_columns(self):
        cols = self.engine.describe_columns(self.table)
        self.assertTrue(all("column_name" in c and "column_type" in c for c in cols))
        self.assertTrue(self.expected_columns <= {c["column_name"] for c in cols})

    def test_query_is_bounded(self):
        res = self.engine.query(f"SELECT * FROM {self.table}", 2)
        self.assertLessEqual(len(res.rows), 2)
        self.assertTrue(self.expected_columns <= set(res.columns))

    def test_nulls_preserved(self):
        res = self.engine.query(f"SELECT note FROM {self.table}", 10)
        self.assertIn(None, [r[0] for r in res.rows])

    def test_describe_query_types_without_running(self):
        d = self.engine.describe_query(f"SELECT user_id FROM {self.table}")
        self.assertEqual([c["column_name"] for c in d], ["user_id"])

    def test_to_parquet_full_result(self):
        out = Path(tempfile.mkdtemp()) / "o.parquet"
        n = self.engine.copy_to_parquet(f"SELECT * FROM {self.table}", str(out))
        self.assertEqual(n, 3)
        self.assertTrue(out.exists())

    def test_unknown_table_is_empty_not_error(self):
        self.assertEqual(self.engine.list_columns("no_such_table"), [])


class DuckDBEngineContractTest(EngineContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp, cls.engine = make_duckdb_source()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
