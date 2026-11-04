"""A 3-location chain is sized as one 3 km catchment, and the gate attests the skill that never ran.

Audit C5. `plan.size_by_scale` routes BOTH `hyperlocal` and `regional` into
`size_hyperlocal(address=<one location>)`. `size_regional` is reachable only from
`skills/sizing/dispatch.py`, which is admittedly unwired. So a regional venture publishes
one trade area as its whole market — no ×n_locations, no multi-site footprint — while the
classifier's `sizing_skill` says `size_regional` and `plan.py:291` stamps the report note
from that name.

D52 exists to catch exactly this: "the sizing skill the classifier NAMED must be the one
that produced the numbers." It passes, because it checks that *a* footprint exists —

    footprint = {radius_m, catchment_km2, trade_area_households}
    if footprint: return Finding(True, f"{skill} ran: {footprint}")

— and `size_hyperlocal` leaves that footprint whichever venture it was pointed at. The gate
confirms a trade area was measured; it never asks which skill measured it, so a hyperlocal
model standing in for a regional one reads as the regional model running correctly.

D49, the density sanity check that would notice a county-scale household count inside a 3 km
radius, is N/A here by its own allow-list:

    if (ms.get("scale") or "").lower() not in ("hyperlocal", "trade_area", ""): return N/A

A regional report carries `scale: "regional"`, so the one gate that could have caught the
substitution numerically declines to look. Two gates, both blind, for different reasons.

WHAT THIS FIXES, and what it does not. It does not wire `size_regional` — that is the real
repair and a much larger change. It converts a SILENT FALSE CLAIM into a LOUD FAILURE:
`size_by_scale` stamps which skill actually ran, D52 asserts equality with the named one
rather than the existence of a footprint, and D49 stops excusing itself on a scale label
when the report carries a radius and a household count it can check. A regional venture then
fails its gates and says why, instead of shipping a one-site TAM under a multi-site name.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


def _payload():
    """What size_hyperlocal returns — a single trade area, whatever it was pointed at."""
    return {
        "tam_usd": 4_000_000.0, "sam_usd": 1_200_000.0, "som_usd": 240_000.0,
        "method": "trade_area_catchment", "figures": [],
        "households": 12_000, "trade_area_households": 12_000,
        "radius_m": 3000, "catchment_km2": 28.3, "households_sourced": True,
    }


class _Ev:
    skeleton = False
    error = None

    def __init__(self, payload):
        self.payload = payload


def _size(scale, skill):
    from unittest.mock import MagicMock

    import plan
    dec = {"scale": scale, "sizing_skill": skill}
    with patch("skills.sizing.hyperlocal.size_hyperlocal",
               return_value=_Ev(_payload())) as m:
        out = plan.size_by_scale(
            dec, "A cafe chain located in the Mission District, San Francisco.",
            {"category": "coffee shop", "geography": "San Francisco, CA"})
    assert isinstance(m, MagicMock)
    return out


class TestTheReportRecordsWhichSkillActuallyRan(unittest.TestCase):
    def test_a_hyperlocal_venture_stamps_size_hyperlocal(self):
        out = _size("hyperlocal", "size_hyperlocal")
        self.assertEqual(out.get("sizing_skill_ran"), "size_hyperlocal")

    def test_a_regional_venture_stamps_what_really_ran_not_what_was_chosen(self):
        """The whole point. The classifier said size_regional; size_hyperlocal ran."""
        out = _size("regional", "size_regional")
        self.assertEqual(
            out.get("sizing_skill_ran"), "size_hyperlocal",
            "the report must record the skill that produced the numbers, not the one the "
            "classifier nominated")


class TestD52AssertsEqualityNotExistence(unittest.TestCase):
    def _report(self, scale, chosen, ran):
        ms = dict(_payload(), scale=scale)
        if ran is not None:
            ms["sizing_skill_ran"] = ran
        return {"market_scale": {"scale": scale, "sizing_skill": chosen},
                "market_sizing": ms}

    def _d52(self, r):
        from gates import d52_chosen_sizing_skill_actually_ran
        return d52_chosen_sizing_skill_actually_ran(r, None)

    def test_a_substituted_skill_fails(self):
        f = self._d52(self._report("regional", "size_regional", "size_hyperlocal"))
        self.assertIs(f.ok, False,
                      f"D52 attested size_regional against a hyperlocal footprint: {f.detail}")
        self.assertIn("size_regional", f.detail)
        self.assertIn("size_hyperlocal", f.detail)

    def test_the_matching_case_passes(self):
        f = self._d52(self._report("hyperlocal", "size_hyperlocal", "size_hyperlocal"))
        self.assertIs(f.ok, True, f.detail)

    def test_an_artifact_without_the_stamp_falls_back_to_the_old_check(self):
        """Every stored report predates this key. A gate that fails 18 archived artifacts
        for lacking a field invented today teaches people to ignore it."""
        f = self._d52(self._report("hyperlocal", "size_hyperlocal", None))
        self.assertIsNot(f.ok, False, f.detail)

    def test_no_footprint_at_all_still_fails(self):
        """The original defect — run1's model-narrated TAM — must stay caught."""
        r = {"market_scale": {"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"},
             "market_sizing": {"scale": "hyperlocal", "tam": {"mid": 4e6}}}
        self.assertIs(self._d52(r).ok, False)


class TestD49LooksAtAnyReportCarryingATradeArea(unittest.TestCase):
    """It declined on `scale: regional` — the exact reports where a one-site catchment is
    standing in for many, i.e. where an implausible density is most diagnostic."""

    def _d49(self, scale, households, radius_m=3000):
        from gates import d49_trade_area_matches_its_radius
        return d49_trade_area_matches_its_radius(
            {"market_sizing": {"scale": scale, "method": "trade_area_catchment",
                               "trade_area_households": households,
                               "radius_m": radius_m}}, None)

    def test_a_regional_report_with_a_county_scale_count_is_caught(self):
        f = self._d49("regional", 3_300_000)
        self.assertIs(f.ok, False,
                      f"a county count in a 3 km catchment passed on scale=regional: "
                      f"{f.detail}")

    def test_a_regional_report_with_a_plausible_count_passes(self):
        self.assertIs(self._d49("regional", 12_000).ok, True)

    def test_hyperlocal_behaviour_is_unchanged(self):
        self.assertIs(self._d49("hyperlocal", 3_300_000).ok, False)
        self.assertIs(self._d49("hyperlocal", 12_000).ok, True)

    def test_a_report_with_no_trade_area_is_still_not_applicable(self):
        """The allow-list is replaced by "does it carry the numbers I check", not by
        "check everything" — a digital venture has no catchment to judge."""
        from gates import d49_trade_area_matches_its_radius
        f = d49_trade_area_matches_its_radius(
            {"market_sizing": {"scale": "national_digital", "tam_usd": 4e9}}, None)
        self.assertIsNone(f.ok, f.detail)


if __name__ == "__main__":
    unittest.main()
