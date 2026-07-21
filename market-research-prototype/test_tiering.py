"""
W5-3: model/tiering.py — spend the expensive model where judgement lives.

Every LLM call in the pipeline currently resolves to ONE model per backend. That
means a throwaway utility call ("normalise these 12 category strings") pays the same
per-token rate as the 4Ps synthesis a buyer reads. The tiers here are a routing
policy, not a quality claim: UTILITY work is mechanical and goes to the cheapest
model the backend offers; REASONING work — sizing, synthesis, viability — stays on
the default; CRITICAL work can be pinned upward.

The rule that matters: an unknown or missing tier must resolve to REASONING. A typo
in a tier name must never silently downgrade the model writing the report.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from model.tiering import (CRITICAL, REASONING, UTILITY, model_for, resolve_tier,
                           tier_of_step)


class TestTierResolution(unittest.TestCase):
    def test_known_tiers_pass_through(self):
        self.assertEqual(resolve_tier("utility"), UTILITY)
        self.assertEqual(resolve_tier("reasoning"), REASONING)
        self.assertEqual(resolve_tier("critical"), CRITICAL)

    def test_case_and_whitespace_are_tolerated(self):
        self.assertEqual(resolve_tier("  Utility "), UTILITY)

    def test_none_defaults_to_reasoning(self):
        self.assertEqual(resolve_tier(None), REASONING)

    def test_unknown_tier_defaults_to_reasoning_not_to_the_cheap_model(self):
        """A typo'd tier must never silently downgrade the model writing the report."""
        self.assertEqual(resolve_tier("utilty"), REASONING)
        self.assertEqual(resolve_tier(""), REASONING)


class TestModelSelection(unittest.TestCase):
    def test_utility_picks_the_cheap_model_when_the_backend_has_one(self):
        self.assertEqual(model_for(UTILITY, "gemini", "gemini-flash-latest"),
                         "gemini-flash-lite-latest")

    def test_reasoning_keeps_the_backend_default(self):
        self.assertEqual(model_for(REASONING, "gemini", "gemini-flash-latest"),
                         "gemini-flash-latest")

    def test_critical_upgrades_where_an_upgrade_exists(self):
        self.assertEqual(model_for(CRITICAL, "anthropic", "claude-haiku-4-5"),
                         "claude-sonnet-4-5")

    def test_backend_without_a_cheap_tier_falls_back_to_the_default(self):
        """No downgrade available is not an error — it is the default model."""
        self.assertEqual(model_for(UTILITY, "nonesuch", "some-model"), "some-model")

    def test_an_explicit_model_override_beats_every_tier(self):
        """LLM_MODEL is an operator pinning a model on purpose; tiering must not fight it."""
        with patch.dict("os.environ", {"LLM_MODEL": "pinned-model"}):
            self.assertEqual(model_for(UTILITY, "gemini", "gemini-flash-latest"),
                             "pinned-model")
            self.assertEqual(model_for(CRITICAL, "gemini", "gemini-flash-latest"),
                             "pinned-model")

    def test_tiering_can_be_switched_off_whole(self):
        with patch.dict("os.environ", {"LLM_TIERING": "0"}):
            self.assertEqual(model_for(UTILITY, "gemini", "gemini-flash-latest"),
                             "gemini-flash-latest")


class TestStepPolicy(unittest.TestCase):
    """Which of the pipeline's own steps are mechanical enough to downgrade."""

    def test_synthesis_and_sizing_are_reasoning(self):
        for step in ("four_ps", "market_sizing", "viability", "synthesis"):
            self.assertEqual(tier_of_step(step), REASONING, step)

    def test_throwaway_steps_are_utility(self):
        for step in ("query_plan", "category_normalise", "keyword_expand"):
            self.assertEqual(tier_of_step(step), UTILITY, step)

    def test_competitor_extraction_is_not_downgraded(self):
        """It LOOKS as mechanical as query planning, but it decides who counts as a
        competitor — and that lands in the report. Policy, pinned so a later edit
        that 'tidies' it into the utility list has to argue with a test."""
        self.assertEqual(tier_of_step("competitor_extract"), REASONING)
        self.assertEqual(tier_of_step("company_profile"), REASONING)

    def test_an_unmapped_step_is_reasoning(self):
        self.assertEqual(tier_of_step("some_new_step"), REASONING)


class TestLlmIntegration(unittest.TestCase):
    """call_json/call_text must accept a tier and route on it."""

    def test_call_json_accepts_a_tier(self):
        import inspect
        import llm
        self.assertIn("tier", inspect.signature(llm.call_json).parameters)
        self.assertIn("tier", inspect.signature(llm.call_text).parameters)

    def test_tier_reaches_the_backend_as_a_different_model(self):
        import llm
        seen = {}

        def fake(system, user, max_tokens, model):
            seen["model"] = model
            return "hi", 1, 1

        with patch.dict(llm._BACKENDS, {"gemini": fake}), \
             patch("llm._detect_backend", return_value="gemini"), \
             patch.dict("os.environ", {"GEMINI_API_KEY": "x" * 20}, clear=False):
            llm.call_text("s", "u", tier="utility")
        self.assertEqual(seen["model"], "gemini-flash-lite-latest")

    def test_default_call_is_unchanged(self):
        import llm
        seen = {}

        def fake(system, user, max_tokens, model):
            seen["model"] = model
            return "hi", 1, 1

        with patch.dict(llm._BACKENDS, {"gemini": fake}), \
             patch("llm._detect_backend", return_value="gemini"), \
             patch.dict("os.environ", {"GEMINI_API_KEY": "x" * 20}, clear=False):
            llm.call_text("s", "u")
        self.assertEqual(seen["model"], "gemini-flash-latest")

    def test_tier_is_part_of_the_cache_key(self):
        """Same prompt at two tiers is two different answers — one cache slot would
        serve the cheap model's output as the expensive model's."""
        import llm
        a = llm._cache_key("s", "u", None, tier="utility")
        b = llm._cache_key("s", "u", None, tier="reasoning")
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
