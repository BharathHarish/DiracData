from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_retail_analytics_parquet import (  # noqa: E402
    DEFAULT_SOURCE_DIR,
    TABLE_RENAMES,
    build_rename_plans,
    rename_column,
)


class RetailAnalyticsHarnessTest(unittest.TestCase):
    def test_column_renames_remove_technical_key_suffixes(self) -> None:
        self.assertEqual(rename_column("customer", "c_customer_sk"), "client_record")
        self.assertEqual(rename_column("customer", "c_customer_id"), "client_code")
        self.assertEqual(
            rename_column("customer", "c_current_cdemo_sk"),
            "current_client_profile_ref",
        )
        self.assertEqual(
            rename_column("customer_address", "ca_address_sk"),
            "address_record",
        )
        self.assertEqual(rename_column("promotion", "p_promo_sk"), "campaign_record")
        self.assertEqual(rename_column("inventory", "inv_item_sk"), "merchandise_ref")

    def test_builds_complete_retail_analytics_plan_from_source_parquet(self) -> None:
        source_dir = ROOT / DEFAULT_SOURCE_DIR
        if not source_dir.exists():
            self.skipTest(f"source parquet not found: {source_dir}")

        with tempfile.TemporaryDirectory() as tmpdir:
            plans = build_rename_plans(source_dir, Path(tmpdir))

        self.assertEqual(len(plans), 24)
        self.assertEqual({plan.source_table for plan in plans}, set(TABLE_RENAMES))
        self.assertEqual({plan.target_table for plan in plans}, set(TABLE_RENAMES.values()))

        for plan in plans:
            self.assertNotEqual(plan.source_table, plan.target_table)
            self.assertNotIn(" ", plan.target_table)
            for target_column in plan.columns.values():
                self.assertFalse(target_column.endswith("_sk"), target_column)
                self.assertFalse(target_column.endswith("_pk"), target_column)
                self.assertFalse(target_column.endswith("_id"), target_column)


if __name__ == "__main__":
    unittest.main()
