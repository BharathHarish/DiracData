"""SourceRegistry: single-source synth from Config (back-compat), lazy cached build, wrapping an
existing engine, and clear errors for unknown source / unavailable engine kind / empty registry.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from _engine_fixture import make_duckdb_source  # noqa: E402
from diracdata.config import Config  # noqa: E402
from diracdata.engines import EngineSpec, SourceRegistry  # noqa: E402


class SourceRegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp, self.engine = make_duckdb_source(schema="reg_schema")

    def tearDown(self):
        self._tmp.cleanup()

    def _cfg(self):
        return Config(sql_engine="duckdb", data_root=Path(self._tmp.name), schema="reg_schema")

    def test_from_config_single_source_reproduces_today(self):
        reg = SourceRegistry.from_config(self._cfg())
        self.assertEqual(reg.names(), ["reg_schema"])
        eng = reg.get_default()
        self.assertIn("t", eng.list_tables())
        self.assertEqual(eng.dialect, "duckdb")

    def test_lazy_build_caches(self):
        reg = SourceRegistry.from_config(self._cfg())
        self.assertIs(reg.get("reg_schema"), reg.get("reg_schema"))

    def test_of_wraps_existing_engine(self):
        reg = SourceRegistry.of(self.engine)
        self.assertIs(reg.get_default(), self.engine)

    def test_unknown_source_raises(self):
        reg = SourceRegistry.of(self.engine)
        with self.assertRaises(KeyError):
            reg.get("nope")

    def test_unknown_kind_raises(self):
        reg = SourceRegistry([EngineSpec(name="x", kind="oracle")])
        with self.assertRaises(NotImplementedError):
            reg.get("x")

    def test_empty_registry_rejected(self):
        with self.assertRaises(ValueError):
            SourceRegistry([])


if __name__ == "__main__":
    unittest.main()
