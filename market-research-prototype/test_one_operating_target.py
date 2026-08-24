"""The volume ladder names a floor and a roof and never says what to plan for (#97).

MEASURED across two live runs of the same venture, with the volume_ladder reminder
CONFIRMED FIRED on both (_reminders_fired.volume_ladder is True):

  run17  price says "targeting 250 drinks per day"
         place says "targeting 150 drinks per day initially (operator decision)"
         promotion says "reach 150 daily drinks" (narrative AND key_takeaways)
         -> two sections of ONE report recommend operating volumes 67% apart

  run18  every daily-volume figure in all four sections is one of exactly two numbers:
         120.4 (break-even) and 320 (the obtainable ceiling)
         -> no section states an operating target at all

Those look like different bugs. They are one bug. `_r_volume_ladder` publishes a RANGE —
"break-even ~= N/day, obtainable ceiling ~= M/day" plus a HARD RULE that any target must be
"between break-even and the obtainable ceiling". 150 and 250 both satisfy that rule, so
run17's sections were each individually obedient while contradicting each other; run18's
sections declined to pick and left the operator with a floor and a roof. #76 fixed "five
targets outside the model"; it never addressed "no agreed target inside it".

AND THE MODEL ALREADY KNOWS THE ANSWER. financials.py owns the ramp: for a physical
transactional venture y1 = 60% of the year-3 ceiling, and the base ceiling IS som_mid.
Measured on run18: 643,243 x 0.60 = $385,946, exactly the base-case year-1 revenue the
report published, which is 194.9 units/day at $5.50 over 360 days. The number the sections
should be writing prose around already exists — it is simply never handed to them.

ONE OWNER, NOT TWO. The target is computed in financials.py, beside the ramp it depends on,
and the reminder asks for it. Recomputing 0.60 in four_ps.py would be a second owner of one
fact, which is the bug this codebase keeps relearning — and 4Ps runs BEFORE financials in
run_plan, so the reminder cannot simply read the scenarios table.
"""
from __future__ import annotations

import unittest


class TestThePlanningTargetHasOneOwner(unittest.TestCase):
    def test_it_is_the_base_case_year_one_volume(self):
        """MEASURED on run18: SOM $643,243, $5.50/drink, retail ramp -> $385,946 in year 1,
        194.9 drinks/day. The published scenarios table says exactly that."""
        from financials import planning_target_units_per_day

        t = planning_target_units_per_day(som_usd=643243.0, price_per_unit=5.50,
                                          market_scale="hyperlocal", model="transactional")
        self.assertAlmostEqual(t["units_per_day"], 194.9, places=1)

    def test_it_uses_the_same_ramp_the_financials_table_uses(self):
        """A second owner of the ramp is how the report ends up disagreeing with itself."""
        from financials import _ramp_for, planning_target_units_per_day

        ramp, _ = _ramp_for("hyperlocal", "transactional")
        t = planning_target_units_per_day(som_usd=643243.0, price_per_unit=5.50,
                                          market_scale="hyperlocal", model="transactional")
        self.assertAlmostEqual(t["revenue_usd"], 643243.0 * ramp[1], places=0)

    def test_a_non_physical_venture_gets_the_s_curve_not_the_retail_ramp(self):
        from financials import planning_target_units_per_day

        t = planning_target_units_per_day(som_usd=643243.0, price_per_unit=5.50,
                                          market_scale="national", model="subscription")
        self.assertAlmostEqual(t["revenue_usd"], 643243.0 * 0.08, places=0)

    def test_it_returns_none_rather_than_a_number_it_cannot_justify(self):
        from financials import planning_target_units_per_day

        for bad in ({"som_usd": 0, "price_per_unit": 5.5},
                    {"som_usd": 643243.0, "price_per_unit": 0},
                    {"som_usd": None, "price_per_unit": 5.5}):
            self.assertIsNone(planning_target_units_per_day(
                market_scale="hyperlocal", model="transactional", **bad))

    def test_it_says_where_the_number_came_from(self):
        """The sections quote it in prose, so it needs an attributable phrasing."""
        from financials import planning_target_units_per_day

        t = planning_target_units_per_day(som_usd=643243.0, price_per_unit=5.50,
                                          market_scale="hyperlocal", model="transactional")
        self.assertIn("year 1", t["basis"].lower())


class TestTheLadderCarriesTheTarget(unittest.TestCase):
    _ECON = {"unit": "drink", "price_per_unit": 5.50, "break_even_units_per_day": 120.4}
    _MS = {"som_usd": 643243.0, "scale": "hyperlocal"}

    def _ladder(self, **kw):
        from four_ps import _r_volume_ladder
        facts = {"economics": self._ECON, "market_sizing": self._MS,
                 "business_model_kind": "transactional"}
        facts.update(kw)
        return _r_volume_ladder(facts)

    def _rungs(self):
        """The model's own rungs. These used to be hardcoded as "195" and "320" — the
        rounded, 365-day spellings of the day — and both changed under a fix that made the
        prompt exact and put it on the same 360 open days as every ramp. A test that pins a
        rendering rather than the number is a test that has to be edited to stay true, so
        it asks the model instead."""
        from financials import ladder_inputs
        return ladder_inputs(self._ECON, self._MS, "transactional")["rungs"]

    def test_the_ladder_states_a_planning_target(self):
        from four_ps import ladder_number
        self.assertIn(ladder_number(self._rungs()["planning target"]),
                      self._ladder().replace(",", ""),
                      "the ladder still gives only a floor and a roof")

    def test_all_three_rungs_are_present(self):
        from four_ps import ladder_number
        text = self._ladder().replace(",", "")
        rungs = self._rungs()
        self.assertEqual(sorted(rungs), ["break-even", "obtainable ceiling",
                                         "planning target"])
        for name, value in rungs.items():
            self.assertIn(ladder_number(value), text,
                          f"rung {name} ({value:g}) missing from the ladder")

    def test_the_rule_names_the_target_not_just_the_range(self):
        """MEASURED: 150 and 250 both sit inside the range, so a range-only rule let two
        sections of one report disagree by 67% while both obeyed it."""
        low = self._ladder().lower()
        self.assertIn("target", low)

    def test_it_degrades_to_the_old_range_when_the_target_is_unavailable(self):
        """No price, no target — but break-even and the ceiling still matter."""
        econ = dict(self._ECON)
        econ.pop("price_per_unit")
        text = self._ladder(economics=econ)
        self.assertTrue(text == "" or "break-even" in text.lower())


class TestD61NoInventedVolumes(unittest.TestCase):
    """The gate that makes it non-latent. run18 happened not to invent a number; nothing
    stopped it, and run17 did."""

    def _report(self, sections: dict, ladder=(120.4, 194.9, 320.4)):
        be, target, ceiling = ladder
        return {
            "economics": {"unit": "drink", "price_per_unit": 5.50,
                          "break_even_units_per_day": be},
            "market_sizing": {"method": "trade_area_catchment", "som_usd": 643243.0,
                              "scale": "hyperlocal"},
            "four_ps": dict(sections, _volume_target_units_per_day=target),
        }

    def _d61(self, r):
        from gates import d61_volume_targets_match_the_ladder
        return d61_volume_targets_match_the_ladder(r, None)

    def test_the_run17_contradiction_is_caught(self):
        f = self._d61(self._report({
            "price": "We recommend targeting 250 drinks per day.",
            "place": "We recommend targeting 150 drinks per day initially.",
            "promotion": "Reach 150 daily drinks."}))
        self.assertFalse(f.ok, "two sections 67% apart were accepted")
        self.assertIn("250", f.detail)

    def test_the_run18_shape_passes(self):
        f = self._d61(self._report({
            "price": "Break-even volume is 120.4 drinks per day and the obtainable "
                     "ceiling is 320 drinks per day.",
            "place": "Move from the 120.4 drinks/day break-even toward the 320 "
                     "drinks/day ceiling."}))
        self.assertTrue(f.ok, f.detail)

    def test_the_planning_target_itself_passes(self):
        f = self._d61(self._report({
            "place": "Plan for 194.9 drinks per day in year one."}))
        self.assertTrue(f.ok, f.detail)

    def test_rounding_to_a_readable_number_is_allowed(self):
        """Prose says 195, not 194.9. Demanding exactness would make the gate cry wolf on
        good writing, which is how a gate gets switched off."""
        f = self._d61(self._report({"place": "Plan for 195 drinks per day."}))
        self.assertTrue(f.ok, f.detail)

    def test_a_number_between_the_rungs_still_fails(self):
        """The exact hole in the old HARD RULE: inside the range is not the same as agreed."""
        f = self._d61(self._report({"place": "Target 200 drinks per day."}))
        self.assertFalse(f.ok)

    def test_it_is_not_applicable_without_a_ladder(self):
        r = self._report({"place": "Target 200 drinks per day."})
        r["economics"] = {}
        r["market_sizing"] = {}
        r["four_ps"].pop("_volume_target_units_per_day")
        self.assertIsNone(self._d61(r).ok)

    def test_it_is_not_applicable_when_no_section_states_a_volume(self):
        f = self._d61(self._report({"place": "Focus on community events."}))
        self.assertIsNone(f.ok)

    def test_a_marketing_cadence_is_not_an_operating_volume(self):
        """MEASURED (job 6d1e27a2, 2026-08-21): place wrote 'Set a target of 5 creator
        visits per month' — influencer visits, a marketing cadence — and the report was
        WITHHELD because 5/month is not a rung. A qualified noun phrase (a non-demand
        modifier between number and unit noun) is a slice or cadence the ladder cannot
        grade; the gate must not demand rung-equality of it."""
        f = self._d61(self._report({
            "place": "Plan for 195 drinks per day. Set a target of 5 creator visits "
                     "per month to drive awareness."}))
        self.assertTrue(f.ok, f.detail)

    def test_a_channel_slice_below_the_ceiling_passes(self):
        # c48497fa wrote '15 referral bookings monthly' — a channel contribution,
        # legitimately not the total operating volume.
        f = self._d61(self._report({
            "place": "Plan for 195 drinks per day, including 15 referral drinks "
                     "per month from partner cafes."}))
        self.assertTrue(f.ok, f.detail)

    def test_a_slice_cannot_exceed_total_demand(self):
        """run6 wrote '100 commuter drinks daily' against a 13.5/day ceiling. A segment
        of demand cannot be bigger than all demand — the one thing the ladder CAN say
        about a qualified claim."""
        f = self._d61(self._report({
            "place": "Capture 999 commuter drinks per day at the morning peak."}))
        self.assertFalse(f.ok)
        self.assertIn("exceed", f.detail.lower())

    def test_a_demand_noun_modifier_is_still_a_bare_claim(self):
        # run13 wrote '100 drink sales per day' — both words are demand nouns; that IS
        # the operating volume, and an invented one still fails.
        f = self._d61(self._report({
            "place": "Target 100 drink sales per day."}))
        self.assertFalse(f.ok)


if __name__ == "__main__":
    unittest.main()
