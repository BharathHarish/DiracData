"""The grounding tool behind the learning agent: `column_facts` runs SQL and reports what the DB
says. These pin that it measures the ground truth (cardinality, unique-key, complete low-card
domains). Runs against the real fintech schema; skipped if the data isn't present.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src"))

from diracdata.utils.duckdb_engine import DuckDBEngine  # noqa: E402
from diracdata.learning import column_facts  # noqa: E402

_FIN = ROOT / "data" / "fintech_schema" / "parquet"


@unittest.skipUnless(_FIN.exists(), "fintech data not present")
class ColumnFactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.e = DuckDBEngine(data_root=ROOT / "data", schema_name="fintech_schema")

    def test_row_count_and_unique_key_measured(self) -> None:
        f = column_facts(self.e, "users", "user_ref")
        self.assertEqual(f["row_count"], 1000)
        self.assertTrue(f["is_unique_key"])                 # user_ref uniquely identifies a user
        self.assertEqual(f["distinct"], 1000)

    def test_non_unique_column_not_flagged_as_key(self) -> None:
        f = column_facts(self.e, "payments", "user_ref")    # many payments per user
        self.assertEqual(f["row_count"], 18000)
        self.assertFalse(f["is_unique_key"])

    def test_low_cardinality_domain_complete_and_exact(self) -> None:
        f = column_facts(self.e, "payments", "payment_status")
        self.assertTrue(f["domain"]["complete"])
        self.assertEqual(f["distinct"], 4)
        self.assertEqual(set(f["domain"]["values"]), {"SUCCESS", "FAILED", "AUTHORIZED", "REFUNDED"})

    def test_rail_type_values_captured(self) -> None:
        f = column_facts(self.e, "payment_attributes", "rail_type")
        self.assertTrue(f["domain"]["complete"])
        self.assertIn("UPI", f["domain"]["values"])
        self.assertEqual(f["distinct"], len(f["domain"]["values"]))  # complete = every value listed

    def test_null_pct_within_bounds(self) -> None:
        f = column_facts(self.e, "user_attributes", "kyc_status")
        self.assertGreaterEqual(f["null_pct"], 0.0)
        self.assertLessEqual(f["null_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
