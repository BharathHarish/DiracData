"""The two stewardship gates. probe_footprint (data quality on inputs) is exercised e2e
elsewhere; here we pin the deterministic SANITY gate on the result, which is pure logic."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "v2" / "src"))
sys.path.insert(0, str(ROOT / "v3" / "src"))

from diracdata_v3.stewardship import sanity_check, trust_line  # noqa: E402


def _res(cols, rows):
    return {"columns": cols, "rows": rows, "row_count": len(rows)}


class SanityGateTests(unittest.TestCase):
    def test_clean_result_has_no_flags(self) -> None:
        out = sanity_check("", _res(["state", "refund_rate", "buyers"], [["CO", 7.18, 219], ["WI", 6.37, 248]]))
        self.assertEqual(out["flags"], [])

    def test_empty_result_flagged(self) -> None:
        out = sanity_check("", _res(["n"], []))
        self.assertTrue(any("EMPTY" in f for f in out["flags"]))

    def test_null_cell_in_answer_flagged(self) -> None:
        out = sanity_check("", _res(["state", "rev"], [["CA", None]]))
        self.assertTrue(any("NULL cell" in f for f in out["flags"]))

    def test_rate_out_of_range_flagged(self) -> None:
        out = sanity_check("", _res(["state", "refund_rate_pct"], [["CO", 142.0]]))
        self.assertTrue(any("rate" in f and "outside" in f for f in out["flags"]))

    def test_rate_in_range_not_flagged(self) -> None:
        out = sanity_check("", _res(["state", "refund_rate_pct"], [["CO", 7.18], ["WI", 6.37]]))
        self.assertFalse(any("rate" in f for f in out["flags"]))

    def test_negative_count_flagged(self) -> None:
        out = sanity_check("", _res(["state", "order_count"], [["CO", -5]]))
        self.assertTrue(any("count" in f and "negative" in f for f in out["flags"]))

    def test_duplicate_dimension_rows_flagged(self) -> None:
        out = sanity_check("", _res(["state", "rev"], [["CO", 10.0], ["CO", 12.0], ["WI", 8.0]]))
        self.assertTrue(any("duplicate" in f for f in out["flags"]))

    def test_trust_line_reports_both_gates(self) -> None:
        dq = {"row_counts": {"online_purchases": 100}, "joins": [], "flags": []}
        sanity = {"row_count": 5, "flags": []}
        line = trust_line(dq, sanity)
        self.assertIn("DATA QUALITY:", line)
        self.assertIn("SANITY:", line)


if __name__ == "__main__":
    unittest.main()
