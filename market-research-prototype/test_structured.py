"""
Wave 1 item 1 (D2-3): structured LLM output — Pydantic-validated, auto re-ask.

call_json(response_model=...) must validate the provider's JSON against the given
schema and, on malformed JSON or validation failure, RE-ASK the chain with the error
embedded (instructor-style corrective loop) instead of returning _parse_error on the
first miss. The _parse_error contract survives only as the exhausted-retries last
resort, so existing callers' `.get("_parse_error")` checks keep working.

All provider traffic is mocked at the single choke point (_try_one_backend); the
cross-provider chain, backoff, and cache logic run for real.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pydantic import BaseModel

from llm import call_json


class _Sizing(BaseModel):
    tam_usd: float
    method: str


def _ok(text: str):
    """A successful _try_one_backend return: (text, in_tok, out_tok, model)."""
    return (text, 10, 5, "mock-model")


class TestStructuredOutput(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("LLM_CACHE_BYPASS")
        os.environ["LLM_CACHE_BYPASS"] = "1"
        # Backend detection needs *a* key present; _try_one_backend is mocked so the
        # value is never used and no network is touched.
        self._old_key = os.environ.get("GROQ_API_KEY")
        if not self._old_key:
            os.environ["GROQ_API_KEY"] = "gsk_test_not_a_real_key"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("LLM_CACHE_BYPASS", None)
        else:
            os.environ["LLM_CACHE_BYPASS"] = self._old
        if self._old_key is None:
            os.environ.pop("GROQ_API_KEY", None)

    def test_valid_first_try_returns_coerced_dict(self):
        with patch("llm._try_one_backend",
                   side_effect=[_ok('{"tam_usd": "42.5", "method": "topdown"}')]) as m:
            out = call_json("size the market", "desc", response_model=_Sizing)
        self.assertEqual(out, {"tam_usd": 42.5, "method": "topdown"})  # str -> float coerced
        self.assertEqual(m.call_count, 1)

    def test_schema_is_in_the_prompt(self):
        with patch("llm._try_one_backend",
                   side_effect=[_ok('{"tam_usd": 1, "method": "x"}')]) as m:
            call_json("size the market", "desc", response_model=_Sizing)
        system_sent = m.call_args_list[0][0][1]
        self.assertIn("tam_usd", system_sent)          # schema fields visible to the model
        self.assertIn("method", system_sent)

    def test_reask_on_validation_error_embeds_the_error(self):
        with patch("llm._try_one_backend", side_effect=[
            _ok('{"method": "topdown"}'),                      # missing tam_usd
            _ok('{"tam_usd": 1000000000, "method": "topdown"}'),
        ]) as m:
            out = call_json("size the market", "desc", response_model=_Sizing)
        self.assertEqual(out["tam_usd"], 1000000000)
        self.assertEqual(m.call_count, 2)
        reask_user = m.call_args_list[1][0][2]
        self.assertIn("tam_usd", reask_user)           # the validation error names the field
        self.assertIn("desc", reask_user)              # original request still present

    def test_reask_on_malformed_json(self):
        with patch("llm._try_one_backend", side_effect=[
            _ok(""),                                            # unparseable, unrepairable
            _ok('{"tam_usd": 5, "method": "bottomup"}'),
        ]) as m:
            out = call_json("size the market", "desc", response_model=_Sizing)
        self.assertEqual(out["method"], "bottomup")
        self.assertEqual(m.call_count, 2)

    def test_exhausted_retries_return_parse_error_contract(self):
        bad = _ok('{"method": "no tam here"}')
        with patch("llm._try_one_backend", side_effect=[bad, bad, bad]) as m:
            out = call_json("size the market", "desc", response_model=_Sizing,
                            max_retries=2)
        self.assertIn("_parse_error", out)             # last-resort contract preserved
        self.assertIn("_raw", out)
        self.assertEqual(m.call_count, 3)              # initial + 2 re-asks, then give up

    def test_schemaless_valid_path_unchanged(self):
        with patch("llm._try_one_backend", side_effect=[_ok('{"a": 1}')]) as m:
            out = call_json("s", "u")
        self.assertEqual(out, {"a": 1})
        self.assertEqual(m.call_count, 1)

    def test_schemaless_malformed_gets_one_repair_reask(self):
        # Previously this returned _parse_error immediately; now one corrective re-ask.
        with patch("llm._try_one_backend", side_effect=[_ok(""), _ok('{"a": 1}')]) as m:
            out = call_json("s", "u")
        self.assertEqual(out, {"a": 1})
        self.assertEqual(m.call_count, 2)

    def test_cache_key_distinguishes_schema(self):
        from llm import _cache_key
        self.assertNotEqual(_cache_key("s", "u", None), _cache_key("s", "u", _Sizing))

    def test_validated_result_is_cached(self):
        os.environ["LLM_CACHE_BYPASS"] = "0"
        puts = {}
        with patch("cache.get", return_value=None), \
             patch("cache.put", side_effect=lambda k, v: puts.update({k: v})), \
             patch("llm._try_one_backend",
                   side_effect=[_ok('{"tam_usd": 7, "method": "x"}')]):
            out = call_json("s-cache", "u-cache", response_model=_Sizing)
        self.assertEqual(out["tam_usd"], 7.0)
        self.assertTrue(any(v.get("tam_usd") == 7.0 for v in puts.values()),
                        f"validated dict not cached: {puts}")


if __name__ == "__main__":
    unittest.main()
