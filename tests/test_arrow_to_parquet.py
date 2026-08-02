"""ME-P1-01: AbstractEngine's Arrow `copy_to_parquet` default (used by external connectors) writes a
parquet identical to DuckDB's native COPY on the same data -- so a connector that returns Arrow lands
the same bytes as a native engine."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.engines.base import AbstractEngine  # noqa: E402

_VALUES = "(SELECT * FROM (VALUES (1,'a',1.5),(2,'b',2.5),(3,'c',3.5)) v(id, s, x))"


class _FakeArrowEngine(AbstractEngine):
    """An external-style engine that only knows how to return Arrow (no native COPY)."""

    dialect = "fake"

    def __init__(self, table):
        super().__init__(name="fake")
        self._t = table

    def arrow_batches(self, sql):
        return self._t.to_reader(max_chunksize=2)   # small chunks -> exercises multi-batch write


class ArrowToParquetTest(unittest.TestCase):
    def test_arrow_default_equals_native_copy(self):
        import duckdb
        import pyarrow as pa

        con = duckdb.connect(":memory:")
        table = pa.table({"id": [1, 2, 3], "s": ["a", "b", "c"], "x": [1.5, 2.5, 3.5]})

        arrow_out = Path(tempfile.mkdtemp()) / "arrow.parquet"
        n = _FakeArrowEngine(table).copy_to_parquet("ignored", str(arrow_out))

        native_out = Path(tempfile.mkdtemp()) / "native.parquet"
        con.execute(f"COPY {_VALUES} TO '{native_out.as_posix()}' (FORMAT PARQUET)")

        a = con.execute(f"SELECT * FROM read_parquet('{arrow_out.as_posix()}') ORDER BY id").fetchall()
        b = con.execute(f"SELECT * FROM read_parquet('{native_out.as_posix()}') ORDER BY id").fetchall()
        self.assertEqual(n, 3)
        self.assertEqual(a, b)

    def test_missing_arrow_batches_is_a_clear_error(self):
        class Bare(AbstractEngine):
            dialect = "bare"
        with self.assertRaises(NotImplementedError):
            Bare(name="bare").copy_to_parquet("SELECT 1", "/tmp/x.parquet")


if __name__ == "__main__":
    unittest.main()
