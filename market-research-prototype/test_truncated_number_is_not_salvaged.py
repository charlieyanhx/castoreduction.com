"""
Critical: json_repair commits a truncated number as a different, valid number.

`_parse_payload` falls back to `json_repair.loads` on a JSONDecodeError and accepts whatever
comes back, logging only "json_repair salvaged malformed output". That message does not
distinguish a STRUCTURAL repair (a missing closing brace, a trailing comma) from a VALUE
repair, which is not a repair at all — it is a fabrication.

MEASURED against the installed json_repair:

    '{"tam_usd": 1234567'                  -> {"tam_usd": 1234567}
    '{"tam_usd": 1234567, "sam_usd": 98'   -> {"sam_usd": 98}        <- was writing 98000000
    '{"households": 8872, "radius_m": 30'  -> {"radius_m": 30}       <- was writing 3000
    '{"price": 12.'                        -> {"price": 12.0}
    '{"a": 1, "b": 2.5e'                   -> {"b": 2.5}

Every one is valid JSON, returns with `error=None`, and flows into the report as a figure.
A radius of 30 m instead of 3,000 m is the same class of error as the 372x county-scale
trade area — arrived at by a different route.

THE KEY POINT, and why the fix is a refusal rather than a smarter parse: `{"tam_usd": 1234567`
is INDISTINGUISHABLE from a complete 1234567 that lost its brace and a truncated 12345670.
The text does not contain the information needed to tell them apart. So no parser can
recover the value — the only correct action is to decline the salvage and let the existing
retry re-ask. One extra call is a trivial price against publishing a fabricated figure.

Truncation at end-of-output is the expected case, not an exotic one: it is what a max_tokens
cutoff produces, and call_json's own history includes "bumped max_tokens — was dropping 7/8
candidates due to truncation".
"""
from __future__ import annotations

import unittest

from llm import _parse_payload


class TestATruncatedNumberIsRefused(unittest.TestCase):
    """A number cut off mid-write must never become a value."""

    def test_a_bare_trailing_integer_is_not_committed(self):
        obj, err = _parse_payload('{"tam_usd": 1234567')
        self.assertIsNone(obj, f"committed a possibly-truncated number: {obj}")
        self.assertTrue(err)

    def test_the_hundredfold_radius_case_is_refused(self):
        obj, err = _parse_payload('{"households": 8872, "radius_m": 30')
        self.assertIsNone(obj, "a 30m radius truncated from 3000m was accepted as valid")

    def test_a_number_cut_after_its_decimal_point_is_refused(self):
        obj, _ = _parse_payload('{"price": 12.')
        self.assertIsNone(obj, "12. became 12.0, which the model never wrote")

    def test_a_number_cut_inside_its_exponent_is_refused(self):
        obj, _ = _parse_payload('{"tam": 2.5e')
        self.assertIsNone(obj, "2.5e became 2.5 — off by whatever the exponent was")

    def test_a_negative_sign_alone_is_refused(self):
        obj, _ = _parse_payload('{"delta": -')
        self.assertIsNone(obj)

    def test_an_unterminated_string_is_refused(self):
        """Unambiguous: an odd count of unescaped quotes means the value was cut."""
        obj, _ = _parse_payload('{"name": "Acme Coff')
        self.assertIsNone(obj, "a truncated string was committed as the whole value")

    def test_the_error_says_truncation_not_just_invalid_json(self):
        """A caller reading the log must be able to tell a cutoff from a malformed reply —
        they have different fixes (raise max_tokens vs fix the prompt)."""
        _, err = _parse_payload('{"tam_usd": 1234567')
        self.assertIn("truncat", (err or "").lower(),
                      f"the error does not name the actual problem: {err!r}")


class TestStructuralRepairsStillWork(unittest.TestCase):
    """The salvage path earns its keep on real malformed-but-complete output. Refusing
    everything would trade a silent wrong number for a loud unnecessary failure."""

    def test_a_missing_closing_brace_after_a_closed_value_is_salvaged(self):
        """The final value must be provably COMPLETE for the salvage to be safe. A closed
        string qualifies (even count of unescaped quotes); a bare trailing number does not,
        and the refusal test above is the one that governs that shape."""
        obj, err = _parse_payload('{"verdict": "go", "notes": "unit economics hold"')
        self.assertIsNotNone(obj, "a closed final value was rejected")
        self.assertEqual(obj["notes"], "unit economics hold")

    def test_a_trailing_number_is_refused_even_though_it_looks_structural(self):
        """Written to pin the boundary honestly: this shape is indistinguishable from a
        truncation, so 'probably just a missing brace' is not a defence."""
        obj, _ = _parse_payload('{"verdict": "go", "score": 61')
        self.assertIsNone(obj)

    def test_a_trailing_comma_is_salvaged(self):
        obj, _ = _parse_payload('{"a": 1, "b": 2,}')
        self.assertEqual(obj, {"a": 1, "b": 2})

    def test_a_closed_but_unbraced_object_is_salvaged(self):
        obj, _ = _parse_payload('{"items": [1, 2, 3], "ok": true')
        self.assertEqual(obj["items"], [1, 2, 3])
        self.assertIs(obj["ok"], True)

    def test_well_formed_json_is_untouched(self):
        obj, err = _parse_payload('{"tam_usd": 1234567}')
        self.assertEqual(obj, {"tam_usd": 1234567})
        self.assertIsNone(err)

    def test_fenced_json_still_parses(self):
        obj, err = _parse_payload('```json\n{"a": 1}\n```')
        self.assertEqual(obj, {"a": 1})
        self.assertIsNone(err)

    def test_a_list_payload_still_parses(self):
        obj, _ = _parse_payload('[{"a": 1}, {"b": 2}]')
        self.assertEqual(len(obj), 2)


class TestTheRefusalIsDistinguishedFromABareParseFailure(unittest.TestCase):
    def test_a_genuinely_malformed_reply_is_still_a_plain_error(self):
        obj, err = _parse_payload("I cannot produce JSON for this request.")
        self.assertIsNone(obj)
        self.assertNotIn("truncat", (err or "").lower(),
                         "prose was misreported as a truncation, sending callers to raise "
                         "max_tokens when the prompt is the problem")


if __name__ == "__main__":
    unittest.main()
