"""Phase 4: cross-source binding discovery. Two sources that share a key (overlapping values) yield a
binding with an overlap %; two sources with DISJOINT ids yield NO binding. Self-contained: DuckDB
sources built from generated parquet, no live model."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.engines import EngineSpec, SourceRegistry  # noqa: E402
from diracdata.learning.bindings import discover_bindings  # noqa: E402


def _source(tmp: str, schema: str, table: str, lo: int, hi: int) -> None:
    import duckdb
    pq = Path(tmp) / schema / "parquet"
    pq.mkdir(parents=True)
    duckdb.connect(":memory:").execute(
        f"COPY (SELECT g AS customer_id FROM range({lo}, {hi}) t(g)) "
        f"TO '{(pq / f'{table}.parquet').as_posix()}' (FORMAT PARQUET)")


class BindingDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._a = tempfile.TemporaryDirectory()
        self._b = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._a.cleanup()
        self._b.cleanup()

    def _registry(self, a_range, b_range):
        _source(self._a.name, "sa", "orders", *a_range)
        _source(self._b.name, "sb", "customers", *b_range)
        return SourceRegistry([
            EngineSpec(name="orders_pg", kind="duckdb", data_root=Path(self._a.name), schema="sa"),
            EngineSpec(name="lake", kind="duckdb", data_root=Path(self._b.name), schema="sb"),
        ])

    def test_overlapping_ids_bind(self):
        reg = self._registry((1, 501), (1, 501))         # both customer_id 1..500
        b = discover_bindings(reg)
        self.assertEqual(len(b), 1)
        self.assertEqual({b[0]["left"], b[0]["right"]},
                         {"orders_pg.orders.customer_id", "lake.customers.customer_id"})
        self.assertGreaterEqual(b[0]["overlap_pct"], 90.0)

    def test_disjoint_ids_do_not_bind(self):
        reg = self._registry((1, 201), (1000, 1201))     # 1..200 vs 1000..1200 -> no overlap
        self.assertEqual(discover_bindings(reg), [])


if __name__ == "__main__":
    unittest.main()
