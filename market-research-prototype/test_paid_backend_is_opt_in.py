"""A free run must not be able to spend money by falling through to the paid backend.

_chain_text builds its fallback chain as `[primary] + every other configured backend`. With
GROQ, GEMINI and ANTHROPIC keys all present, that chain is groq -> gemini -> anthropic. So
the moment both free tiers throttle — Groq is 30 RPM, Gemini 15 RPM, and a full run makes
LLM calls in bursts (four 4Ps sections in parallel, multi-perspective consumer research) —
the run silently continues on the PAID key and bills for it.

Nothing in the log distinguishes that from a normal run except one INFO line, and the
operator asked for a free run. Spending someone's money as a side effect of throttling is
not a fallback, it is a surprise.

THE SECOND HALF of the same finding: chain exhaustion returns
{"_parse_error": "all backends exhausted (rate-limited or unavailable)"}, which callers
treat as a failed step. That is the reported production symptom — a run "breaking at market
scale classification and customer voice", i.e. at exactly the LLM-dependent steps, while
the deterministic ones came through fine. Before today's ANTHROPIC key was valid, the chain
would exhaust at the third backend too, so the fallback was not saving those runs either.

So: paid backends are opt-in, and an exhausted run says so in its own artifact instead of
looking like a venture with thin data.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestTheChainExcludesPaidByDefault(unittest.TestCase):
    def _chain(self, env):
        import llm

        with patch.dict("os.environ", env, clear=True):
            return llm.fallback_chain()

    def test_a_free_primary_does_not_fall_through_to_anthropic(self):
        chain = self._chain({"GROQ_API_KEY": "g", "GEMINI_API_KEY": "m",
                             "ANTHROPIC_API_KEY": "a"})
        self.assertIn("groq", chain)
        self.assertIn("gemini", chain)
        self.assertNotIn("anthropic", chain,
                         "a free run can still bill the paid key on a throttle")

    def test_opting_in_restores_the_paid_backend(self):
        chain = self._chain({"GROQ_API_KEY": "g", "GEMINI_API_KEY": "m",
                             "ANTHROPIC_API_KEY": "a", "LLM_ALLOW_PAID": "1"})
        self.assertIn("anthropic", chain)

    def test_choosing_anthropic_explicitly_is_itself_consent(self):
        """LLM_BACKEND=anthropic is an operator saying "use the paid one" — refusing it
        would be obeying the letter of the guard and defeating its purpose."""
        chain = self._chain({"ANTHROPIC_API_KEY": "a", "GROQ_API_KEY": "g",
                             "LLM_BACKEND": "anthropic"})
        self.assertEqual(chain[0], "anthropic")

    def test_the_paid_backend_is_still_usable_when_it_is_the_only_key(self):
        """Excluding it would turn a working single-provider setup into no LLM at all."""
        chain = self._chain({"ANTHROPIC_API_KEY": "a"})
        self.assertEqual(chain, ["anthropic"])

    def test_the_primary_is_always_first(self):
        chain = self._chain({"GEMINI_API_KEY": "m", "GROQ_API_KEY": "g",
                             "LLM_BACKEND": "gemini"})
        self.assertEqual(chain[0], "gemini")

    def test_backends_with_no_key_are_not_in_the_chain(self):
        """Trying a backend with no key burns an attempt and a backoff for nothing."""
        chain = self._chain({"GROQ_API_KEY": "g"})
        self.assertEqual(chain, ["groq"])


class TestAnExhaustedRunSaysSo(unittest.TestCase):
    def test_exhaustion_is_counted(self):
        import llm

        llm.reset_exhaustion()
        llm.note_exhaustion("rate-limited or unavailable")
        llm.note_exhaustion("rate-limited or unavailable")
        s = llm.exhaustion_summary()
        self.assertEqual(s["count"], 2)
        self.assertIn("rate-limited", s["reason"])

    def test_a_clean_run_reports_nothing(self):
        import llm

        llm.reset_exhaustion()
        self.assertEqual(llm.exhaustion_summary(), {})

    def test_the_pipeline_records_it_on_the_result(self):
        """A degraded run must be distinguishable from a venture with thin data — that is
        the difference between "we could not look" and "we looked and found nothing", the
        distinction this pipeline keeps having to relearn."""
        import inspect

        import plan
        src = inspect.getsource(plan.run_plan)
        self.assertIn("_llm_exhaustion", src,
                      "a throttled run still looks like a thin-data venture")


if __name__ == "__main__":
    unittest.main()
