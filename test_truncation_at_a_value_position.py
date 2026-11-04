"""
My own truncation guard had a hole, and json_repair fabricated through it for a week.

I added `llm._truncated_value` earlier in this program to refuse truncated LLM JSON instead of
salvaging it. It works by ENUMERATING the shapes a cut *value* can take. That is the wrong shape
of solution, and this is the bill: a cut landing at a value POSITION is not in the list, so it is
declared clean. Measured by execution:

    _truncated_value('{"a": 1, "source":')        -> None      <- HOLE
    _truncated_value('{"a": 1, "source": ')       -> None      <- HOLE
    _truncated_value('{"a": 1, "source"')         -> None      <- HOLE
    _truncated_value('{"a": 1, "source": "Comp')  -> 'an unterminated string'          (caught)
    _truncated_value('{"a": 1, "n": 30')          -> 'a number with no closing ...'    (caught)

WHAT IT COST, traced end to end. run4's product 4Ps call (four_ps.py:730, max_tokens=3500) was cut
inside citation #2, ending exactly at `{"id": 2, "source":`. The guard passed it, so json_repair
"completed" it to `{"id": 2, "source": ""}` — a value the model never wrote — and silently dropped
`narrative` and every field after the cut. four_ps then synthesized a narrative out of
key_takeaways and reported success. The model had been writing 4 citations to match the 4
superscript markers in its prose; 2 survived. Those two orphaned markers are the
`dangling_citations` BLOCK that made run5, run6 and run7 unpublishable.

AND THE BAD PARSE WAS CACHED FOR SEVEN DAYS. call_json caches the salvaged dict (llm.py:568)
under cache.py's TTL_SECONDS = 7*24*3600. The poisoned row llm_json:2a45820963320f20 was written
at run4's timestamp and replayed by run5/6/7 — the product block is byte-identical across all
four runs. So "the blocker fires on 3 of 3 runs" was ONE frozen artifact, not three failures, and
it would have expired ~2026-08-11 and looked like a self-healing fix.

TWO FIXES, and the second matters as much as the first:
  1. Detect a cut at a value position — STRUCTURALLY, by scanning the JSON, not by adding two
     more entries to a list of known-bad endings. The list will keep having holes; that is what
     just happened.
  2. Never cache a parse that needed repair. Caching a repaired result freezes a defect into
     every subsequent run for a week, which converted one truncated response into three
     unpublishable reports.

WHAT MUST STAY TRUE: structural damage AFTER a provably complete value is still salvageable — a
missing closing brace, a trailing comma, a closed bracket. Nothing there is ambiguous and
refusing it would burn a retry for no reason. Those cases are asserted below as guardrails.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from llm import _parse_payload, _truncated_value


class TestACutAtAValuePositionIsTruncated(unittest.TestCase):
    """The hole. In each of these the model was mid-pair: the key exists, the value does not."""

    HOLES = [
        ('{"id": 2, "source":', "colon then nothing"),
        ('{"id": 2, "source": ', "colon, whitespace, nothing"),
        ('{"id": 2, "source"', "key closed, no colon"),
        ('{"a": 1, "b": {"c":', "nested, colon then nothing"),
        ('{"rows": [{"brand": "X", "price":', "inside an array of objects"),
        ('{"a": 1,\n  "narrative"', "key on its own line, newline-padded"),
    ]

    def test_each_hole_is_named_as_truncated(self):
        for text, why in self.HOLES:
            with self.subTest(why=why):
                self.assertIsNotNone(_truncated_value(text),
                                     f"{why}: {text!r} still reads as ending cleanly")

    def test_each_hole_is_refused_rather_than_repaired(self):
        for text, why in self.HOLES:
            with self.subTest(why=why):
                obj, err = _parse_payload(text)
                self.assertIsNone(obj, f"{why}: json_repair fabricated {obj!r}")
                self.assertTrue(err, f"{why}: refused without saying why")

    def test_the_real_run4_product_payload_is_refused(self):
        """The exact shape that poisoned the cache: cut inside citation #2's source."""
        text = ('{"key_takeaways": ["Limit pastry program to 4 items"], '
                '"citations": [{"id": 1, "source": "Max-Diff Feature Importance Ranking"}, '
                '{"id": 2, "source":')
        obj, err = _parse_payload(text)
        self.assertIsNone(obj, f"still salvaged: {obj!r}")
        self.assertTrue(err)

    def test_json_repair_would_have_fabricated_an_empty_source(self):
        """Documents WHY refusing matters — proves the fabrication is real, not theoretical."""
        try:
            import json_repair
        except ImportError:
            self.skipTest("json_repair not installed")
        got = json_repair.loads('{"id": 2, "source":')
        self.assertEqual(got, {"id": 2, "source": ""},
                         f"json_repair behaviour changed; it now returns {got!r}")


class TestSalvageableDamageStillSalvages(unittest.TestCase):
    """Guardrails. Refusing these would burn a retry on an unambiguous, complete value."""

    SAFE = [
        ('{"verdict": "go", "notes": "unit economics hold"', "missing brace after a closed string"),
        ('{"a": 1, "b": 2,}', "trailing comma"),
        ('{"items": [1, 2, 3], "ok": true', "closed bracket, then true"),
        ('{"a": {"b": "c"}', "closed nested object"),
        ('{"a": 1, "b": null', "null value, missing brace"),
    ]

    def test_they_are_not_flagged_as_truncated(self):
        for text, why in self.SAFE:
            with self.subTest(why=why):
                self.assertIsNone(_truncated_value(text),
                                  f"{why}: {text!r} is now refused, which burns a retry")

    def test_they_still_parse(self):
        for text, why in self.SAFE:
            with self.subTest(why=why):
                obj, _ = _parse_payload(text)
                self.assertIsNotNone(obj, f"{why}: no longer salvaged")

    def test_a_trailing_comma_inside_an_object_is_still_safe(self):
        """A comma means another pair was COMING but none was written — nothing is fabricated by
        dropping it, unlike a colon, where a value is missing from a pair that exists."""
        obj, _ = _parse_payload('{"a": 1,')
        self.assertIsNotNone(obj)

    def test_the_previously_caught_shapes_are_still_caught(self):
        for text in ('{"tam_usd": 1234567', '{"households": 8872, "radius_m": 30',
                     '{"price": 12.', '{"tam": 2.5e', '{"delta": -',
                     '{"name": "Acme Coff'):
            with self.subTest(text=text):
                self.assertIsNotNone(_truncated_value(text))

    def test_valid_json_is_untouched(self):
        obj, err = _parse_payload('{"tam_usd": 1234567}')
        self.assertEqual(obj, {"tam_usd": 1234567})
        self.assertIsNone(err)

    def test_prose_is_not_mistaken_for_truncation(self):
        """A refusal sentence ends in '.', and a bare '.' is not a cut number."""
        self.assertIsNone(_truncated_value("I cannot produce JSON for this request."))


class TestARepairedParseIsNeverCached(unittest.TestCase):
    """Caching a repaired parse turned one truncated response into three unpublishable reports.
    The cache must hold only results that parsed cleanly."""

    def test_parse_payload_reports_whether_repair_was_needed(self):
        from llm import _parse_payload_ex
        clean_obj, clean_err, clean_repaired = _parse_payload_ex('{"a": 1}')
        self.assertEqual(clean_obj, {"a": 1})
        self.assertFalse(clean_repaired, "a clean parse was reported as repaired")
        rep_obj, rep_err, rep_repaired = _parse_payload_ex('{"a": 1, "b": "x"')
        self.assertIsNotNone(rep_obj, "the salvageable case stopped salvaging")
        self.assertTrue(rep_repaired, "a json_repair salvage was not flagged as repaired")

    def test_a_clean_response_is_cached(self):
        import llm
        with patch.object(llm, "_chain_text", return_value='{"verdict": "go"}'), \
             patch("cache.get", return_value=None), \
             patch("cache.put") as put:
            out = llm.call_json(system="s", user="u", max_tokens=50)
        self.assertEqual(out.get("verdict"), "go")
        self.assertTrue(put.called, "a clean parse was not cached — caching still works")

    def test_a_repaired_response_is_not_cached(self):
        import llm
        with patch.object(llm, "_chain_text", return_value='{"verdict": "go", "n": "x"'), \
             patch("cache.get", return_value=None), \
             patch("cache.put") as put:
            out = llm.call_json(system="s", user="u", max_tokens=50)
        self.assertEqual(out.get("verdict"), "go",
                         "the repaired result should still be RETURNED to this caller")
        self.assertFalse(put.called,
                         "a json_repair-salvaged result was written to the 7-day cache, which is "
                         "how one truncated response poisoned three consecutive runs")


if __name__ == "__main__":
    unittest.main()
