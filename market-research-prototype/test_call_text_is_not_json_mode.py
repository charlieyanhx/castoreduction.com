"""
Critical: call_text — the only prose path — is forced into JSON mode by both free backends.

`call_text` and `call_json` share the same backend functions, and two of the three hardcode
structured output:

    llm.py:156  _call_groq    response_format={"type": "json_object"}
    llm.py:234  _call_gemini  config={"response_mime_type": "application/json", ...}

There is no parameter to turn it off. So a caller that wants prose gets JSON: the model is
constrained to emit an object, and `call_text` returns that raw text to be printed.

`agents/synthesis.py:51` is the live caller — it builds `research_brief`, which the report
renders verbatim. The corpus carries research_brief on 0 of 16 reports, so no shipped report
displays the malformed output today; this protects the prose path rather than correcting a
published one. That the brief is absent everywhere is itself consistent with a stage that
does not survive its own output.

`_call_anthropic` never set a response format, so prose already worked there — which is
exactly why the defect could sit unnoticed: it is invisible on the paid backend and only
appears on the two free ones.
"""
from __future__ import annotations

import inspect
import unittest
from unittest.mock import MagicMock, patch

import llm


class TestTheBackendsAcceptAPlainTextMode(unittest.TestCase):
    """Every backend must be able to answer in prose, or call_text is a lie on that backend."""

    def test_every_backend_takes_a_json_mode_parameter(self):
        for name, fn in llm._BACKENDS.items():
            params = inspect.signature(fn).parameters
            self.assertIn("json_mode", params,
                          f"_call_{name} cannot be asked for prose")

    def test_groq_omits_the_json_response_format_for_prose(self):
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="A paragraph of prose."))],
            usage=MagicMock(prompt_tokens=5, completion_tokens=7))
        with patch("groq.Groq", return_value=client):
            llm._call_groq("s", "u", 100, "m", json_mode=False)
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertNotIn("response_format", kwargs,
                         "prose request still constrained to a JSON object")

    def test_groq_still_asks_for_json_when_it_wants_json(self):
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"a": 1}'))],
            usage=MagicMock(prompt_tokens=5, completion_tokens=7))
        with patch("groq.Groq", return_value=client):
            llm._call_groq("s", "u", 100, "m", json_mode=True)
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})

    def test_gemini_omits_the_json_mime_type_for_prose(self):
        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(
            text="A paragraph of prose.", usage_metadata=MagicMock(
                prompt_token_count=5, candidates_token_count=7))
        with patch.object(llm, "_gemini_client", return_value=client):
            llm._call_gemini("s", "u", 100, "m", json_mode=False)
        cfg = client.models.generate_content.call_args.kwargs["config"]
        self.assertNotEqual(cfg.get("response_mime_type"), "application/json",
                            "prose request still constrained to application/json")

    def test_gemini_still_asks_for_json_when_it_wants_json(self):
        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(
            text='{"a": 1}', usage_metadata=MagicMock(
                prompt_token_count=5, candidates_token_count=7))
        with patch.object(llm, "_gemini_client", return_value=client):
            llm._call_gemini("s", "u", 100, "m", json_mode=True)
        cfg = client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(cfg.get("response_mime_type"), "application/json")


class TestCallTextAsksForProse(unittest.TestCase):
    def test_call_text_requests_prose_from_its_backend(self):
        seen = {}

        def _spy(system, user, max_tokens, model, json_mode=True):
            seen["json_mode"] = json_mode
            return "prose", 1, 1

        with patch.dict(llm._BACKENDS, {"groq": _spy}), \
             patch.object(llm, "_backend_and_model", return_value=("groq", "m")):
            out = llm.call_text("s", "u")
        self.assertEqual(out, "prose")
        self.assertIs(seen["json_mode"], False,
                      "call_text asked its backend for a JSON object")

    def test_the_json_chain_still_requests_json(self):
        """call_json reaches its backend through _try_one_backend, not _BACKENDS directly,
        so that is the path to assert on."""
        seen = {}

        def _spy(system, user, max_tokens, model, json_mode=True):
            seen["json_mode"] = json_mode
            return '{"ok": true}', 1, 1

        with patch.dict(llm._BACKENDS, {"groq": _spy}), \
             patch.dict("os.environ", {"GROQ_API_KEY": "gsk_test_key_not_a_secret"}):
            llm._try_one_backend("groq", "s", "u", 100)
        self.assertIs(seen["json_mode"], True,
                      "the JSON chain stopped requesting structured output")


class TestTheDefaultStaysSafe(unittest.TestCase):
    def test_json_mode_defaults_to_true(self):
        """Every existing caller goes through call_json. A default of False would silently
        un-structure all of them."""
        for name, fn in llm._BACKENDS.items():
            p = inspect.signature(fn).parameters.get("json_mode")
            if p is not None:
                self.assertIs(p.default, True, f"_call_{name} defaults to prose")


if __name__ == "__main__":
    unittest.main()
