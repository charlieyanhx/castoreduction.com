"""
The D09 class: gates that check a disclosure EXISTS, not that the report OBEYS it.

The R4 panel found this and it is reproducible. On out/wave4_corpus/174ae091:

    market_sizing.validation.passed = False
    market_sizing.publishable       = False
    the report renders "⚠ Failed validation — figures withheld"
    ...and, in the same document, "a massive $1.22B TAM"
    ...and a 65/100 market-opportunity score built on that number

    gates.d09_publishable_gated  ->  ok, "gated correctly"

D09 checked two things: that `publishable` is False, and that a withhold banner
exists in the html. Both were true. It never checked that the withheld number stayed
OUT of the prose — so the gate verified that a disclaimer was printed, not that the
report honoured it. A buyer reads the number and the score; the banner does not
un-ring that bell.

This is not one venture. All 4 of the 16 that fail validation restate the withheld
TAM as a confident claim in `viability`, which is exactly the field that then drives
the market-opportunity score.

The fix keeps DISCLOSURE legal and makes ASSERTION illegal: the sizing table may
still show the figure next to its warning (that is the disclosure), but narrative
prose — viability reasoning, 4Ps sections, the executive summary — may not restate it
as fact.
"""
from __future__ import annotations

import copy
import glob
import json
import os
import unittest

import gates

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


def _load(slug):
    r = json.load(open(f"out/wave4_corpus/{slug}.json"))["result"]
    html = open(f"out/wave4_corpus/{slug}.html", encoding="utf-8", errors="replace").read()
    return r, html


class TestTheRealCase(unittest.TestCase):
    """The exact report the panel flagged."""

    @unittest.skipIf(not os.path.exists("out/wave4_corpus/174ae091.json"), "no corpus")
    def test_d09_fails_on_the_report_that_published_a_withheld_tam(self):
        r, html = _load("174ae091")
        self.assertIs((r["market_sizing"]["validation"] or {}).get("passed"), False)
        f = gates.d09_publishable_gated(r, html)
        self.assertIs(f.ok, False, "D09 still passes the report the panel flagged")
        self.assertIn("1.22", f.detail.replace(",", ""))

    @unittest.skipIf(not _CORPUS, "no corpus")
    def test_every_failed_validation_venture_is_now_caught(self):
        """4/16 fail validation; all 4 restate the withheld TAM in viability prose."""
        checked = 0
        for p in _CORPUS:
            r, html = _load(os.path.basename(p)[:-5])
            if ((r.get("market_sizing") or {}).get("validation") or {}).get("passed") is not False:
                continue
            checked += 1
            self.assertIs(gates.d09_publishable_gated(r, html).ok, False,
                          f"{os.path.basename(p)} not caught")
        self.assertEqual(checked, 4, "corpus changed — recheck the premise")


class TestDisclosureStaysLegal(unittest.TestCase):
    """Withholding must remain possible. A gate that no report can satisfy is not a
    gate, it is a wall — the pipeline would have no way to ship a caveated sizing."""

    def _r(self, **over):
        r = {"market_sizing": {"validation": {"passed": False}, "publishable": False,
                               "tam": {"mid": 1_220_000_000}},
             "viability": {"summary": "Unit economics are unproven."},
             "four_ps": {}}
        r["market_sizing"].update(over)
        return r

    def test_a_properly_withheld_report_passes(self):
        html = "<p>⚠ Failed validation — figures withheld. TAM $1.22B (do not rely).</p>"
        self.assertIs(gates.d09_publishable_gated(self._r(), html).ok, True)

    def test_restating_it_in_viability_prose_fails(self):
        r = self._r()
        r["viability"]["summary"] = "Strong potential given a massive $1.22B TAM."
        html = "<p>⚠ Failed validation — figures withheld.</p>"
        self.assertIs(gates.d09_publishable_gated(r, html).ok, False)

    def test_restating_it_in_a_4ps_narrative_fails(self):
        r = self._r()
        r["four_ps"] = {"product": {"narrative": "The $1.22B market rewards vetting."}}
        html = "<p>⚠ Failed validation — figures withheld.</p>"
        self.assertIs(gates.d09_publishable_gated(r, html).ok, False)

    def test_a_different_number_in_prose_is_not_a_violation(self):
        """Only the WITHHELD figure is illegal. Other numbers are the report's job."""
        r = self._r()
        r["viability"]["summary"] = "Average booking is $250 and CAC must stay under $37."
        html = "<p>⚠ Failed validation — figures withheld.</p>"
        self.assertIs(gates.d09_publishable_gated(r, html).ok, True)

    def test_a_passing_validation_is_not_policed(self):
        """Nothing is withheld, so restating the TAM is exactly what should happen."""
        r = self._r(validation={"passed": True}, publishable=True)
        r["viability"]["summary"] = "A $1.22B TAM supports the wedge."
        self.assertIs(gates.d09_publishable_gated(r, "<p>fine</p>").ok, None)


class TestPreservedBehaviour(unittest.TestCase):
    """The checks D09 already made must survive the extension."""

    def test_publishable_not_false_still_fails(self):
        r = {"market_sizing": {"validation": {"passed": False}, "publishable": True,
                               "tam": {"mid": 1_000_000}}}
        f = gates.d09_publishable_gated(r, "<p>x</p>")
        self.assertIs(f.ok, False)
        self.assertIn("publishable", f.detail)

    def test_a_missing_banner_still_fails(self):
        r = {"market_sizing": {"validation": {"passed": False}, "publishable": False,
                               "tam": {"mid": 1_000_000}}}
        f = gates.d09_publishable_gated(r, "<p>no warning here</p>")
        self.assertIs(f.ok, False)
        self.assertIn("banner", f.detail)

    def test_no_html_does_not_crash(self):
        r = {"market_sizing": {"validation": {"passed": False}, "publishable": False,
                               "tam": {"mid": 1_000_000}}}
        self.assertIsNotNone(gates.d09_publishable_gated(r, None))

    def test_validation_absent_is_not_applicable(self):
        self.assertIsNone(gates.d09_publishable_gated({"market_sizing": {}}, "<p>x</p>").ok)


if __name__ == "__main__":
    unittest.main()
