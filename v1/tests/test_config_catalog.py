import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.backends import ConfigCatalogResolver
from diracdata.config.settings import DiracDataSettings


class ConfigCatalogTest(unittest.TestCase):
    def test_config_catalog_resolves_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "catalog.json"
            config_path.write_text(
                json.dumps(
                    {
                        "catalog": "commerce_pod",
                        "database": "analytics",
                        "schema": "main",
                        "tables": [
                            {
                                "name": "orders",
                                "path": "/tmp/orders.parquet",
                                "format": "parquet",
                                "description": "Customer order facts",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            resolver = ConfigCatalogResolver.from_file(config_path)

        self.assertEqual(resolver.catalog, "commerce_pod")
        self.assertEqual(resolver.database, "analytics")
        self.assertEqual(resolver.schema, "main")
        self.assertEqual(resolver.list_tables(), ["orders"])
        self.assertEqual(resolver.get_table("orders").path, "/tmp/orders.parquet")
        self.assertEqual(resolver.get_table("orders").description, "Customer order facts")

    def test_config_catalog_validates_customer_facing_settings(self) -> None:
        resolver = ConfigCatalogResolver(
            catalog="commerce_pod",
            database="analytics",
            schema="main",
            tables={},
        )
        resolver.validate_settings(
            DiracDataSettings(catalog="commerce_pod", database="analytics", schema="main")
        )

        with self.assertRaises(ValueError):
            resolver.validate_settings(
                DiracDataSettings(catalog="other_pod", database="analytics", schema="main")
            )

