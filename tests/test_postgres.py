"""Phase 2 Postgres specifics: read-only enforcement, complex-type canonicalization
(jsonb/array/timestamptz -> parquet -> reconciler), and a clear missing-driver hint. The live tests
skip cleanly without DIRACDATA_TEST_PG_DSN; the missing-driver test needs no database.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

_DSN = os.environ.get("DIRACDATA_TEST_PG_DSN")


class MissingDriverTests(unittest.TestCase):
    def test_missing_driver_gives_install_hint(self):
        import diracdata.engines.postgres as pgmod
        key = "adbc_driver_postgresql.dbapi"
        saved = sys.modules.get(key)
        sys.modules[key] = None   # force ImportError on the driver import
        try:
            with self.assertRaises(RuntimeError) as ctx:
                pgmod._connect("postgresql://nope")
            self.assertIn("diracdata[postgres]", str(ctx.exception))
        finally:
            if saved is not None:
                sys.modules[key] = saved
            else:
                del sys.modules[key]


@unittest.skipUnless(_DSN, "no DIRACDATA_TEST_PG_DSN (Postgres)")
class PostgresLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import adbc_driver_postgresql.dbapi as pg
        con = pg.connect(_DSN)
        with con.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS diracdata_types_t")
            cur.execute("CREATE TABLE diracdata_types_t (id int, j jsonb, arr int[], ts timestamptz)")
            cur.execute("INSERT INTO diracdata_types_t VALUES "
                        "(1, '{\"k\": 1}', ARRAY[10,20], TIMESTAMP '2024-03-01 12:00:00+05:30')")
        con.commit()
        con.close()

    def _engine(self, **kw):
        from diracdata.engines.postgres import PostgresEngine
        return PostgresEngine(dsn=_DSN, name="pg", **kw)

    def test_read_only_rejects_writes(self):
        e = self._engine(read_only=True)
        with self.assertRaises(Exception) as ctx:
            e._arrow("INSERT INTO diracdata_types_t VALUES (2,'{\"k\":9}','{}',now())")
        self.assertIn("read-only", str(ctx.exception).lower())   # rejected by the read-only txn

    def test_complex_types_canonicalize_through_parquet(self):
        import duckdb
        e = self._engine()
        out = Path(tempfile.mkdtemp()) / "t.parquet"
        e.copy_to_parquet("SELECT id, j, arr, ts FROM diracdata_types_t WHERE id=1", str(out))
        row = duckdb.connect().execute(
            f"SELECT typeof(j), j, typeof(arr), arr, typeof(ts) FROM read_parquet('{out.as_posix()}')"
        ).fetchone()
        self.assertIn("JSON", row[0].upper())                 # jsonb -> canonical JSON text
        self.assertIn('"k"', row[1])
        self.assertTrue(row[2].endswith("[]"))                # array -> list
        self.assertEqual(list(row[3]), [10, 20])
        self.assertIn("TIME ZONE", row[4].upper())            # timestamptz preserved (UTC)

    @classmethod
    def tearDownClass(cls):
        import adbc_driver_postgresql.dbapi as pg
        con = pg.connect(_DSN)
        with con.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS diracdata_types_t")
        con.commit()
        con.close()


if __name__ == "__main__":
    unittest.main()
