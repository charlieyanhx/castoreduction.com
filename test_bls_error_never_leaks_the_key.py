"""BLS echoes your API key back inside its error text, and we persist error text.

MEASURED LIVE (2026-08-12), the first time a real BLS_API_KEY was configured. BLS
rejected it and answered HTTP 200 with:

    status  REQUEST_NOT_PROCESSED
    message "The key:<32-hex-secret> provided by the User is invalid. Please provide
             a proper key for the operation to be successful"

_bls_why() appends that message verbatim to our own error string, and that string is
NOT a transient log line — persistence/ledger.py:153 stores it on the tool event:

    "error": (error or "")[:140]

Count the characters for bls_cex_spend's phrasing:

    "BLS returned no usable value for CXUFOODAWAYLB0101M "   51
    "[BLS REQUEST_NOT_PROCESSED: "                           28  ->  79
    "The key:"                                                8  ->  87
    the secret itself                                        32  -> 119

119 < 140, so the ENTIRE key lands in the append-only ledger, the per-run transcript,
and anything rendered from them. The 140-char truncation is not a safety mechanism — it
is a coincidence, and on the quintile-curve path it merely truncates the secret at 27 of
32 characters instead of hiding it.

Nothing had leaked yet when this was found (grepped every json/jsonl/log/html on disk
plus git: clean) because the failing probe ran outside the job system. The next keyed
run inside it would have written the secret to durable storage.

THE FIX IS AT THE CHOKE POINT. _bls_why is the only place a BLS response message becomes
our text, so redaction belongs there — not at each of the two call sites, and not in the
ledger (a secret should never reach the thing whose whole job is to remember forever).
Two layers: the configured key by exact value, and any long hex token after "key" by
shape, so a rotated or second key is covered before anyone thinks to update this code.

The DIAGNOSTIC MUST SURVIVE. _bls_why exists because an exhausted quota returns HTTP 200
and used to read as a bad series id; redaction that ate the status or the quota hint
would trade a leaked secret for the blindness that made this function necessary.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.econ import _bls_why

_FAKE_KEY = "0123456789abcdef0123456789abcdef"  # 32 hex, same shape as a real BLS key


def _invalid_key_response(key: str = _FAKE_KEY) -> dict:
    """The measured live shape, verbatim apart from the secret."""
    return {"status": "REQUEST_NOT_PROCESSED",
            "responseTime": 0,
            "message": [f"The key:{key} provided by the User is invalid. Please "
                        "provide a proper key for the operation to be successful"]}


class TestTheConfiguredKeyIsRedactedByValue(unittest.TestCase):
    def test_the_key_does_not_appear_in_the_diagnostic(self):
        with patch.dict("os.environ", {"BLS_API_KEY": _FAKE_KEY}):
            why = _bls_why(_invalid_key_response())
        self.assertNotIn(_FAKE_KEY, why,
                         "the BLS API key is still echoed into text we persist")

    def test_something_still_says_the_key_was_rejected(self):
        """Redaction must not silence the reason — a run that fails on a bad key has to
        be diagnosable without the operator guessing."""
        with patch.dict("os.environ", {"BLS_API_KEY": _FAKE_KEY}):
            why = _bls_why(_invalid_key_response())
        self.assertIn("REQUEST_NOT_PROCESSED", why)
        self.assertIn("invalid", why.lower())
        self.assertIn("REDACTED", why.upper())

    def test_an_unset_key_is_not_matched_as_the_empty_string(self):
        """A naive replace(os.getenv('BLS_API_KEY'), ...) with no key set would splice
        REDACTED between every character."""
        with patch.dict("os.environ", {}, clear=True):
            why = _bls_why({"status": "REQUEST_NOT_PROCESSED",
                            "message": ["some unrelated failure"]})
        self.assertIn("some unrelated failure", why)
        self.assertNotIn("REDACTEDs", why)


class TestKeyShapedTokensAreRedactedEvenIfUnconfigured(unittest.TestCase):
    """Defence in depth: the key that leaks might not be the one in this process's env
    (rotated, second account, a colleague's key in a shared log)."""

    def test_a_hex_token_after_key_is_redacted_with_no_env_key_at_all(self):
        with patch.dict("os.environ", {}, clear=True):
            why = _bls_why(_invalid_key_response("deadbeefcafebabe0123456789abcdef"))
        self.assertNotIn("deadbeefcafebabe0123456789abcdef", why)

    def test_a_different_key_than_the_configured_one_is_still_redacted(self):
        other = "ffffffffffffffffffffffffffffffff"
        with patch.dict("os.environ", {"BLS_API_KEY": _FAKE_KEY}):
            why = _bls_why(_invalid_key_response(other))
        self.assertNotIn(other, why)

    def test_ordinary_numbers_in_a_message_are_left_alone(self):
        """The quota message carries real numbers; redaction must not eat them."""
        with patch.dict("os.environ", {}, clear=True):
            why = _bls_why({"status": "REQUEST_NOT_PROCESSED", "message": [
                "The daily threshold for total number of requests allocated to the "
                "user with 25 requests has been reached"]})
        self.assertIn("25 requests", why)


class TestTheQuotaDiagnosticIsUnchanged(unittest.TestCase):
    """Regression guard on why _bls_why exists at all."""

    def test_the_quota_hint_still_fires(self):
        why = _bls_why({"status": "REQUEST_NOT_PROCESSED", "message": [
            "the daily threshold for total number of requests allocated to the user "
            "has been reached"]})
        self.assertIn("threshold", why)
        self.assertIn("BLS_API_KEY", why, "the actionable hint disappeared")
        self.assertIn("500/day", why)

    def test_a_successful_response_still_adds_nothing(self):
        self.assertEqual(_bls_why({"status": "REQUEST_SUCCEEDED"}), "")
        self.assertEqual(_bls_why(None), "")
        self.assertEqual(_bls_why({}), "")


class TestNoToolSurfacesTheKey(unittest.TestCase):
    """End to end through both call sites — the invariant is about what a tool RETURNS,
    because that is what the ledger records."""

    def _resp(self, payload):
        class R:
            status_code = 200

            def json(self):
                return payload
        return R()

    def test_the_quintile_curve_error_carries_no_key(self):
        from tools import econ

        with patch.dict("os.environ", {"BLS_API_KEY": _FAKE_KEY,
                                       "CEX_CURVE_CACHE_PATH": "/nonexistent/nope.json"}), \
             patch.object(econ, "_curve_cache_read", return_value=None), \
             patch("scrape.http.request", return_value=self._resp(_invalid_key_response())):
            ev = econ.cex_income_quintile_curve(item_code="FOODAWAY")
        self.assertTrue(ev.error, "expected the invalid-key failure to surface")
        self.assertNotIn(_FAKE_KEY, ev.error)

    def test_the_spend_call_error_carries_no_key(self):
        """The measured worst case: this phrasing fits inside the ledger's 140-char
        slice with the whole secret intact."""
        from tools import econ

        with patch.dict("os.environ", {"BLS_API_KEY": _FAKE_KEY}), \
             patch("scrape.http.request", return_value=self._resp(_invalid_key_response())):
            ev = econ.bls_cex_spend(series_id="CXUFOODAWAYLB0101M")
        self.assertTrue(ev.error)
        self.assertNotIn(_FAKE_KEY, ev.error)
        self.assertNotIn(_FAKE_KEY, (ev.error or "")[:140],
                         "the key survives inside exactly the slice the ledger stores")


if __name__ == "__main__":
    unittest.main()
