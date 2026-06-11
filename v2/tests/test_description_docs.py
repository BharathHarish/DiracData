import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.context import build_description_docs


class DescriptionDocsTests(unittest.TestCase):
    def test_builds_table_and_column_markdown_with_sample_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            descriptions_path = root / "metadata_descriptions.json"
            data_root = root / "data"
            parquet_dir = data_root / "demo_schema" / "parquet"
            parquet_dir.mkdir(parents=True)
            _write_demo_parquet(parquet_dir / "orders.parquet")
            descriptions_path.write_text(
                json.dumps(
                    {
                        "tables": {
                            "orders": {
                                "short_description": "Checkout orders.",
                                "long_description": "Every checkout order created by a shopper.",
                            }
                        },
                        "columns": {
                            "orders": {
                                "order_id": {
                                    "short_description": "Unique order identifier.",
                                    "long_description": "Identifier for a checkout order.",
                                },
                                "channel": {
                                    "short_description": "Order channel.",
                                    "long_description": "Channel where the order was placed.",
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = build_description_docs(
                descriptions_path=descriptions_path,
                data_root=data_root,
                schema_name="demo_schema",
                output_dir=root / "context",
                sample_values_limit=3,
            )

            table_doc = result.table_descriptions_path.read_text(encoding="utf-8")
            column_doc = result.table_column_descriptions_path.read_text(encoding="utf-8")

        self.assertEqual(result.table_count, 1)
        self.assertEqual(result.column_count, 2)
        self.assertIn("Every checkout order created by a shopper.", table_doc)
        self.assertIn("`channel`", table_doc)
        self.assertIn("orders.channel", column_doc)
        self.assertIn("'web'", column_doc)


def _write_demo_parquet(path: Path) -> None:
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE orders AS
        SELECT * FROM (
            VALUES (1, 'web'), (2, 'mobile'), (3, 'web')
        ) AS t(order_id, channel)
        """
    )
    con.execute(f"COPY orders TO '{path.as_posix()}' (FORMAT PARQUET)")
    con.close()


if __name__ == "__main__":
    unittest.main()
