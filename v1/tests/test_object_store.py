from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.storage import LocalObjectStore, artifact_key, object_store_from_settings


class ObjectStoreTest(unittest.TestCase):
    def test_local_object_store_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LocalObjectStore(tmpdir)
            key = artifact_key(
                pod_id="commerce",
                run_id="learn_001",
                artifact_type="table_profiles",
                name="store_sales.json",
            )

            store.write_json(key, {"table": "store_sales", "rows": 123})

            self.assertTrue(store.exists(key))
            self.assertEqual(store.read_json(key), {"rows": 123, "table": "store_sales"})
            self.assertEqual(store.list_keys("pods/commerce"), [key])

    def test_object_store_factory_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(object_store="local", local_artifact_root=Path(tmpdir))
            store = object_store_from_settings(settings)
            store.write_text("hello/world.txt", "ok")
            self.assertEqual(store.read_text("hello/world.txt"), "ok")

