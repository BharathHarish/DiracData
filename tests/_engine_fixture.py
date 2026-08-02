"""Self-contained engine fixture: a temp DuckDB parquet source with known rows (incl. a NULL), so
the engine conformance contract runs with no external data or services."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def make_duckdb_source(schema: str = "testschema", table: str = "t"):
    """Create <tmp>/<schema>/parquet/<table>.parquet with 3 known rows; return (tmpdir, engine)."""
    import duckdb

    from diracdata.engines.duckdb import DuckDBEngine

    tmp = tempfile.TemporaryDirectory()
    pq = Path(tmp.name) / schema / "parquet"
    pq.mkdir(parents=True)
    con = duckdb.connect(":memory:")
    con.execute(
        "COPY (SELECT * FROM (VALUES (1,'a',10.0,'x'),(2,'b',20.0,NULL),(3,'a',30.0,'z')) "
        f"AS v(user_id, seg, amount, note)) TO '{(pq / f'{table}.parquet').as_posix()}' (FORMAT PARQUET)")
    con.close()
    engine = DuckDBEngine(data_root=Path(tmp.name), schema_name=schema, name="test_src")
    return tmp, engine
