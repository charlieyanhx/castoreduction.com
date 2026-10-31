"""
A prompt-side fix failed silently, and proving it took byte-identity forensics.

MEASURED on run13: the paired-competitor-count rule (78fab50) never changed the report —
"30 competitors" still appeared 12 times — because it never entered the prompts:
product/price/promotion sections came back BYTE-IDENTICAL to run12's (LLM cache hits, so
identical cache keys, so identical prompt text). Yet section_reminders executed with run13's
FINAL stored inputs produces the rule correctly. Conclusion: the inputs AT CALL TIME differed
from the final state, and nothing recorded what the assembled reminder block contained.

Three failures in one incident:
 1. STALE BINDING: plan.py passed competitor_density from a local `disc` bound at the
    discover step, while the geo-roster promotion that sets it runs later — whether the local
    sees the promotion depends on mutation-vs-replacement semantics three functions away.
    The call site now re-reads result["discover"] at call time.
 2. NO EVIDENCE: the artifact never said what the prompts carried, so a silent prompt
    regression is indistinguishable from a model ignoring an instruction.
    four_ps["_reminders_fired"] + four_ps["_reminder_facts"] now record it.
 3. BLIND TRACE: the 4Ps pool threads dropped the ledger's step ContextVar, so their LLM
    events traced as step=None and "did the 4Ps hit the cache" took a Counter over Nones.
    The pool now copies the context per task.

These tests EXECUTE assemble_4ps_split with call_json captured, using run13's real stored
inputs — the wiring, not the unit.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch


def _run13():
    if not os.path.exists("out/live/run13.json"):
        return None
    return (json.load(open("out/live/run13.json")) or {}).get("result") or {}


def _assemble(r, captured):
    import four_ps as F

    def fake_call(system, user, max_tokens, response_model=None):
        for name in ("Product", "Price", "Place", "Promotion"):
            if name in system:
                captured[name.lower()] = user
                break
        return {"narrative": "n.", "key_takeaways": ["t"], "citations": []}

    disc = r.get("discover") or {}
    with patch.object(F, "call_json", side_effect=fake_call):
        return F.assemble_4ps_split(
            profile=r.get("profile") or {},
            competitors=((disc.get("synthesis") or {}).get("ranked_opportunities") or [])[:5],
            top_audience={}, max_diff=r.get("max_diff") or {},
            van_westendorp=(r.get("pricing") or {}).get("psm") or {},
            place=r.get("place") or {},
            pricing_benchmark=(r.get("pricing") or {}).get("benchmark"),
            economics=r.get("economics") or {},
            reddit_signal=r.get("reddit_signal") or {},
            business_model_kind=r.get("business_model_kind"),
            competitor_density=disc.get("competitor_density"),
            active_signal_density=disc.get("active_signal_density"),
            market_sizing=r.get("market_sizing") or {},
        )


class TestTheRuleReachesEveryPromptWithRealInputs(unittest.TestCase):
    def test_all_four_prompts_carry_the_paired_counts(self):
        r = _run13()
        if r is None:
            self.skipTest("run13 not present")
        captured: dict = {}
        _assemble(r, captured)
        self.assertEqual(len(captured), 4, f"only {sorted(captured)} sections called the LLM")
        for name, prompt in captured.items():
            self.assertIn("COMPETITOR COUNTS — HARD RULE", prompt,
                          f"{name}: the paired-count rule is missing from the prompt again")
            self.assertIn("102", prompt)

    def test_the_ladder_is_in_every_prompt_too(self):
        r = _run13()
        if r is None:
            self.skipTest("run13 not present")
        captured: dict = {}
        _assemble(r, captured)
        for name, prompt in captured.items():
            self.assertIn("CANONICAL DAILY-VOLUME LADDER", prompt, name)


class TestTheArtifactRecordsWhatFired(unittest.TestCase):
    def test_reminders_fired_is_in_the_output(self):
        r = _run13()
        if r is None:
            self.skipTest("run13 not present")
        out = _assemble(r, {})
        fired = out.get("_reminders_fired")
        self.assertIsInstance(fired, dict, "the artifact still cannot say what the prompts "
                                           "carried — silent prompt regressions stay silent")
        self.assertTrue(fired.get("competitor_counts_pair"))
        self.assertTrue(fired.get("volume_ladder"))

    def test_the_resolved_facts_are_recorded(self):
        r = _run13()
        if r is None:
            self.skipTest("run13 not present")
        out = _assemble(r, {})
        facts = out.get("_reminder_facts") or {}
        self.assertEqual(facts.get("competitor_density"), 30)
        self.assertEqual(facts.get("ms_competitors"), 102)

    def test_a_run_without_the_rule_says_so_instead_of_hiding_it(self):
        r = _run13()
        if r is None:
            self.skipTest("run13 not present")
        r = dict(r, market_sizing=dict(r.get("market_sizing") or {}, competitors=30))
        out = _assemble(r, {})
        self.assertFalse((out.get("_reminders_fired") or {}).get("competitor_counts_pair"),
                         "equal counts must record the rule as NOT fired — a false 'fired' "
                         "flag is worse than none")


if __name__ == "__main__":
    unittest.main()
