"""The prompt writes one ladder, the gate checks a different one, and neither reads the venture.

#100 taught `financials.planning_target` that a consultancy plans in projects/month and a
cafe in drinks/day. It did not touch the two places that USE the ladder, so the period never
propagated. MEASURED, four ventures, the shipped code:

  kind           ladder handed to the 4Ps sections                              D61
  transactional  "break-even 120.4 drinks/day · TARGET 118 drinks/day ·          ok=True
                  ceiling 195 drinks/day"                          <- correct
  subscription   "PLANNING TARGET $20,000 revenue/month"                         ok=None
                 - the venture prices at $29/seat and could be told 690
                   seats/month; four_ps reads `price_per_unit` only, and a
                   subscription stores `monthly_price_usd`
  services       "PLANNING TARGET $20,000 revenue/month"                         ok=None
                 - same: `price_usd` unread, so 1.7 projects/month became a
                   revenue figure no operator can staff against
  marketplace    "PLANNING TARGET 57 units/month · ceiling 23 units/day"         ok=None
                 - the CEILING IS BELOW THE TARGET. Not a typo: the target is
                   monthly (from #100) and the ceiling is still som/price/365.
                   The sections are handed a ladder that contradicts itself and
                   told it is the only source of volumes they may state.

Three separate defects, one cause -- three owners of one number:

  (a) four_ps and gates each reach into the economics dict themselves and only know the
      retail keys (`unit`, `price_per_unit`), so every non-retail shape reads as priceless.
  (b) four_ps prints "CANONICAL DAILY-VOLUME LADDER", "the ONLY *daily* volumes you may
      state", and divides the ceiling by 365 -- all three hardcoded to days.
  (c) gates._VOLUME_CLAIM matches `per day|/day|daily` beside a noun from a fixed list
      (drinks, units, transactions, customers, covers, orders, visits). "267 seats per
      month" contains no listed noun AND no daily phrasing, so D61 returns not-applicable
      on three of the four models above -- the gate is blind exactly where the ladder is
      broken, which is why this shipped.

They must be fixed together. Fixing (a) alone yields "690 seats/month" under a DAILY header.
Fixing (c) alone points a working gate at a ladder whose ceiling is below its target, and
D61 starts failing correct prose.

A fourth thing falls out of consolidating: `financials._DAYS_PER_YEAR` is 360 (the deliberate
open-days assumption behind every ramp), while four_ps and gates both hardcode 365. The
ceiling the prompt states and the ceiling the gate checks are 1.4% off the model that
computes the target sitting next to them. Same direction, so nobody saw it.
"""
from __future__ import annotations

import unittest

_LOCAL = {"scale": "hyperlocal", "som": {"mid": 462_000.0}}
_NATIONAL = {"scale": "national_digital", "som": {"mid": 3_000_000.0}}

#: (economics, market_sizing) exactly as each model's own code path stores them. The key
#: names are the point: a subscription has never written `price_per_unit` in its life.
VENTURES = {
    "transactional": (dict(unit="drink", price_per_unit=6.5,
                           break_even_units_per_day=120.4), _LOCAL),
    "subscription": (dict(pricing_unit="seat", monthly_price_usd=29.0), _NATIONAL),
    "services": (dict(pricing_unit="project", price_usd=12_000.0), _NATIONAL),
    "marketplace": (dict(pricing_unit="booking", price_per_unit=350.0), _NATIONAL),
}


def _ladder(kind: str) -> str:
    from four_ps import _r_volume_ladder
    econ, ms = VENTURES[kind]
    return _r_volume_ladder({"economics": econ, "market_sizing": ms,
                             "business_model_kind": kind})


def _d61(kind: str, prose: str):
    import gates
    econ, ms = VENTURES[kind]
    return gates.d61_volume_targets_match_the_ladder(
        {"economics": econ, "market_sizing": ms, "business_model": {"kind": kind},
         "four_ps": {"price": prose}}, None)


class TestTheLadderIsInternallyConsistent(unittest.TestCase):
    """A ladder whose rungs are in different periods is worse than no ladder: the sections
    are told it is the only volume source they may quote."""

    def test_every_rung_carries_the_same_period(self):
        import re
        for kind in VENTURES:
            with self.subTest(kind=kind):
                periods = set(re.findall(r"/(day|month)\b", _ladder(kind)))
                self.assertLessEqual(
                    len(periods), 1,
                    f"the {kind} ladder mixes periods {sorted(periods)} — a monthly target "
                    f"beside a daily ceiling is an off-by-30x instruction")

    def test_the_marketplace_ceiling_is_above_its_target(self):
        """The measured shape: TARGET 57 units/month, ceiling 23 units/day. A ceiling below
        the target makes the whole ladder unquotable."""
        import re
        txt = _ladder("marketplace")
        target = re.search(r"PLANNING TARGET ≈ ([\d,]+)", txt)
        ceiling = re.search(r"ceiling \(SOM\) ≈ ([\d,]+)", txt)
        self.assertIsNotNone(target, txt)
        self.assertIsNotNone(ceiling, txt)
        self.assertGreater(float(ceiling.group(1).replace(",", "")),
                           float(target.group(1).replace(",", "")),
                           "the obtainable ceiling came out below the planning target")


class TestTheLadderSpeaksTheVenturesLanguage(unittest.TestCase):
    def test_a_subscription_gets_a_seat_count_not_a_revenue_figure(self):
        """$29/seat is a price. Handing a founder "$20,000 revenue/month" instead of "690
        seats/month" is refusing to answer the only question the ladder exists for."""
        txt = _ladder("subscription")
        self.assertIn("seat", txt, f"the unit noun never reached the prompt: {txt[:200]}")
        self.assertNotIn("revenue/month", txt,
                         "a priced subscription was described in revenue because "
                         "`monthly_price_usd` was not read as a price")

    def test_a_consultancy_gets_a_project_count(self):
        txt = _ladder("services")
        self.assertIn("project", txt, txt[:200])
        self.assertNotIn("revenue/month", txt)

    def test_a_marketplace_ladder_says_bookings(self):
        """"57 units/month" is not wrong, it is unusable — the operator cannot tell whether
        a unit is a booking, a listing or a dollar."""
        self.assertIn("booking", _ladder("marketplace"))

    def test_the_cafe_is_unchanged(self):
        txt = _ladder("transactional")
        self.assertIn("drink", txt)
        self.assertIn("/day", txt)

    def test_the_header_and_rule_name_the_period_they_mean(self):
        """MEASURED: a monthly venture's ladder was titled "CANONICAL DAILY-VOLUME LADDER"
        and ruled "the ONLY daily volumes you may state" — so a section that obeyed the
        rule literally would state no volume at all, and run18's sections did exactly that.

        R4 (88b416f6) refined the contract: a per-period-priced model's ladder counts a
        STOCK, and its header/rule name the stockness ("ACTIVE ... LADDER", "held at
        once") instead of a period — "320 seats/month" was a 12x acquisition
        overstatement wearing the ladder's own numbers. Rate models keep the period."""
        for kind in ("subscription", "services", "marketplace"):
            with self.subTest(kind=kind):
                txt = _ladder(kind).lower()
                self.assertNotIn("daily-volume ladder", txt)
                self.assertNotIn("only daily volumes", txt)
                if "active-" in txt:              # stock ladder (per-period price)
                    self.assertIn("held at once", txt)
                    self.assertNotIn("s/month", txt)
                else:                             # rate ladder keeps its period
                    self.assertIn("monthly", txt)


class TestTheGateCanSeeWhatTheSectionsWrite(unittest.TestCase):
    """(c). D61 returned not-applicable on three of four models, so the ladder could be as
    broken as it liked."""

    def test_a_monthly_volume_is_a_volume(self):
        f = _d61("subscription", "We plan for 690 seats per month by month 12.")
        self.assertIsNotNone(f.ok, "D61 could not see 'seats per month' at all")
        self.assertTrue(f.ok, f.detail)

    def test_the_ventures_own_noun_is_recognised(self):
        """The noun list was drinks/units/transactions/customers/covers/orders/visits —
        every one of them a cafe or shop word. A venture that sells projects was invisible.

        Digits only, deliberately: "two projects per month" stays undetected, and widening
        to word numerals would match "one of the two channels" far more often than a real
        target. A known gap, not an oversight."""
        f = _d61("services", "1.7 projects per month is the year-one plan.")
        self.assertIsNotNone(f.ok, "'projects per month' matched no volume phrasing")
        self.assertTrue(f.ok, f.detail)

    def test_an_invented_monthly_target_is_caught(self):
        """The whole point. 80 bookings/month against rungs of 57 and 714 is a number the
        section chose for itself, and it used to pass as not-applicable."""
        f = _d61("marketplace", "We target 80 bookings per month.")
        self.assertIs(f.ok, False, f"an invented volume passed: {f.detail}")
        self.assertIn("57", f.detail, "the failure must name the rung it should have quoted")

    def test_a_quoted_rung_passes(self):
        f = _d61("marketplace", "We target 57 bookings per month.")
        self.assertIs(f.ok, True, f.detail)

    def test_the_cafe_case_still_behaves(self):
        self.assertIs(_d61("transactional", "Targeting 118 drinks per day.").ok, True)
        self.assertIs(_d61("transactional", "Targeting 250 drinks per day.").ok, False)

    def test_a_daily_figure_in_a_monthly_business_does_not_silently_pass(self):
        """23/day was the broken ceiling. If a section quotes it after the fix, that is an
        invented number and must fail rather than match a stale rung."""
        f = _d61("marketplace", "We target 23 bookings per day.")
        self.assertIs(f.ok, False, f.detail)


class TestOneOwnerForTheNumber(unittest.TestCase):
    """(a). The prompt and the gate must not each compute the ceiling."""

    def test_both_sides_agree_on_every_rung(self):
        from financials import ladder_inputs
        from four_ps import ladder_number
        for kind, (econ, ms) in VENTURES.items():
            with self.subTest(kind=kind):
                rungs = ladder_inputs(econ, ms, kind)["rungs"]
                txt = _ladder(kind)
                for name, value in rungs.items():
                    self.assertIn(ladder_number(value), txt,
                                  f"{kind}: the prompt's {name} is not the model's "
                                  f"{ladder_number(value)} — {txt[:200]}")

    def test_the_open_days_assumption_is_the_models_own(self):
        """four_ps and gates both hardcoded 365 while every ramp in financials runs on 360
        open days. The ceiling stated and the target stated came from different calendars."""
        from financials import _DAYS_PER_YEAR, ladder_inputs
        from four_ps import ladder_number
        econ, ms = VENTURES["transactional"]
        ceiling = ladder_inputs(econ, ms, "transactional")["rungs"]["obtainable ceiling"]
        self.assertAlmostEqual(ceiling, 462_000.0 / 6.5 / _DAYS_PER_YEAR, places=6)
        self.assertIn(ladder_number(ceiling), _ladder("transactional"))


class TestTheCitationCheckerKnowsTheLadderToo(unittest.TestCase):
    """A THIRD owner of the ceiling, found while wiring the other two.

    `report.claim_support.given_numbers` builds the set of figures a 4Ps section may cite,
    and it computed its own `som / price / 365`. MEASURED against the model's rungs:

      transactional  NOT CITABLE: planning target 118.5, obtainable ceiling 197.4
      subscription   NOT CITABLE: planning target 689.7, obtainable ceiling 8,620.7
      services       NOT CITABLE: planning target 1.7,   obtainable ceiling 20.8

    The planning target has never been citable for ANY model — the whitelist only ever knew
    the ceiling. So since #97 the pipeline has told every section "Quote the PLANNING TARGET
    when you need an operating number" while the citation checker called that number
    fabricated. One subsystem commands what another forbids.
    """

    def test_every_rung_the_sections_were_given_is_citable(self):
        from financials import ladder_inputs
        from report.claim_support import given_numbers
        for kind, (econ, ms) in VENTURES.items():
            with self.subTest(kind=kind):
                rungs = ladder_inputs(econ, ms, kind)["rungs"]
                given = given_numbers({"economics": econ, "market_sizing": ms,
                                       "business_model": {"kind": kind}})
                missing = {k: round(v, 1) for k, v in rungs.items()
                           if not any(abs(v - g) < 0.51 for g in given)}
                self.assertEqual(missing, {},
                                 f"{kind}: the sections are told to quote these and the "
                                 f"citation checker rejects them: {missing}")

    def test_the_stamped_ladder_wins_over_recomputation(self):
        """Same reason D61 prefers it: a report is graded against the ladder it was written
        from. Reading `four_ps._volume_ladder` is the one narrow exception to "four_ps is
        never its own evidence" — that key is stamped by Python, not written by the model."""
        from report.claim_support import given_numbers
        econ, ms = VENTURES["transactional"]
        given = given_numbers({
            "economics": econ, "market_sizing": ms,
            "business_model": {"kind": "transactional"},
            "four_ps": {"_volume_ladder": {"unit": "drink", "period": "day",
                                           "rungs": {"planning target": 77.0}}}})
        self.assertTrue(any(abs(77.0 - g) < 0.51 for g in given),
                        "the target the sections were actually shown is not citable")

    def test_narrative_is_still_not_its_own_evidence(self):
        """The exception must stay narrow: prose in a four_ps section still cannot make its
        own numbers citable."""
        from report.claim_support import given_numbers
        econ, ms = VENTURES["transactional"]
        given = given_numbers({
            "economics": econ, "market_sizing": ms,
            "business_model": {"kind": "transactional"},
            "four_ps": {"price": "We recommend 8,888 drinks per day."}})
        self.assertFalse(any(abs(8888.0 - g) < 0.51 for g in given),
                         "a number invented in the prose became citable")


if __name__ == "__main__":
    unittest.main()
