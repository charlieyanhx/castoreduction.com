"""R7 (88b416f6 audit): the customer universe holds companies, honestly labeled.

MEASURED: '5 real companies matching the ICP' were a nav word ('Compliance'), a
conference ('AI Enterprise Conference 2026'), a job posting ('Staff AI Engineer'),
a revenue-tracker site, and a Russian-language article headline ('Испытываем подход
от CEO Y Combinator'). All five passed _is_plausible_company_name; no code anywhere
performs an ICP match, yet the viability prompt asserts 'identified as ICP-matching';
and the #1 recommended segment was the buyer the report's own simulated interview
disqualified (regulated enterprise: 'would not buy' without BAA/SOC 2).
"""
from __future__ import annotations

import unittest


class TestEntityShape(unittest.TestCase):
    def test_the_five_measured_junk_names_are_rejected(self):
        from customer_universe import _is_plausible_company_name as ok
        for junk in ("Compliance", "AI Enterprise Conference 2026",
                     "Staff AI Engineer",
                     "Испытываем подход от CEO Y Combinator"):
            self.assertFalse(ok(junk), junk)

    def test_real_company_names_still_pass(self):
        from customer_universe import _is_plausible_company_name as ok
        for real in ("Glean", "Stripe", "Sonoratown", "Ragie", "TrustMRR",
                     "Palo Alto Networks", "Unstructured"):
            self.assertTrue(ok(real), real)


class TestTheViabilityLabelSaysWhatThePipelineCanBack(unittest.TestCase):
    def test_icp_matching_is_no_longer_asserted(self):
        import four_ps
        src = open(four_ps.__file__.rstrip("c")).read()
        self.assertNotIn("identified as ICP-matching", src)
        self.assertIn("ICP match not verified", src)


class TestTopPickHearsItsOwnObjections(unittest.TestCase):
    def test_a_would_not_buy_interview_attaches_a_disqualifier_note(self):
        from segment_scoring import objection_check
        ranking = {"top_pick": {
            "name": "Regulated enterprise organizations requiring secure custom "
                    "knowledge retrieval",
            "description": "compliance-heavy entities handling sensitive data"}}
        interviews = [
            {"segment_name": "Enterprise Innovation / Knowledge Management Lead",
             "would_buy": False,
             "quote": "In healthcare, I cannot put compliance documents into a "
                      "self-serve cloud tool without a signed BAA and SOC 2."},
            {"segment_name": "Solo Technical Founder", "would_buy": True,
             "would_pay": 40},
        ]
        note = objection_check(ranking, interviews)
        self.assertIsNotNone(note)
        self.assertIn("would not buy", note.lower())
        self.assertIn("BAA", note)

    def test_no_overlap_means_no_note(self):
        from segment_scoring import objection_check
        ranking = {"top_pick": {"name": "Indie hackers", "description": "solo devs"}}
        interviews = [{"segment_name": "Enterprise Compliance Lead",
                       "would_buy": False, "quote": "no BAA no deal"}]
        self.assertIsNone(objection_check(ranking, interviews))


if __name__ == "__main__":
    unittest.main()
