"""Two matchers fired on a fragment of a word, and both changed what the report said.

Found by an adversarial review of the report shipped for job d62bc04f, then confirmed
directly against the code.

  1. THE REAL COMPETITOR WAS CLASSIFIED AS CHEWING GUM.

        _is_megabrand("Reflect Orbital")  ->  True

     `MEGABRAND_NAMES` contains "orbit" — Wrigley's Orbit gum. The list is visibly built for a
     CPG/gum venture: 5 gum, altoids, dentyne, mentos, tic tac, trident, wrigley, eclipse,
     extra, stride. `_is_megabrand` then does a BARE SUBSTRING pass guarded only by
     `len(mega) > 4`, and "orbit" is five characters, so it matches inside "orbital".

     Reflect Orbital is the one real company in this market. Being a "megabrand" multiplied its
     opportunity score by 0.4, which dropped it below the 5,000-char synthesis prompt
     truncation — so the model never saw it, invented three brands in its place, and the report
     told the founder to "position directly against the 3 competitors by delivering physical
     sunlight redirection rather than adjacent software or logistics layers". That is aimed
     precisely at the one real hardware competitor, and it is falsifiable in one web search.

  2. THE "MONETIZATION INFERRED" DISCLOSURE WAS SWITCHED OFF BY TWO LETTERS.

        business_model._norm("/mo")            ->  'mo'
        'mo' in _norm("business model")        ->  True

     `_norm` maps "/" to a space, so the pricing keyword "/mo" degrades to the bigram "mo",
     which substring-matches inside the word "model". Any brief containing the phrase "business
     model" therefore looks like it stated an explicit per-month price, sets `explicit: True`,
     and suppresses the disclosure that the monetization was INFERRED rather than read.

THE SHARED FIX is word boundaries. "ice breakers" must still match "Ice Breakers Candy" and
"/mo" must still match "$29/mo", but neither may match inside a longer word.

KNOWN RESIDUAL, recorded rather than hidden: MEGABRAND_NAMES still contains generic English
words ("extra", "eclipse", "stride", "crest") because it was assembled for a chewing-gum
venture, so a real company called "Extra Space Storage" would still be demoted. Word boundaries
fix the measured defect and do not fix that; properly scoping a CPG denylist to CPG ventures is
a separate change and is not smuggled in here.
"""
from __future__ import annotations

import unittest


class TestAMegabrandIsNotAFragment(unittest.TestCase):
    def test_the_measured_case_the_real_competitor_is_not_gum(self):
        from discover import _is_megabrand
        self.assertFalse(_is_megabrand("Reflect Orbital"),
                         "the one real competitor in this market is still classified as "
                         "Wrigley's Orbit gum and demoted out of the report")

    def test_orbital_domain_forms_are_also_safe(self):
        from discover import _is_megabrand
        for name in ("reflectorbital", "Orbital Sciences", "Orbital Insight",
                     "Rocket Lab Orbital Systems"):
            with self.subTest(name=name):
                self.assertFalse(_is_megabrand(name))

    def test_the_actual_megabrand_still_matches(self):
        """The guard must keep doing its job — exact and as a whole word."""
        from discover import _is_megabrand
        for name in ("Orbit", "orbit", "Orbit Gum", "Wrigley's Orbit"):
            with self.subTest(name=name):
                self.assertTrue(_is_megabrand(name))

    def test_the_documented_multiword_example_still_matches(self):
        """The substring pass exists for this case, per its own comment."""
        from discover import _is_megabrand
        self.assertTrue(_is_megabrand("Ice Breakers Candy"))

    def test_other_known_megabrands_are_unaffected(self):
        from discover import _is_megabrand
        for name in ("Amazon Web Services", "Google Cloud", "Colgate-Palmolive",
                     "Nestle Waters", "Samsung Electronics"):
            with self.subTest(name=name):
                self.assertTrue(_is_megabrand(name))

    def test_empty_and_none_do_not_raise(self):
        from discover import _is_megabrand
        self.assertFalse(_is_megabrand(""))
        self.assertFalse(_is_megabrand(None))


class TestAPricingKeywordIsNotABigram(unittest.TestCase):
    """`_has` is a closure over the normalised blob, so drive it through the public entry —
    `classify_with_confidence`, which is where `explicit` decides whether the reader is told
    the monetization was inferred."""

    # The literal profile from job d62bc04f. The brief says the monetization is UNDETERMINED;
    # the classifier called it subscription and told the reader nothing.
    MEASURED = {
        "name": "Unknown",
        "business_model": "B2B infrastructure/utility service",
        "category": "orbital solar reflection infrastructure",
        "summary": "The company is developing a satellite-based orbital mirror system "
                   "designed to reflect sunlight down to specific surface targets on Earth. "
                   "Currently in the conceptual stage. Business model: Undetermined / early "
                   "exploratory (likely B2B infrastructure/utility).",
    }

    def _classify(self, business_model: str, category: str = "orbital infrastructure"):
        from business_model import classify_with_confidence
        return classify_with_confidence(
            {"business_model": business_model, "category": category,
             "summary": "A satellite venture.", "name": "Test"})

    def test_business_model_is_not_a_stated_monthly_price(self):
        from business_model import classify_with_confidence
        out = classify_with_confidence(self.MEASURED)
        self.assertFalse(
            out["explicit"],
            "'/mo' still matches inside the word 'model', so a brief that only says "
            "'business model' reads as an explicitly stated per-month price and the "
            "'monetization INFERRED' disclosure is suppressed")
        self.assertTrue(out.get("disclosure"),
                        "no disclosure was produced for an inferred monetization")

    def test_a_real_per_month_price_still_reads_as_explicit(self):
        for stated in ("$29/mo per seat", "subscription billed at 1450/mo",
                       "we charge $29 per month"):
            with self.subTest(stated=stated):
                self.assertTrue(self._classify(stated)["explicit"], stated)

    def test_the_normaliser_still_collapses_punctuation(self):
        """The word-boundary fix must not undo what _norm exists for."""
        from business_model import _norm
        self.assertEqual(_norm("ad-supported"), _norm("ad supported"))
        self.assertEqual(_norm("peer-to-peer"), "peer to peer")


if __name__ == "__main__":
    unittest.main()
