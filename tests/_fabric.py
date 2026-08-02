"""Shared test fixture: the retail_analytics domain context from the object store + a real DuckDB
engine over the local parquet. Skips cleanly when MinIO/the fabric or the local data are absent.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

SCHEMA = "retail_analytics"
DATA_ROOT = ROOT / "data"
DATA_PRESENT = (DATA_ROOT / SCHEMA / "parquet").exists()
GOLD = DATA_ROOT / "evals" / "Goldset_retail_queries.csv"
HISTORY = DATA_ROOT / "query_history" / f"{SCHEMA}_query_history.csv"


def retail_fabric() -> tuple[object | None, bool]:
    try:
        from diracdata.context.fabric import fabric_store_from_settings
        from diracdata.config import settings_from_env
        store = fabric_store_from_settings(settings_from_env(str(ROOT / ".env")))
        ok = bool(store.get(SCHEMA, "metadata_descriptions.json"))
        return store, ok
    except Exception:  # noqa: BLE001
        return None, False


def retail_workspace(**kwargs):
    from diracdata.context.workspace import Workspace
    store, ok = retail_fabric()
    if not ok:
        return None
    return Workspace.from_store(store=store, schema=SCHEMA, **kwargs)


def engine():
    from diracdata.utils.duckdb_engine import DuckDBEngine
    return DuckDBEngine(data_root=DATA_ROOT, schema_name=SCHEMA)


STORE, HAS_RETAIL = retail_fabric()
