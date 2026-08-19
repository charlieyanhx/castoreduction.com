"""The tree owns the next question; the LLM only extracts. Wiring test for intake.py.

Before this, the LLM chose each next question from a generic prompt — which is how every
venture got the same interview. Now `process_message` runs the tree after extraction and the
assistant's question comes from the pack, with the LLM's suggestion used only when the tree
is exhausted. All LLM calls are mocked: these tests assert the WIRING, not model behaviour.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import intake
from intake_tree import is_unknown


def _llm(extracted_updates, next_q="What else?", action="ask"):
    """A canned call_json response in the intake schema."""
    return {"extracted": extracted_updates, "next_action": action,
            "next_question": next_q, "final_description": None, "reasoning": "test"}


CAFE_TURN = {"product": "A specialty coffee shop serving pour-overs and pastries.",
             "target_customer": "Local commuters", "geography": "Portland, OR",
             "business_model": "people buy drinks at the counter"}


class TestTheTreePicksTheQuestion(unittest.TestCase):
    def _start(self, updates, llm_next="Generic LLM question?"):
        with patch.object(intake, "call_json", return_value=_llm(updates, llm_next)):
            s = intake.start_session()
            return s["session_id"], intake.process_message(s["session_id"], "here's my idea")

    def test_a_cafe_is_asked_a_cafe_question_not_the_llms(self):
        sid, r = self._start(CAFE_TURN)
        tree_fields = {q["field"] for q in
                       __import__("intake_tree").plan_questions(
                           intake.get_session(sid)["extracted"],
                           __import__("intake_tree").classify_turn(
                               intake.get_session(sid)["extracted"]))}
        self.assertIn(r.get("asked_field"), tree_fields,
                      f"the assistant asked {r.get('asked_field')!r} — not from the pack")
        self.assertNotEqual(r["assistant_message"], "Generic LLM question?")

    def test_the_question_carries_its_why(self):
        _, r = self._start(CAFE_TURN)
        self.assertTrue(r.get("asked_why"),
                        "the question ships without its 'drives' line — a chore, not a reason")

    def test_the_progress_chips_are_the_active_pack(self):
        _, r = self._start(CAFE_TURN)
        fields = {f["field"] for f in r.get("tree_fields") or []}
        self.assertIn("capacity", fields, "the UI cannot render pack progress")

    def test_not_sure_records_an_assumption_and_advances(self):
        sid, r1 = self._start(CAFE_TURN)
        asked = r1["asked_field"]
        with patch.object(intake, "call_json", return_value=_llm({})):
            r2 = intake.process_message(sid, "no idea, sorry")
        ex = intake.get_session(sid)["extracted"]
        self.assertTrue(is_unknown(ex.get(asked)),
                        f"'no idea' did not mark {asked!r} as an assumption: {ex.get(asked)!r}")
        self.assertNotEqual(r2.get("asked_field"), asked, "'not sure' re-asked the question")

    def test_ready_only_when_the_tree_is_exhausted(self):
        sid, r = self._start(CAFE_TURN)
        self.assertFalse(r["ready"],
                         "ready fired while the venture's own pack still has open questions")

    def test_answering_everything_reaches_ready(self):
        sid, r = self._start(CAFE_TURN)
        for _ in range(20):
            if r.get("ready"):
                break
            asked = r.get("asked_field")
            with patch.object(intake, "call_json",
                              return_value=_llm({asked: "22"} if asked else {})):
                r = intake.process_message(sid, "about 22")
        self.assertTrue(r.get("ready"), "answering every tree question never reached ready")

    def test_the_escape_hatch_still_exists(self):
        """A founder who answers vaguely forever must not be trapped: after enough turns
        the session lowers the bar, marks the rest assumed, and goes ready."""
        sid, r = self._start(CAFE_TURN)
        for _ in range(intake.MAX_TREE_TURNS + 2):
            if r.get("ready"):
                break
            with patch.object(intake, "call_json", return_value=_llm({})):
                r = intake.process_message(sid, "hmm let me think about that one")
        self.assertTrue(r.get("ready"), "a vague founder is trapped in the interview forever")


class TestTheBriefCarriesTheNewFacts(unittest.TestCase):
    def test_synthesis_includes_tree_facts_in_extractable_phrasings(self):
        ex = {"product": "A coffee shop.", "geography": "Portland, OR",
              "business_model": "counter sales", "target_customer": "commuters",
              "avg_ticket": "$6.50 per drink", "capacity": "22 seats",
              "named_competitors": "Stumptown, Heart Coffee",
              "monthly_cost_estimate": "$9,000 including rent",
              "status_quo": "they go to the chain on the corner"}
        brief = intake._synthesize_from_extracted(ex)
        self.assertIn("$6.50", brief)
        self.assertIn("22 seats", brief)
        self.assertIn("Stumptown", brief)
        self.assertIn("Named competitors:", brief, "discover seeds from this exact phrase")
        self.assertIn("$9,000", brief)

    def test_an_assumption_is_disclosed_not_silently_dropped(self):
        ex = {"product": "A coffee shop.", "geography": "Portland, OR",
              "business_model": "counter sales", "target_customer": "commuters",
              "rent_estimate": {"unknown": True}}
        brief = intake._synthesize_from_extracted(ex)
        self.assertIn("not know", brief.lower(),
                      "a 'not sure' vanished instead of becoming a disclosed assumption")


if __name__ == "__main__":
    unittest.main()
