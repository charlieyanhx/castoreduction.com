"""
Rank 24 (formula-tokenizer phantom suffix) of the R4 fix order.

`safe_eval_formula`'s numeric-token regex allowed WHITESPACE before a k/m/b/t
magnitude suffix: `\\d...\\s*(?:k|m|b|t|%)?`. So "300 mid-size" parsed as 300 × Mega
(the 'm' of "mid"), and "1,500 Midwest" as 1,500M — a phantom 1e6× blow-up. The
formula-reconciliation check then reported "computes 120,000,000,000,000 but value is
120,000,000 (1e+06× off)" and, because that ratio is >2×, raised a hard BLOCK that
withheld an otherwise-correct sizing (3219f4db, de34e328).

The fix: a space-separated suffix letter must be a standalone token — `[kmbt]` not
followed by another letter — so "300 mid" is just 300, while "100k", "130M" and
"15%" (attached) still resolve. (174ae091's 10× is a genuine formula/value mismatch and
must stay flagged.)
"""
from __future__ import annotations

import unittest

from skills.sizing.validate import safe_eval_formula


class TestNoPhantomSuffix(unittest.TestCase):
    def test_word_starting_with_m_is_not_mega(self):
        # 300 * 100k * $4 = 120M  (NOT 120e12)
        got = safe_eval_formula("300 mid-size US cities * 100k active users * "
                                "$4.00 annual ad ARPU")
        self.assertAlmostEqual(got, 120_000_000, delta=1)

    def test_midwest_is_not_mega(self):
        got = safe_eval_formula("1,500 Midwest fast-casual salad locations * "
                                "$75,600 ACV per location")
        self.assertAlmostEqual(got, 113_400_000, delta=1)

    def test_attached_suffixes_still_resolve(self):
        # 130M * 2.5 * $250 * 15%  (attached M, %, plain numbers)
        got = safe_eval_formula("130M US households * 2.5 jobs/yr * $250 avg job "
                                "value * 15% take rate")
        self.assertAlmostEqual(got, 12_187_500_000, delta=1)

    def test_k_suffix_attached(self):
        self.assertAlmostEqual(safe_eval_formula("10 * 100k"), 1_000_000, delta=1)

    def test_genuine_mismatch_still_computes(self):
        # A real formula still evaluates (the reconciliation compares it to the value).
        self.assertAlmostEqual(safe_eval_formula("2 * 3 * 4"), 24, delta=0.001)


if __name__ == "__main__":
    unittest.main()
