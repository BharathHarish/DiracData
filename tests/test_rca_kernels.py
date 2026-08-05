"""RCA attribution kernels: exact splits that reconcile to the parent delta, and Adtributor slice
ranking. Pure math -- hand-checked numbers, no DB."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.rca.kernels import (  # noqa: E402
    adtributor, attribute, attribute_additive, attribute_multiplicative, attribute_ratio,
)


class AdditiveTests(unittest.TestCase):
    def test_parts_are_their_own_delta_and_reconcile(self):
        # net = gross - refund: gross 100->90 (-10), refund enters as -amount: -5 -> -6 (-1) => net -11
        c = attribute_additive([("gross", 100, 90), ("neg_refund", -5, -6)])
        self.assertEqual([round(x.contribution, 6) for x in c], [-10.0, -1.0])
        self.assertAlmostEqual(sum(x.contribution for x in c), -11.0)      # reconciles


class MultiplicativeTests(unittest.TestCase):
    def test_symmetric_split_reconciles_exactly(self):
        # revenue = buyers x rev_per_buyer: 10*100=1000 -> 9*110=990 (delta -10)
        c = attribute_multiplicative(("buyers", 10, 9), ("rpb", 100, 110))
        self.assertAlmostEqual(sum(x.contribution for x in c), 990 - 1000)  # exact, no residual
        # buyer effect = dA*(b0+b1)/2 = -1*(210)/2 = -105 ; rpb effect = dB*(a0+a1)/2 = 10*(19)/2 = 95
        self.assertAlmostEqual(c[0].contribution, -105.0)
        self.assertAlmostEqual(c[1].contribution, 95.0)


class RatioTests(unittest.TestCase):
    def test_numerator_denominator_split_reconciles_exactly(self):
        # aov = revenue / orders: 1000/10=100 -> 990/11=90 (delta -10)
        c = attribute_ratio(("revenue", 1000, 990), ("orders", 10, 11))
        self.assertAlmostEqual(sum(x.contribution for x in c), (990 / 11) - (1000 / 10))
        # N-effect = (990-1000)/11 = -0.909... ; D-effect = -1000*(1)/(10*11) = -9.0909...
        self.assertAlmostEqual(c[0].contribution, -10 / 11)
        self.assertAlmostEqual(c[1].contribution, -1000 * 1 / (10 * 11))

    def test_zero_denominator_raises(self):
        with self.assertRaises(ValueError):
            attribute_ratio(("n", 1, 2), ("d", 0, 5))


class DispatchTests(unittest.TestCase):
    def test_kind_dispatch_and_residual_is_zero(self):
        for kind, children in (
            ("additive", [("a", 1, 2), ("b", 3, 1)]),
            ("multiplicative", [("a", 10, 9), ("b", 100, 110)]),
            ("ratio", [("n", 1000, 990), ("d", 10, 11)]),
            ("proportion", [("succ", 30, 25), ("pop", 100, 100)]),  # folds into ratio
        ):
            out = attribute(kind, children)
            self.assertAlmostEqual(out["residual"], 0.0, places=9,
                                   msg=f"{kind} must reconcile to zero residual")

    def test_unknown_kind_falls_back_to_additive(self):
        out = attribute("mystery", [("a", 1, 2), ("b", 2, 2)])
        self.assertAlmostEqual(out["parent_delta"], 1.0)


class AdtributorTests(unittest.TestCase):
    def test_ranks_the_slice_that_carries_the_drop(self):
        # total falls 300 -> 240 (-60). "Electronics" carries most of it and its share shrinks most.
        slices = [("Electronics", 150, 95), ("Music", 100, 100), ("Books", 50, 45)]
        ranked = adtributor(slices, top_k=3)
        self.assertEqual(ranked[0]["slice"], "Electronics")           # biggest mover ranks first
        self.assertLess(ranked[0]["delta"], 0)
        self.assertEqual(len(ranked), 3)

    def test_top_k_caps_output(self):
        slices = [(f"s{i}", 100, 100 - i) for i in range(10)]
        self.assertEqual(len(adtributor(slices, top_k=4)), 4)


if __name__ == "__main__":
    unittest.main()
