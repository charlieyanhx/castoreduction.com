"""
F2 — LLM calls must be deterministic (temperature=0, seed where supported) so the same
input yields the same number. Failure-first: these assert each backend's request carries
temperature=0; they MUST fail on pre-fix llm.py (which sets no temperature) and pass after.

SDK clients are mocked so no network/keys are needed. A backend whose SDK isn't installed
is skipped (Gemini is the one actually used in this env).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

import llm


class TestDeterministicPayloads(unittest.TestCase):
    def test_gemini_sets_temperature_zero(self):
        fake = MagicMock()
        resp = MagicMock(text="{}",
                         usage_metadata=MagicMock(prompt_token_count=1, candidates_token_count=1))
        fake.models.generate_content.return_value = resp
        with patch("google.genai.Client", return_value=fake), patch("llm.time.sleep"):
            llm._call_gemini("sys", "usr", 100, "gemini-2.5-flash")
        cfg = fake.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(cfg.get("temperature"), 0, "Gemini config must set temperature=0")

    def test_groq_sets_temperature_zero(self):
        try:
            import groq  # noqa: F401
        except ImportError:
            self.skipTest("groq SDK not installed")
        fake = MagicMock()
        fake.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="{}"))],
            usage=MagicMock(prompt_tokens=1, completion_tokens=1))
        with patch("groq.Groq", return_value=fake):
            llm._call_groq("sys", "usr", 100, "x")
        kw = fake.chat.completions.create.call_args.kwargs
        self.assertEqual(kw.get("temperature"), 0, "Groq call must set temperature=0")

    def test_anthropic_sets_temperature_zero(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            self.skipTest("anthropic SDK not installed")
        fake = MagicMock()
        fake.messages.create.return_value = MagicMock(
            content=[MagicMock(text="{}")],
            usage=MagicMock(input_tokens=1, output_tokens=1))
        with patch("anthropic.Anthropic", return_value=fake):
            llm._call_anthropic("sys", "usr", 100, "x")
        kw = fake.messages.create.call_args.kwargs
        self.assertEqual(kw.get("temperature"), 0, "Anthropic call must set temperature=0")


if __name__ == "__main__":
    unittest.main()
