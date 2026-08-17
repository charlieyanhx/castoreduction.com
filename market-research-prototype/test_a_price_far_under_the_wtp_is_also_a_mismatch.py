"""D18 watched one side of a two-sided rule, and the report gave dangerous advice through the gap.

MEASURED on job d62bc04f, the orbital solar reflection venture:

    consumer_research  willingness_to_pay   $150,000/mo   (1 of 4 segments named a price)
    pricing.psm        optimal_price_point  $  1,450/mo
    EVC verdict        "UNDER-PRICED ... Consider a 20-40% price increase before launch"

The report holds two numbers 103x apart, reconciles neither, and then recommends a ~30%
rise — an answer neither number supports. D18 exists precisely to stop that ("a large gap
between the consumer-research WTP synthesis and the PSM-recommended price must be disclosed,
never rendered side by side with no comment") and it abstained:

    d18: N/A — "recommended 1450.0 within WTP range (ceiling 150000.0)"

WHY. The docstring states the rule symmetrically — "FAIL when the ratio is outside 0.1x-10x"
— but the body added an early return:

    if rec_n <= ceiling_n:
        return Finding(None, f"recommended {rec_n} within WTP range (ceiling {ceiling_n})")

annotated "the mismatch that misleads a buyer is a price ABOVE the top of the WTP range ... A
price at/below the ceiling is fine (someone would pay it)." That reasoning holds for the R4
cases it was written for, and it is true that someone would pay $1,450 when they said
$150,000. It is not true that the two numbers agree, and DISCLOSURE — not price level — is
what this gate enforces. So the narrowing quietly replaced the documented rule with half of it.

MEASURED BEFORE CHANGING IT, across all 24 stored artifacts: every real report prices at
0.1x-1.1x of its own WTP floor. **Zero would newly fail.** The two ecommerce reports at
$125,000 against a $150 floor are the ABOVE-ceiling case D18 already catches. So restoring
symmetry costs nothing on the corpus and catches the one venture that needed it.

NOT WIDENED FURTHER. The band stays 10x. A tighter threshold would fire on ordinary
value-based pricing (a report priced at half its WTP ceiling is normal and healthy), and this
gate's remedy is a disclosure flag, not a price change — it never tells the operator what to
charge, only that two of its own numbers disagree and the report never said so.
"""
from __future__ import annotations

import unittest

from gates import d18_wtp_price_reconciled


def _report(*, wtp: dict, price: float, flagged: bool = False) -> dict:
    syn = {"willingness_to_pay": wtp}
    if flagged:
        syn["wtp_price_mismatch"] = "disclosed in the report"
    return {"consumer_research": {"synthesis": syn},
            "pricing": {"psm": {"optimal_price_point": price}}}


class TestThePriceFarBelowTheStatedWTP(unittest.TestCase):
    def test_the_measured_case_now_fails(self):
        f = d18_wtp_price_reconciled(
            _report(wtp={"point": 150000.0}, price=1450.0), None)
        self.assertIs(f.ok, False,
                      f"a 103x gap is still unreported: {f.detail!r}")
        self.assertRegex((f.detail or "").lower(), r"below|under",
                         f"the detail does not say which way the gap runs: {f.detail!r}")

    def test_disclosing_it_satisfies_the_gate(self):
        """The remedy is disclosure, not a different price — same contract as the
        above-ceiling case."""
        f = d18_wtp_price_reconciled(
            _report(wtp={"point": 150000.0}, price=1450.0, flagged=True), None)
        self.assertIs(f.ok, True, f.detail)

    def test_a_price_above_the_ceiling_still_fails(self):
        """The original half of the rule must keep working."""
        f = d18_wtp_price_reconciled(
            _report(wtp={"low": 150.0, "high": 150.0}, price=125000.0), None)
        self.assertIs(f.ok, False, f.detail)


class TestOrdinaryPricingIsUntouched(unittest.TestCase):
    """Measured across all 24 stored artifacts: every real report prices at 0.1x-1.1x of its
    own WTP floor. None of these may start failing."""

    MEASURED = [
        # (wtp_low, wtp_high, recommended_price, which report)
        (15.0, 4500.0, 14.0, "4a755faa subscription"),
        (6.0, 8.0, 6.0, "e8baf9dd transactional"),
        (22.0, 1200.0, 24.0, "c98_subscription"),
        (15000.0, 25000.0, 18500.0, "348c69ca services"),
        (4.0, 6.0, 6.0, "c98_chain regional"),
        (1.0, 9.0, 4.0, "94008e7c hyperlocal"),
        (1.0, 4.0, 4.0, "c98_nonus hyperlocal"),
        (15.0, 350.0, 250.0, "174ae091 marketplace"),
        (45.0, 120.0, 75.0, "c48497fa transactional"),
        (99.0, 149.0, 149.0, "8add1fa2 hybrid"),
    ]

    def test_no_stored_report_starts_failing(self):
        broke = []
        for low, high, price, name in self.MEASURED:
            f = d18_wtp_price_reconciled(
                _report(wtp={"low": low, "high": high}, price=price), None)
            if f.ok is False:
                broke.append(f"{name}: {f.detail}")
        self.assertEqual(broke, [],
                         "healthy reports began failing — the band is too tight:\n  "
                         + "\n  ".join(broke))

    def test_a_price_at_half_the_wtp_ceiling_is_normal_and_passes(self):
        f = d18_wtp_price_reconciled(
            _report(wtp={"low": 80.0, "high": 200.0}, price=100.0), None)
        self.assertIsNot(f.ok, False, f.detail)

    def test_missing_numbers_still_abstain(self):
        for wtp, price in (({}, 100.0), ({"point": 100.0}, None), ({"point": 0}, 10.0)):
            with self.subTest(wtp=wtp):
                self.assertIsNone(
                    d18_wtp_price_reconciled(_report(wtp=wtp, price=price), None).ok)


class TestTheGateStillMatchesItsOwnDocstring(unittest.TestCase):
    def test_the_documented_band_is_the_implemented_band(self):
        """The defect was a body that stopped matching its docstring. Keep them married."""
        import inspect
        src = inspect.getsource(d18_wtp_price_reconciled)
        self.assertIn("0.1x-10x", src, "the documented band changed; re-derive the threshold")
        # The defect was ORDER, not wording: an abstention that returned before the
        # below-floor case could be reached. A final "within WTP range" N/A is correct and
        # must stay; what must never come back is that N/A preceding the floor check.
        i_floor = src.find("floor_n and rec_n")
        i_na = src.find('Finding(None, f"recommended')
        self.assertGreater(i_floor, 0, "the below-floor branch is gone")
        self.assertGreater(i_na, i_floor,
                           "an abstention still returns before the below-floor check — a "
                           "price far BELOW a stated WTP abstains again")


if __name__ == "__main__":
    unittest.main()
