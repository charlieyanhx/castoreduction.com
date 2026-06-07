"""
F1 — the validation gate must WITHHOLD the headline numbers, not just banner them.

Failure-first: these tests assert the rendered Market Size section hides TAM/SAM/SOM
figures when validation.passed is False. They MUST fail on the pre-fix template (which
renders the numbers regardless) and pass after the fix.
"""
from __future__ import annotations

import re
import unittest

from jinja2 import Environment, FileSystemLoader

_env = Environment(loader=FileSystemLoader("templates"), autoescape=True)
_SRC = _env.loader.get_source(_env, "report.html")[0]


def _market_size_section() -> str:
    # Slice the FULL balanced market-sizing block: from its opening comment/if
    # through to (but not including) the next top-level block (financials).
    start = _SRC.index("<!-- MARKET SIZING (TAM/SAM/SOM) -->")
    end = _SRC.index("{% if financials and not financials.error %}", start)
    return _SRC[start:end]


def _render(market_sizing: dict) -> str:
    from market_sizing import format_currency
    return _env.from_string(_market_size_section()).render(
        market_sizing=market_sizing, format_currency=format_currency)


_PASS = {
    "tam": {"mid": 5_000_000_000, "low": 4e9, "high": 6e9, "label": "TAM"},
    "sam": {"mid": 1_000_000_000, "low": 8e8, "high": 1.2e9, "label": "SAM"},
    "som": {"mid": 50_000_000, "low": 4e7, "high": 6e7, "label": "SOM"},
    "validation": {"passed": True, "blocks": []},
}


def _blocked():
    d = {k: dict(v) if isinstance(v, dict) else v for k, v in _PASS.items()}
    d["validation"] = {"passed": False, "blocks": [{"msg": "SOM 9B > SAM 1B"}]}
    return d


class TestGateWithholdsNumbers(unittest.TestCase):
    def test_blocked_sizing_hides_headline_numbers(self):
        out = _render(_blocked())
        # The 24pt TAM currency must NOT appear when the gate failed.
        self.assertNotIn("$5.0B", out, "blocked sizing still rendered the TAM figure")
        self.assertNotIn("$1.0B", out, "blocked sizing still rendered the SAM figure")
        # The failure notice + the specific block must be shown instead.
        self.assertIn("failed validation", out.lower())
        self.assertIn("SOM 9B", out)   # the specific block reason is shown (HTML-escaped)
        self.assertIn("SAM 1B", out)

    def test_passing_sizing_shows_numbers(self):
        out = _render(_PASS)
        self.assertIn("$5.0B", out)   # TAM rendered normally when it passed
        self.assertIn("$1.0B", out)   # SAM rendered

    def test_no_validation_key_still_shows_numbers(self):
        # Backward-compat: legacy reports without a validation block still render.
        d = {k: dict(v) for k, v in _PASS.items() if k != "validation"}
        out = _render(d)
        self.assertIn("$5.0B", out)


if __name__ == "__main__":
    unittest.main()
