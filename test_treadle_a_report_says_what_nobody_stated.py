"""Treadle phase 4: a report says, in a sentence, every value it used that nobody stated.

The FactStore already records each input's tier, and `rests_on` carries the weakest of
them up to every figure. A reader does not read tiers. So a report carries one plain
sentence for each input a section actually READ that no person stated:

  assumed    the input's default stood in, without asking or after 'not sure'
  found      an agent looked it up and nobody has confirmed it
  disputed   a lookup disagrees with what the person said, and nobody has ruled
  declined   'not sure' with nothing to stand in, so a section was left out

An input nobody read says nothing: a value the report did not use is not a claim in it.
"""
from __future__ import annotations

import unittest

from test_treadle_found_values_are_confirmed import KEY, SRC, TRIP, eur, guide
from treadle import run_report
from treadle.inputs import DECLINE
from treadle.registry import load_pack

EM_DASH = chr(0x2014)
#: TRIP with the currency said too, so only what a test adds can need disclosing.
SAID = {**TRIP, "currency": "EUR", "travellers": 2}


def told(report) -> dict[str, dict]:
    return {d["field"]: d for d in report.disclosures}


class EveryUnstatedReadIsDisclosed(unittest.TestCase):
    def setUp(self):
        load_pack(KEY)

    def test_everything_stated_discloses_nothing(self):
        self.assertEqual(run_report(KEY, survey=guide(SAID)).disclosures, ())

    def test_a_default_used_without_asking_is_disclosed(self):
        brief = {k: v for k, v in SAID.items() if k != "travellers"}
        d = told(run_report(KEY, survey=guide(brief)))
        self.assertEqual(set(d), {"travellers"})
        self.assertEqual(d["travellers"]["kind"], "assumed")
        self.assertIn("one traveller", d["travellers"]["text"])
        self.assertIn("without asking", d["travellers"]["text"])
        self.assertIn("What it costs", d["travellers"]["text"])

    def test_a_default_used_after_not_sure_says_so(self):
        d = told(run_report(KEY, survey=guide({**SAID, "travellers": DECLINE})))
        self.assertEqual(d["travellers"]["kind"], "assumed")
        self.assertIn("after 'not sure'", d["travellers"]["text"])

    def test_an_unconfirmed_finding_is_disclosed_with_where_it_came_from(self):
        survey = eur(guide({**SAID, "currency": DECLINE}))
        d = told(run_report(KEY, survey=survey))
        self.assertEqual(d["currency"]["kind"], "found")
        for part in ("'EUR'", SRC, "What it costs", "not confirmed"):
            self.assertIn(part, d["currency"]["text"])

    def test_a_confirmed_finding_is_the_persons_word_and_says_nothing(self):
        found = eur(guide({k: v for k, v in SAID.items() if k != "currency"}))
        confirmed = found.answer("currency", "EUR", reply_to=found.next_question()["id"])
        self.assertNotIn("currency", told(run_report(KEY, survey=confirmed)))

    def test_a_run_before_the_ruling_discloses_the_dispute(self):
        disputed = eur(guide({**SAID, "currency": "GBP"}))
        report = run_report(KEY, survey=disputed)
        self.assertEqual(report.result["budget"]["currency"], "GBP", "the statement is used")
        d = told(report)["currency"]
        self.assertEqual(d["kind"], "disputed")
        for part in ("'GBP'", "'EUR'", SRC, "nobody has settled"):
            self.assertIn(part, d["text"])

    def test_not_sure_with_nothing_to_stand_in_names_what_was_left_out(self):
        d = told(run_report(KEY, survey=guide({**SAID, "diet": DECLINE})))
        self.assertEqual(d["diet"]["kind"], "declined")
        self.assertIn("Where to eat", d["diet"]["text"])
        self.assertIn("not sure", d["diet"]["text"])

    def test_an_input_nobody_read_is_not_disclosed(self):
        # A business trip has no restaurant section, so 'not sure' about the diet left
        # nothing out and nothing read it.
        business = {**SAID, "interests": ["meetings"], "diet": DECLINE}
        self.assertNotIn("diet", told(run_report(KEY, survey=guide(business))))

    def test_a_disclosure_is_plain_json_without_em_dashes(self):
        report = run_report(KEY, survey=eur(guide({**SAID, "currency": DECLINE,
                                                    "travellers": DECLINE})))
        self.assertEqual(len(report.disclosures), 2)
        for d in report.disclosures:
            self.assertEqual(sorted(d), ["field", "kind", "text"])
            self.assertNotIn(EM_DASH, d["text"])

    def test_a_pack_without_inputs_and_a_runner_disclose_nothing(self):
        import plan
        load_pack("market_research")
        real, plan.run_plan = plan.run_plan, lambda description, **kw: {"x": 1}
        try:
            self.assertEqual(run_report("market_research", {"description": "d"}).disclosures, ())
        finally:
            plan.run_plan = real


if __name__ == "__main__":
    unittest.main()
