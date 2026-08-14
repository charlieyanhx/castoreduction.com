"""A SOURCED receipts-per-establishment benchmark (#91), and the traps in getting one.

The headline SOM for every hyperlocal venture is `min(unit_revenue, sam)`, and
`unit_revenue` is an LLM guess that MOVED 67% between two runs of the same venture
(run14 $390,000 -> run15 $650,000). #83 made that visible. This makes it retrievable:
the 2022 Economic Census publishes total receipts and establishment counts by NAICS by
county, so "what does an establishment in this industry take here" is a fetch, not a
guess.

MEASURED live with the repo's CENSUS_API_KEY, the live venture's cell:

    /data/2022/ecnbasic?get=NAME,NAICS2022_LABEL,ESTAB,RCPTOT,RCPTOT_F
                       &for=county:075&in=state:06&NAICS2022=722515
    -> ["San Francisco County, California","Snack and Nonalcoholic Beverage Bars",
        "525","464115",null,...]
    -> 464115 * 1000 / 525 = $884,029 per establishment, reference year 2022

THE FOUR TRAPS, each pinned by a test below because each one ships a plausible number:

  SUPPRESSION THAT LOOKS LIKE ZERO. Across all 1,818 county rows for 722515, 971 carry
  RCPTOT_F="D" (withheld), and 124 of those return RCPTOT as the literal string "0"
  WITH ESTAB > 0. Verified instance: Santa Barbara County, 158 establishments, "0", "D".
  A naive RCPTOT/ESTAB computes $0 per establishment and reports it as sourced. RCPTOT_F
  is not even listed in the dataset's variables.json — it must be asked for by name.

  BROADENING THE INDUSTRY. When the 6-digit cell is suppressed the tempting fallback is a
  shorter NAICS code. Measured on the SF cell: 722515 $884,029, 72251 $1,368,562 (+55%),
  7225 $1,368,562 (identical — the 5-digit rung is a duplicate, not a softer step), 722
  +58%, 72 +119%. Broadening the GEOGRAPHY instead costs 6x less: CBSA +10%, state +13%,
  national -9%. So the ladder walks geography and never touches the industry code.

  THE CHAIN MEANS NOTHING WITHOUT ITS OPERANDS. A single adjusted dollar figure cannot be
  checked against the dataset it claims to come from: following the citation for
  "$643,243" finds $884,029. Every operand must ship with the number, exactly as the
  income-adjusted spend figure already does.

  THE COMPOSITION RATIO IS NOT THE RECEIPTS SHARE. ecnsize gives single-unit firms
  $28,366,095K over 55,352 establishments and all firms $62,769,408K over 78,110. The
  useful ratio is of per-establishment MEANS, 512,467/803,603 = 0.6377. The receipts
  SHARE is 28,366,095/62,769,408 = 0.4519 — a different quantity. Publishing 0.638 while
  describing it as a share of receipts is a checkable falsehood in the one sentence meant
  to make the number checkable.

WHAT THIS TOOL DELIBERATELY DOES NOT DECIDE: whether the figure is good enough to ground
the anchor. It reports which rung answered and what substituting that rung costs; the
sizing skill owns the policy. A county mean is a fact about a county either way.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

# The live SF cell, verbatim, as the API returns it.
_SF_ROWS = [["NAME", "NAICS2022", "NAICS2022_LABEL", "ESTAB", "RCPTOT", "RCPTOT_F",
             "NAICS2022", "state", "county"],
            ["San Francisco County, California", "722515",
             "Snack and Nonalcoholic Beverage Bars", "525", "464115", None,
             "722515", "06", "075"]]

# Santa Barbara County — suppressed, and the suppressed value arrives as "0".
_SUPPRESSED_ROWS = [["NAME", "NAICS2022", "NAICS2022_LABEL", "ESTAB", "RCPTOT",
                     "RCPTOT_F", "NAICS2022", "state", "county"],
                    ["Santa Barbara County, California", "722515",
                     "Snack and Nonalcoholic Beverage Bars", "158", "0", "D",
                     "722515", "06", "083"]]

_STATE_ROWS = [["NAME", "NAICS2022", "NAICS2022_LABEL", "ESTAB", "RCPTOT", "RCPTOT_F",
                "NAICS2022", "state"],
               ["California", "722515", "Snack and Nonalcoholic Beverage Bars",
                "6013", "6001674", None, "722515", "06"]]


def _tool():
    """Via the registry, exactly as production must call it: a direct import bypasses the
    @tool wrapper that records the call, and the ledger then under-reports the source."""
    from tools.registry import get_tool
    import tools.geo  # noqa: F401  (registers the tool)
    return get_tool("census_receipts_per_establishment")


class TestTheCountyFigure(unittest.TestCase):
    def test_it_computes_receipts_per_establishment_in_dollars(self):
        """RCPTOT is in $1,000s. Forgetting that is a 1000x error, which at least
        announces itself — unlike everything else in this file."""
        with patch("tools.geo._http_json", return_value=_SF_ROWS):
            ev = _tool().fn(naics="722515", state_fips="06", county_fips="075")
        self.assertFalse(ev.skeleton, ev.error)
        self.assertAlmostEqual(ev.payload["receipts_per_establishment_usd"],
                               464115 * 1000 / 525, places=0)
        self.assertEqual(ev.payload["establishments"], 525)

    def test_it_reports_which_rung_answered(self):
        with patch("tools.geo._http_json", return_value=_SF_ROWS):
            ev = _tool().fn(naics="722515", state_fips="06", county_fips="075")
        self.assertEqual(ev.payload["rung"], "county")

    def test_it_carries_the_operands_a_reader_needs_to_recompute_it(self):
        """The whole point. A lone dollar figure cannot be checked against the dataset."""
        with patch("tools.geo._http_json", return_value=_SF_ROWS):
            p = _tool().fn(naics="722515", state_fips="06",
                           county_fips="075").payload
        for k in ("receipts_usd", "establishments", "vintage", "geography_name",
                  "naics", "naics_label", "dataset", "url"):
            self.assertIn(k, p, f"{k} missing — the citation is not reproducible")
        self.assertEqual(p["vintage"], 2022)
        self.assertIn("ecnbasic", p["url"])
        self.assertEqual(p["naics_label"], "Snack and Nonalcoholic Beverage Bars")

    def test_the_geography_is_named_not_implied(self):
        """A reader must be able to see that this is a COUNTY figure, not their block."""
        with patch("tools.geo._http_json", return_value=_SF_ROWS):
            p = _tool().fn(naics="722515", state_fips="06",
                           county_fips="075").payload
        self.assertIn("San Francisco County", p["geography_name"])


class TestSuppressionIsNeverReadAsZero(unittest.TestCase):
    """The highest-severity trap: 124 counties return "0" alongside RCPTOT_F="D"."""

    def test_a_D_flag_is_not_a_measurement(self):
        with patch("tools.geo._http_json", return_value=_SUPPRESSED_ROWS):
            ev = _tool().fn(naics="722515", state_fips="06", county_fips="083")
        self.assertTrue(ev.skeleton, "a withheld cell was published as a figure")
        self.assertNotEqual((ev.payload or {}).get("receipts_per_establishment_usd"), 0)

    def test_the_suppression_flag_is_requested_by_name(self):
        """RCPTOT_F is absent from the dataset's variables.json but returned when named.
        Not asking for it means never knowing the value was withheld."""
        seen = {}

        def spy(method, url, **kw):
            seen.update(kw.get("params") or {})
            return _SF_ROWS

        with patch("tools.geo._http_json", side_effect=spy):
            _tool().fn(naics="722515", state_fips="06", county_fips="075")
        self.assertIn("RCPTOT_F", seen.get("get", ""))

    def test_zero_establishments_is_not_a_division(self):
        rows = [_SF_ROWS[0], ["Nowhere County", "722515", "L", "0", "0", None,
                              "722515", "06", "999"]]
        with patch("tools.geo._http_json", return_value=rows):
            ev = _tool().fn(naics="722515", state_fips="06", county_fips="999")
        self.assertTrue(ev.skeleton)

    def test_an_empty_response_is_not_a_zero(self):
        """MEASURED: a NAICS 2017 code against the 2022 dataset returns an empty body."""
        for empty in (None, [], [_SF_ROWS[0]]):
            with patch("tools.geo._http_json", return_value=empty):
                ev = _tool().fn(naics="459210", state_fips="06", county_fips="075")
            self.assertTrue(ev.skeleton)


class TestTheLadderWalksGeographyNeverIndustry(unittest.TestCase):
    def test_a_suppressed_county_falls_back_to_the_state(self):
        with patch("tools.geo._http_json",
                   side_effect=[_SUPPRESSED_ROWS, _STATE_ROWS]):
            ev = _tool().fn(naics="722515", state_fips="06", county_fips="083")
        self.assertFalse(ev.skeleton, ev.error)
        self.assertEqual(ev.payload["rung"], "state")

    def test_the_naics_code_is_never_broadened(self):
        """+55% at the 5-digit rung, +119% at the 2-digit. Never worth it."""
        codes = []

        def spy(method, url, **kw):
            codes.append((kw.get("params") or {}).get("NAICS2022"))
            return _SUPPRESSED_ROWS

        with patch("tools.geo._http_json", side_effect=spy):
            _tool().fn(naics="722515", state_fips="06", county_fips="083")
        self.assertTrue(codes)
        self.assertEqual(set(codes), {"722515"},
                         f"the industry code was broadened: {codes}")

    def test_a_substituted_rung_says_what_the_substitution_costs(self):
        """MEASURED against the 802 counties where county truth IS published: substituting
        the state mean is off by a median 2.29x for counties with 1-9 establishments,
        1.44x for 10-24, 1.02x at 25-99. Suppression targets exactly the small counties
        (median ESTAB 5 suppressed vs 12 usable), so the state rung disproportionately
        serves the population where it is worst. A rung that cannot say this must not be
        allowed to look like the county figure."""
        with patch("tools.geo._http_json",
                   side_effect=[_SUPPRESSED_ROWS, _STATE_ROWS]):
            p = _tool().fn(naics="722515", state_fips="06",
                           county_fips="083").payload
        self.assertTrue(p.get("substitution"),
                        "a state-level substitution shipped with no caveat")
        self.assertIn("suppressed", " ".join(p.get("rungs_tried") or []).lower())

    def test_the_county_rung_carries_no_substitution_caveat(self):
        with patch("tools.geo._http_json", return_value=_SF_ROWS):
            p = _tool().fn(naics="722515", state_fips="06",
                           county_fips="075").payload
        self.assertFalse(p.get("substitution"))

    def test_it_gives_up_rather_than_inventing(self):
        with patch("tools.geo._http_json", return_value=_SUPPRESSED_ROWS):
            ev = _tool().fn(naics="722515", state_fips="06", county_fips="083")
        self.assertTrue(ev.skeleton)
        self.assertIsNone((ev.payload or {}).get("receipts_per_establishment_usd"))


class TestTheIndependentAdjustment(unittest.TestCase):
    """A raw area mean mixes Starbucks with the corner cafe."""

    def test_the_ratio_is_of_per_establishment_means(self):
        from tools.geo import single_unit_receipts_ratio

        rows = [["NAME", "NAICS2022", "ESTAB", "RCPTOT", "SUMUFI", "SUMUFI_LABEL",
                 "NAICS2022", "us"],
                ["United States", "722515", "78110", "62769408", "001", "All firms",
                 "722515", "1"],
                ["United States", "722515", "55352", "28366095", "200",
                 "Single unit firms", "722515", "1"]]
        with patch("tools.geo._http_json", return_value=rows):
            out = single_unit_receipts_ratio("722515")
        self.assertAlmostEqual(out["ratio"], (28366095 / 55352) / (62769408 / 78110), 4)
        self.assertAlmostEqual(out["ratio"], 0.6377, places=3)

    def test_it_publishes_both_operands_so_the_ratio_is_reproducible(self):
        """0.638 stated bare invites a reader to compute the receipts SHARE (0.4519),
        find a different number, and conclude the report is wrong."""
        from tools.geo import single_unit_receipts_ratio

        rows = [["NAME", "NAICS2022", "ESTAB", "RCPTOT", "SUMUFI", "SUMUFI_LABEL",
                 "NAICS2022", "us"],
                ["United States", "722515", "78110", "62769408", "001", "All firms",
                 "722515", "1"],
                ["United States", "722515", "55352", "28366095", "200",
                 "Single unit firms", "722515", "1"]]
        with patch("tools.geo._http_json", return_value=rows):
            out = single_unit_receipts_ratio("722515")
        self.assertAlmostEqual(out["single_unit_per_establishment_usd"],
                               28366095 / 55352 * 1000, places=0)
        self.assertAlmostEqual(out["all_firms_per_establishment_usd"],
                               62769408 / 78110 * 1000, places=0)
        self.assertEqual(out["scope"], "national")

    def test_a_missing_split_returns_nothing_rather_than_one(self):
        """Silently defaulting the ratio to 1.0 would publish the chain-inclusive mean as
        an independent's revenue — the exact error the split exists to prevent."""
        from tools.geo import single_unit_receipts_ratio

        with patch("tools.geo._http_json", return_value=None):
            self.assertIsNone(single_unit_receipts_ratio("722515"))


if __name__ == "__main__":
    unittest.main()
