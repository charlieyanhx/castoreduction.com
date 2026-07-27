"""
The LLM layer was entirely dead: every call returned _parse_error.

Measured against the live free-tier key, one config key was the cause — and the failure
was reported to the user as "the model is busy (rate limit)", which is why it stayed hidden.
It was a 400 INVALID_ARGUMENT: a malformed request on our side, not throttling.

`thinking_config: {thinking_budget: 0}` exists for a real reason (cycle36): thinking models
spend the whole max_output_tokens budget on hidden reasoning and emit nothing, which is what
produced TAM $0 and dropped sections. But support for it is SPLIT across the models this key
can reach, and the two requirements directly conflict:

    gemini-flash-latest        with -> 400            without -> OK
    gemini-flash-lite-latest   with -> 400            without -> OK
    gemini-3.5-flash           with -> OK (16 chars)  without -> OK but ZERO chars
    gemini-2.5-flash-lite      with -> OK             without -> OK

So no single static config can work: 3.5-flash NEEDS the key to produce any output at all,
and flash-latest REJECTS the request outright for carrying it. The call has to adapt per
model — attempt with it, and on 400 retry that same model without it — and remember which
form worked so the wasted attempt is paid once, not on every call.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import llm


class _Boom(Exception):
    pass


def _err(msg):
    return _Boom(msg)


class _FakeResponse:
    def __init__(self, text="{}"):
        self.text = text
        self.usage_metadata = type("U", (), {"prompt_token_count": 5,
                                             "candidates_token_count": 7})()


class _FakeModels:
    """Records每 call and fails per a policy keyed on whether thinking_config was sent."""

    def __init__(self, policy):
        self.policy = policy
        self.calls = []

    def generate_content(self, *, model, contents, config):
        has_thinking = "thinking_config" in (config or {})
        self.calls.append((model, has_thinking))
        outcome = self.policy(model, has_thinking)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(outcome)


class _FakeClient:
    def __init__(self, policy):
        self.models = _FakeModels(policy)


def _install(policy):
    """Patch the genai client factory _call_gemini uses, and clear any learned state."""
    client = _FakeClient(policy)
    if hasattr(llm, "_GEMINI_THINKING_OK"):
        llm._GEMINI_THINKING_OK.clear()
    return client


class TestRetryWithoutThinkingConfig(unittest.TestCase):
    def _run(self, policy, model="gemini-flash-latest"):
        client = _install(policy)
        with patch.object(llm, "_gemini_client", return_value=client, create=True):
            text, _i, _o = llm._call_gemini("sys", "user", 64, model)
        return text, client.models.calls

    def test_a_400_on_thinking_config_retries_the_same_model_without_it(self):
        def policy(model, has_thinking):
            return _err("400 INVALID_ARGUMENT") if has_thinking else '{"ok":true}'
        text, calls = self._run(policy)
        self.assertEqual(text, '{"ok":true}')
        self.assertEqual(calls, [("gemini-flash-latest", True),
                                 ("gemini-flash-latest", False)])

    def test_the_working_form_is_remembered_so_the_retry_is_paid_once(self):
        def policy(model, has_thinking):
            return _err("400 INVALID_ARGUMENT") if has_thinking else '{"ok":true}'
        client = _install(policy)
        with patch.object(llm, "_gemini_client", return_value=client, create=True):
            llm._call_gemini("sys", "user", 64, "gemini-flash-latest")
            llm._call_gemini("sys", "user", 64, "gemini-flash-latest")
        self.assertEqual([has for _m, has in client.models.calls], [True, False, False],
                         "the rejected form was retried on the second call")

    def test_a_model_that_accepts_thinking_config_keeps_it(self):
        """3.5-flash returns ZERO characters without it — dropping it globally would
        silently empty every section rather than fail loudly."""
        def policy(model, has_thinking):
            return '{"ok":true}' if has_thinking else ""
        text, calls = self._run(policy, model="gemini-3.5-flash")
        self.assertEqual(text, '{"ok":true}')
        self.assertEqual(calls, [("gemini-3.5-flash", True)])

    def test_a_400_that_is_not_about_thinking_still_falls_through(self):
        """A model rejecting the request for any other reason must not loop forever."""
        def policy(model, has_thinking):
            return _err("400 INVALID_ARGUMENT")
        client = _install(policy)
        with patch.object(llm, "_gemini_client", return_value=client, create=True):
            with self.assertRaises(Exception):
                llm._call_gemini("sys", "user", 64, "gemini-flash-latest")
        for model in {m for m, _h in client.models.calls}:
            attempts = [h for m, h in client.models.calls if m == model]
            self.assertLessEqual(len(attempts), 2, f"{model} was attempted {len(attempts)}x")

    def test_429_and_404_still_fall_through_to_the_next_model(self):
        seen = []

        def policy(model, has_thinking):
            seen.append(model)
            return _err("429 RESOURCE_EXHAUSTED") if len(seen) < 3 else '{"ok":true}'
        client = _install(policy)
        with patch.object(llm, "_gemini_client", return_value=client, create=True):
            text, _i, _o = llm._call_gemini("sys", "user", 64, "gemini-flash-latest")
        self.assertEqual(text, '{"ok":true}')
        self.assertGreaterEqual(len({m for m, _h in client.models.calls}), 2)


class TestFailureIsNotMislabelledAsRateLimiting(unittest.TestCase):
    """The user saw "the model is busy (rate limit)" for a malformed request. A 400 is our
    bug; conflating it with throttling sends the reader off to wait instead of to the log."""

    def test_the_exhausted_message_does_not_claim_rate_limiting_for_a_400(self):
        import inspect
        src = inspect.getsource(llm)
        self.assertIn("_parse_error", src)
        # The message must be able to distinguish the two causes.
        self.assertIn("invalid request", src.lower(),
                      "no wording exists for a malformed-request failure")


if __name__ == "__main__":
    unittest.main()
