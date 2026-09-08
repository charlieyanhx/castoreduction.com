"""resolve_naics is the classifier the whole grounded-benchmark path will hang off (#91).

It is broken in three ways today, and all three were MEASURED live against
api.census.gov with the repo's own CENSUS_API_KEY before this file was written.

1. IT ACCEPTS A 2-DIGIT SECTOR CODE. The guard is `2 <= len(code) <= 6`, and the LLM
   really does return bare sector codes ("mobile dog grooming" -> 81). The Economic
   Census answers a 2-digit code with HTTP 200 and a real row:

     NAICS 81      -> "Other services (except public administration)", 2342 estabs,
                      $7,396,566K  ->  $3,158,226 per establishment
     NAICS 812910  -> "Pet Care (except Veterinary) Services",  87 estabs,
                      $111,952K    ->  $1,286,805 per establishment

   A 2.45x overstatement carrying a genuine Census URL and vintage — a fabricated
   number would at least look like one. This is the worst failure mode this repo has:
   a credibility badge on a wrong-scale figure.

2. IT RETURNS CODES FROM THE WRONG NAICS VINTAGE. The prompt asks for NAICS 2022 and
   the model answers with 2017 codes. Measured: "bookstore" -> 451110, and

     /data/2022/ecnbasic?NAICS2022=451110  ->  EMPTY response
     /data/2022/ecnbasic?NAICS2022=459210  ->  "Book Retailers and News Dealers", 37 estabs

   A code that looks perfectly well-formed and silently retrieves nothing. Regex
   validation cannot see this; only the dataset can.

3. IT RETURNS None FOR ORDINARY CATEGORIES. Measured: "mobile dog grooming" -> None,
   though 812910 exists and answers. call_json is capped at max_tokens=60 and a
   non-dict return hits `raw.get(...)` -> AttributeError -> swallowed by the bare
   `except Exception: return None`. Every None here costs the venture its grounded
   anchor and silently falls back to the LLM revenue guess.

THE FIX IS VALIDATION BY USE, NOT A BIGGER REGEX. Whether a code is real is a
question only the dataset can answer, and it is vintage-specific — so the check is
"does this code retrieve a row", which #2 makes unavoidable anyway. The regex tightens
to exactly six digits because a sector aggregate is never an answer to "what industry
is this business", and a re-ask covers the truncation.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


def _clear():
    from tools.geo import _NAICS_CACHE
    _NAICS_CACHE.clear()


class TestSectorCodesNeverEscape(unittest.TestCase):
    """Defect 1 — the 2.45x hole."""

    def setUp(self):
        _clear()

    def test_a_two_digit_sector_code_is_refused(self):
        from tools.geo import resolve_naics

        with patch("llm.call_json", return_value={"naics": "81"}):
            self.assertIsNone(resolve_naics("mobile dog grooming"),
                              "sector code 81 escaped: $3.16M/estab vs pet care's $1.29M")

    def test_three_four_and_five_digit_codes_are_refused_too(self):
        """MEASURED on the SF county cell: broadening the industry costs far more than
        broadening the geography — 722515 $884,029/estab, 72251 and 7225 both
        $1,368,562 (+55%), 722 $1,397,022 (+58%), 72 $1,934,835 (+119%). A partial code
        is never a usable answer for this."""
        from tools.geo import resolve_naics

        for code in ("722", "7225", "72251"):
            _clear()
            with patch("llm.call_json", return_value={"naics": code}):
                self.assertIsNone(resolve_naics("specialty coffee shop"),
                                  f"{code} escaped as if it were an industry")

    def test_a_six_digit_code_is_accepted(self):
        from tools.geo import resolve_naics

        with patch("llm.call_json", return_value={"naics": "722515"}):
            self.assertEqual(resolve_naics("specialty coffee shop"), "722515")


class TestMalformedRepliesDoNotSilentlyBecomeNone(unittest.TestCase):
    """Defect 3 — every None costs a venture its grounded anchor, so a recoverable
    reply must be recovered rather than swallowed."""

    def setUp(self):
        _clear()

    def test_an_integer_reply_is_read_not_dropped(self):
        """MEASURED: the model returns a bare int for some categories. `raw.get` raises
        AttributeError on it and the bare except turns a usable answer into None."""
        from tools.geo import resolve_naics

        with patch("llm.call_json", return_value={"naics": 722515}):
            self.assertEqual(resolve_naics("specialty coffee shop"), "722515")

    def test_a_code_with_surrounding_prose_is_extracted(self):
        from tools.geo import resolve_naics

        with patch("llm.call_json", return_value={"naics": "NAICS 812910"}):
            self.assertEqual(resolve_naics("mobile dog grooming"), "812910")

    def test_a_truncated_first_reply_is_re_asked_once(self):
        """max_tokens=60 truncates; the logs say so verbatim ("output ends in an
        unterminated string ... likely a max_tokens cutoff"). One re-ask, not a None."""
        from tools.geo import resolve_naics

        with patch("llm.call_json", side_effect=[None, {"naics": "812910"}]) as m:
            self.assertEqual(resolve_naics("mobile dog grooming"), "812910")
        self.assertEqual(m.call_count, 2, "no re-ask was attempted")

    def test_it_gives_up_rather_than_guessing(self):
        """The contract stays: a digit string or None, NEVER a guess."""
        from tools.geo import resolve_naics

        with patch("llm.call_json", return_value={"naics": "not-a-code"}):
            self.assertIsNone(resolve_naics("interdimensional widgets"))

    def test_re_asking_is_bounded(self):
        from tools.geo import resolve_naics

        with patch("llm.call_json", return_value=None) as m:
            self.assertIsNone(resolve_naics("interdimensional widgets"))
        self.assertLessEqual(m.call_count, 2, "unbounded re-asking burns the rate budget")

    def test_a_raising_backend_still_returns_none(self):
        from tools.geo import resolve_naics

        with patch("llm.call_json", side_effect=RuntimeError("backend down")):
            self.assertIsNone(resolve_naics("specialty coffee shop"))


class TestTheCacheStillWorks(unittest.TestCase):
    def setUp(self):
        _clear()

    def test_a_resolved_code_is_memoized(self):
        from tools.geo import resolve_naics

        with patch("llm.call_json", return_value={"naics": "722515"}) as m:
            resolve_naics("specialty coffee shop")
            resolve_naics("specialty coffee shop")
        self.assertEqual(m.call_count, 1)

    def test_a_refused_code_is_not_memoized_as_an_answer(self):
        """Caching a rejection as a value would make one bad reply permanent for the
        session — and _NAICS_CACHE is checked before the code is validated."""
        from tools.geo import _NAICS_CACHE, resolve_naics

        with patch("llm.call_json", return_value={"naics": "81"}):
            resolve_naics("mobile dog grooming")
        self.assertNotIn("81", _NAICS_CACHE.values())


class TestValidationByUse(unittest.TestCase):
    """Defect 2 — a well-formed code from the wrong vintage retrieves nothing.

    Only the dataset knows its own vocabulary, so the caller that is about to query it
    must be able to ask "would this code retrieve anything" instead of trusting the
    shape. This is the seam #91's receipts lookup needs.
    """

    def test_a_validator_hook_exists_for_callers_that_have_a_vocabulary(self):
        from tools.geo import resolve_naics

        seen = []

        def only_2022(code: str) -> bool:
            seen.append(code)
            return code == "459210"          # 451110 is NAICS 2017 and retrieves nothing

        _clear()
        with patch("llm.call_json", side_effect=[{"naics": "451110"},
                                                 {"naics": "459210"}]):
            self.assertEqual(resolve_naics("bookstore", is_valid=only_2022), "459210")
        self.assertIn("451110", seen, "the rejected code was never offered to the validator")

    def test_without_a_validator_the_behaviour_is_unchanged(self):
        from tools.geo import resolve_naics

        _clear()
        with patch("llm.call_json", return_value={"naics": "451110"}):
            self.assertEqual(resolve_naics("bookstore"), "451110")


if __name__ == "__main__":
    unittest.main()
