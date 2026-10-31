"""
Tests for the chat intake (intake.py), focused on the robustness bug a live
workspace test surfaced: a transient LLM hiccup must NOT dead-end the chat into
re-asking already-given info. The LLM is mocked.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import intake


def _good(**over):
    base = {"extracted": {"product": "meal app", "target_customer": "US families",
                          "business_model": "freemium", "geography": "US",
                          "pricing": "$8/mo", "differentiation": None,
                          "stage": None, "key_features": None},
            "next_action": "ask", "next_question": "What makes it different?",
            "final_description": None}
    base.update(over)
    return base


class TestIntakeRetry(unittest.TestCase):
    def test_retries_past_a_transient_parse_error(self):
        seq = [{"_parse_error": "boom"}, _good()]   # first flaky, second good
        it = iter(seq)
        with patch("intake.call_json", side_effect=lambda **k: next(it)):
            s = intake.start_session()
            r = intake.process_message(s["session_id"], "A meal app, freemium $8/mo, US families")
        self.assertEqual(r["extracted"]["product"], "meal app")   # extracted, not salvaged
        self.assertEqual(r["extracted"]["pricing"], "$8/mo")

    def test_salvages_only_after_all_retries_fail(self):
        with patch("intake.call_json", return_value={"_parse_error": "always"}):
            s = intake.start_session()
            r = intake.process_message(s["session_id"], "A meal app for families")
        # All retries failed → salvage path → extraction unchanged (all None), not crash.
        self.assertFalse(r["ready"])
        self.assertTrue(all(v is None for v in r["extracted"].values()))

    def test_merge_only_overwrites_with_non_null(self):
        # Field already known must not be wiped by a later null.
        with patch("intake.call_json", side_effect=[
                _good(), _good(extracted={**_good()["extracted"], "product": None})]):
            s = intake.start_session()
            intake.process_message(s["session_id"], "first")
            r = intake.process_message(s["session_id"], "second")
        self.assertEqual(r["extracted"]["product"], "meal app")   # preserved


if __name__ == "__main__":
    unittest.main()
