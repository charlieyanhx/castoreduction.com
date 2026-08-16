"""A non-US venture is sized on US federal data, cited to a US federal agency, and passes.

Audit C4. `resolve_annual_spend(category)` takes NO GEOGRAPHY. It returns the BLS Consumer
Expenditure Survey national average and `sourced=True`, and `size_hyperlocal` stamps:

    spend_src    = "BLS Consumer Expenditure Survey"
    spend_origin = "bls"
    spend_is_sourced = True

for a bakery in Lisbon exactly as for one in Austin. The venture ships

    $3,945/household/yr · source: BLS Consumer Expenditure Survey

and D11, D53 and D56 all pass — D53 because the HOUSEHOLDS half already carries an
UNSOURCED label, so the funnel looks half-grounded rather than wrongly-grounded.

`is_non_us_geography` EXISTS and works (Lisbon, London, Paris, Berlin, Tokyo all True;
San Francisco and Austin False). It is consulted in exactly one place — inside
`adjust_spend_for_local_income`, and only after `if not (state_fips and county_fips):
return`, which a non-US address never gets past. So the predicate declines an adjustment
that was never going to happen and the SOURCING decision, made two hundred lines earlier,
never asks. The caveat at :727 fires only when BLS FAILS, i.e. never on the path that hurts.

Two more strings say "US" on a non-US report, same root:

    hyperlocal.py:63   density prompt: "using typical US density for this kind of place"
    hyperlocal.py:647  households: "LLM estimate (UNSOURCED — validate vs US Census ACS)"
                       while `validation_sources_for()` one call away already returns
                       "Eurostat/INE" for the same location

WHAT THIS ASKS FOR, and what it does not. Not a spend figure for Portugal — this codebase
has no source for one, and inventing one would be worse. It asks that a US national average
used outside the US stop being labelled SOURCED and stop citing a US agency, that the
confidence reflect it, and that the reader be told which statistics office to validate
against. The number may stay; the citation may not.
"""
from __future__ import annotations

import unittest

NON_US = ["Lisbon, Portugal", "Shoreditch, London", "Le Marais, Paris",
          "Kreuzberg, Berlin", "Shibuya, Tokyo"]
US = ["Mission District, San Francisco", "Austin, Texas", "Brooklyn, New York"]


class TestThePredicateIsReachableFromTheSourcingDecision(unittest.TestCase):
    """The whole defect is that a working predicate was wired only to the wrong caller.

    The decision belongs in `spend_provenance`, not in `resolve_annual_spend`. That function
    answers "did this come from BLS" — which is what its name claims and what fourteen
    `patch.object(..., return_value=(3945.0, True))` seams assert — and widening its return
    to carry geography would break every one of them to express something it was never
    asked. `spend_provenance` separates the three facts that were conflated.
    """

    def test_a_non_us_venture_gets_no_bls_provenance(self):
        from skills.sizing.hyperlocal import spend_provenance
        for where in NON_US:
            with self.subTest(where=where):
                sourced, origin, label = spend_provenance(3945.0, True, where)
                self.assertFalse(
                    sourced,
                    f"{where}: a US national average was marked SOURCED. The figure may "
                    f"stand in as a proxy; the federal citation may not.")
                self.assertEqual(origin, "bls_national_us",
                                 "a proxy must not borrow the 'bls' origin D53 trusts")
                self.assertNotEqual(label, "BLS Consumer Expenditure Survey")

    def test_a_us_venture_is_unchanged(self):
        """This is a narrowing. BLS grounding for a US venture is the point of #64/#86 and
        must survive intact."""
        from skills.sizing.hyperlocal import spend_provenance
        for where in US + [None, ""]:
            with self.subTest(where=where):
                sourced, origin, label = spend_provenance(3945.0, True, where)
                self.assertTrue(sourced)
                self.assertEqual(origin, "bls")
                self.assertEqual(label, "BLS Consumer Expenditure Survey")

    def test_an_unknown_location_is_treated_as_us(self):
        """Absence of evidence is not evidence of foreignness: an empty address must keep
        today's behaviour rather than silently downgrading every US run."""
        from skills.sizing.hyperlocal import spend_provenance
        self.assertEqual(spend_provenance(3945.0, True, None),
                         spend_provenance(3945.0, True, "Austin, Texas"))

    def test_an_llm_estimate_is_still_an_llm_estimate_everywhere(self):
        """The proxy origin is for a REAL BLS figure used out of area. An unsourced guess
        must not be promoted into it just because the venture is abroad."""
        from skills.sizing.hyperlocal import spend_provenance
        for where in NON_US + US:
            with self.subTest(where=where):
                sourced, origin, _label = spend_provenance(3360.0, False, where)
                self.assertFalse(sourced)
                self.assertEqual(origin, "llm")

    def test_no_figure_at_all_is_neither(self):
        from skills.sizing.hyperlocal import spend_provenance
        sourced, origin, _label = spend_provenance(None, True, "Lisbon, Portugal")
        self.assertFalse(sourced)
        self.assertEqual(origin, "none")


class TestTheSourceStringsNameTheRightCountry(unittest.TestCase):
    def test_the_households_caveat_points_at_the_right_office(self):
        """"validate vs US Census ACS" on a Lisbon report is advice the operator cannot
        follow. `validation_sources_for` already knows the answer."""
        from skills.sizing.hyperlocal import households_source_label
        label = households_source_label(sourced=False, address="Lisbon, Portugal")
        self.assertIn("UNSOURCED", label)
        self.assertNotIn("US Census", label)

    def test_a_us_report_still_says_us_census(self):
        from skills.sizing.hyperlocal import households_source_label
        self.assertIn("US Census",
                      households_source_label(sourced=False, address="Austin, Texas"))

    def test_the_density_prompt_does_not_assert_us_density_abroad(self):
        from skills.sizing.hyperlocal import density_prompt_hint
        self.assertNotIn("typical US density",
                         density_prompt_hint("Lisbon, Portugal"))
        self.assertIn("typical US density", density_prompt_hint("Austin, Texas"))


class TestTheReportSaysSoRatherThanImplyingIt(unittest.TestCase):
    """A downgrade nobody can see is a downgrade that changes nothing for the reader."""

    def test_the_spend_label_says_it_is_a_us_figure_used_abroad(self):
        from skills.sizing.hyperlocal import spend_source_label
        label = spend_source_label(sourced=False, origin="bls_national_us",
                                   address="Lisbon, Portugal")
        low = label.lower()
        self.assertIn("unsourced", low)
        self.assertTrue("us " in low or "u.s." in low,
                        f"the label must say whose average this is: {label}")

    def test_a_us_venture_keeps_the_plain_bls_citation(self):
        from skills.sizing.hyperlocal import spend_source_label
        self.assertEqual(
            spend_source_label(sourced=True, origin="bls", address="Austin, Texas"),
            "BLS Consumer Expenditure Survey")


class TestTheDowngradeIsToldAccurately(unittest.TestCase):
    """The proxy must not be described as an LLM guess. That would be a second inaccuracy
    correcting the first: the figure IS survey data, from a survey of the wrong country, and
    a reader judging how far off it might be needs to know which."""

    def _notes(self, address, origin):
        from skills.sizing import hyperlocal as H
        srcs = H._validation_note_sources(address)
        notes = []
        if origin == "bls_national_us":
            notes.append(
                "Annual spend/household is the US BLS Consumer Expenditure Survey national "
                "average used as a PROXY — this venture is outside the US and no local "
                "household-expenditure survey was consulted, so the per-household figure "
                f"carries unknown error. Validate against {srcs['spend']} before "
                "relying on TAM.")
        return notes

    def test_the_note_text_exists_for_the_proxy_origin(self):
        """Pinned against the source so the branch cannot be deleted silently."""
        import inspect

        from skills.sizing import hyperlocal as H
        src = inspect.getsource(H.size_hyperlocal)
        self.assertIn('spend_origin == "bls_national_us"', src,
                      "the proxy case fell back to the 'LLM estimate' wording")
        self.assertIn("used as a PROXY", src)

    def test_the_validation_advice_points_abroad(self):
        from skills.sizing.hyperlocal import _validation_note_sources
        srcs = _validation_note_sources("Lisbon, Portugal")
        self.assertNotIn("BLS", srcs["spend"])
        self.assertIn("BLS", _validation_note_sources("Austin, Texas")["spend"])


class TestTheGateThatShouldHaveCaughtIt(unittest.TestCase):
    """D11 exists for exactly this — "non-US venture avoids US-only sources" — and passed
    the Lisbon bakery, because it never inspected the spend side's provenance."""

    def _report(self, location, spend_origin, spend_sourced):
        """Field names taken from a stored artifact: geography lives on `profile.geography`
        and the trade-area location on `market_sizing._hyperlocal_location`."""
        return {
            "profile": {"geography": location, "summary": ""},
            "market_sizing": {
                "scale": "hyperlocal", "_hyperlocal_location": location,
                # The advice strings have ALWAYS been right for a non-US venture —
                # `validation_sources_for()` returns Eurostat/INE — which is exactly why a
                # gate that inspects only advice has never fired on a wrongly-sourced TAM.
                "sources_to_validate": [
                    "national statistics office household data (e.g. Eurostat/INE in the EU)",
                    "national household expenditure survey (category spend/household)",
                ],
                "spend_per_hh_source": ("BLS Consumer Expenditure Survey" if spend_sourced
                                        else "LLM estimate (UNSOURCED)"),
                "data_origin": {"spend": spend_origin},
                "tam_usd": 117_000_000.0,
            },
        }

    def test_it_fails_a_non_us_venture_carrying_bls_spend(self):
        from gates import d11_currency_sources
        f = d11_currency_sources(
            self._report("Lisbon, Portugal", "bls", True), None)
        self.assertIs(f.ok, False,
                      f"D11 passed a Lisbon venture sized on BLS: {f.detail}")

    def test_it_passes_a_non_us_venture_that_labelled_itself_honestly(self):
        from gates import d11_currency_sources
        f = d11_currency_sources(
            self._report("Lisbon, Portugal", "bls_national_us", False), None)
        self.assertIsNot(f.ok, False, f.detail)

    def test_a_us_venture_on_bls_is_fine(self):
        from gates import d11_currency_sources
        f = d11_currency_sources(self._report("Austin, Texas", "bls", True), None)
        self.assertIsNot(f.ok, False, f.detail)


if __name__ == "__main__":
    unittest.main()
