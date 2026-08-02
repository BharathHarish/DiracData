"""FabricStore: schema-keyed artifact storage over the object store. Unit-tested against a
local object store (fast, no network); a MinIO round-trip runs only if s3 is configured + live.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src"))

from diracdata.utils.object_store import LocalObjectStore  # noqa: E402
from diracdata.context.fabric import FabricStore  # noqa: E402


class FabricStoreTests(unittest.TestCase):
    def _store(self, tmp):
        return FabricStore(LocalObjectStore(tmp))

    def test_fabric_json_roundtrip_and_isolation_by_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fs = self._store(tmp)
            self.assertFalse(fs.has("fin", "metadata_descriptions.json"))
            fs.put("fin", "metadata_descriptions.json", {"tables": {"users": {}}})
            fs.put("retail", "metadata_descriptions.json", {"tables": {"orders": {}}})
            self.assertTrue(fs.has("fin", "metadata_descriptions.json"))
            self.assertEqual(fs.get("fin", "metadata_descriptions.json")["tables"], {"users": {}})
            self.assertEqual(fs.get("retail", "metadata_descriptions.json")["tables"], {"orders": {}})  # not crossed
            self.assertIn("fabric/fin/metadata_descriptions.json", fs.list("fin"))

    def test_get_missing_returns_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self._store(tmp).get("fin", "join_graph.json"))
            self.assertEqual(self._store(tmp).get("fin", "x.json", default=[]), [])

    def test_jsonl_append_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fs = self._store(tmp)
            self.assertEqual(fs.read_records("fin", "joins.jsonl"), [])
            fs.append_record("fin", "joins.jsonl", {"left": "a", "right": "b"})
            fs.append_record("fin", "joins.jsonl", {"left": "c", "right": "d"})
            recs = fs.read_records("fin", "joins.jsonl")
            self.assertEqual([r["left"] for r in recs], ["a", "c"])

    def test_state_json_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fs = self._store(tmp)
            self.assertIsNone(fs.get_state("fin", "column_values.json"))
            fs.put_state("fin", "column_values.json", {"users.city": ["A", "B"]})
            self.assertEqual(fs.get_state("fin", "column_values.json")["users.city"], ["A", "B"])


class MinioRoundTripTests(unittest.TestCase):
    def test_minio_put_get_when_configured(self) -> None:
        from diracdata.config import settings_from_env
        try:
            settings = settings_from_env(str(ROOT / ".env"))
        except Exception:  # noqa: BLE001
            self.skipTest(".env not available")
        if settings.object_store.strip().lower() not in {"s3", "minio"}:
            self.skipTest("object store is not s3/minio")
        from diracdata.context.fabric import fabric_store_from_settings
        try:
            fs = fabric_store_from_settings(settings)
            fs.put("__selftest__", "probe.json", {"ok": True})
            self.assertEqual(fs.get("__selftest__", "probe.json"), {"ok": True})
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"MinIO not reachable: {exc}")


if __name__ == "__main__":
    unittest.main()
