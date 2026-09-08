"""The product looked evasive about its own arithmetic, twice over.

A reader marked "TAM sits at ~$986M with an obtainable SOM of $2.3M", asked "show me the
method", and was told:

    "This report data artifact does not contain the specific mathematical modeling or
     source formulas used to derive the ~$986M TAM and $2.3M SOM figures."

It does. market_sizing.tam.method_top_down.calculation reads, verbatim:

    "$7B Global AI Developer Tooling (IDC 2024) x 50% US x 45% RAG pipeline share = $1.575B"

TWO SEPARATE LOSSES OF THE FOUNDER'S OWN CONTEXT, one in each direction.

  ANSWERING. _digest handed the model `json.dumps(result)[:14000]`. MEASURED on a real
  report: 280,162 characters of result, 14,000 forwarded — five per cent — sliced
  mid-structure so it was not even parseable JSON. Four of thirty-eight sections survived,
  and only because `discover` is a long list of competitors sitting near the front that ate
  the budget before the analysis was reached. market_sizing, economics, financials,
  pricing, validation, viability: all absent. The model was honest about a context nobody
  gave it, and its honesty read as the report having no method.

  REVISING. add_annotation stores quote[:400] and comment[:1000]. build_revision_brief
  forwarded quote[:80] and comment[:200], so four fifths of a founder's correction was
  discarded on the way to the one run that exists to act on it. A correction cut at 200
  characters loses the number, the reason, or both.

The fix in both places is the same idea: shrink the SHAPE, never the tail. Sections stay
present and their contents get shorter; corrections ride whole.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest


def _fat_report() -> dict:
    """A result shaped like the real thing: a huge evidence list in front of the analysis,
    which is precisely the arrangement that starved the sizing section."""
    return {
        "_trace": ["internal"] * 400,
        "profile": {"name": "A record shop", "summary": "s" * 900},
        # WIDE, NOT JUST LONG. Trimming lists to eight was never the problem: the real
        # report's evidence sections are dict-heavy, so per-item truncation barely dents
        # them and the flat character cap still landed before the analysis. A fixture made
        # only of long lists is compressible enough that the OLD digest passes, which is
        # how a test can look like it proves a fix and prove nothing.
        "discover": {
            "competitors": [{"brand": f"Rival {i}", "domain": f"r{i}.example",
                             "notes": "n" * 900} for i in range(120)],
            **{f"finding_{i}": {"claim": "c" * 700, "evidence": "e" * 700,
                                "source": f"https://example.com/{i}"} for i in range(40)},
        },
        "customer_universe": {
            **{f"segment_{i}": {"companies": [f"Co {j}" for j in range(30)],
                                "rationale": "r" * 800} for i in range(30)},
        },
        "market_sizing": {
            "tam": {"value_usd": 986_500_000,
                    "method_top_down": {
                        "calculation": "$7B market x 50% US x 45% share = $1.575B",
                        "source": "IDC Worldwide AI Applications 2024"}},
            "som": {"mid": 2_304_000, "method": "analog-anchored",
                    "comparable_anchor": "Kapa.ai ~$3M Y3 ARR"},
            "notes": ["Bottom-up grounded in live Census count."],
        },
        "economics": {"break_even_units": 812, "fixed_cost_monthly": 5000},
        "financials": {"y3_revenue": 2_304_000},
        "pricing": {"psm": {"optimal": 48}},
        "validation": {"checks": ["census", "psm"]},
        "viability": {"viability_score": 7},
    }


class TheAnswererSeesTheWholeReport(unittest.TestCase):
    def setUp(self):
        import iteration
        self.iteration = iteration
        self.result = _fat_report()
        self.digest = iteration._digest(self.result)

    def test_the_sizing_method_reaches_it(self):
        """The reported failure, as a rule. This is the section the question was about."""
        self.assertIn("market_sizing", self.digest)
        self.assertIn("$7B market x 50% US x 45% share", self.digest,
                      "the formula the reader asked for must be in the model's context")

    def test_every_visible_section_survives(self):
        """Not 'most'. A question can be about any of them, and a section that did not
        make the cut is one the product will deny having."""
        for key in ("profile", "market_sizing", "economics", "financials",
                    "pricing", "validation", "viability", "discover"):
            self.assertIn(f'"{key}"', self.digest, key)

    def test_a_fat_evidence_list_does_not_starve_the_analysis(self):
        """The exact mechanism of the bug: `discover` came first and ate everything."""
        d = json.loads(self.digest)
        self.assertIn("market_sizing", d)
        self.assertLess(len(d["discover"]["competitors"]), 120,
                        "the long list is what should shrink")

    def test_it_is_valid_json(self):
        """A model handed an object cut mid-structure has to guess where it was cut."""
        json.loads(self.digest)

    def test_a_shortened_list_says_it_was_shortened(self):
        """Otherwise three of forty competitors reads as a complete competitive landscape,
        and the report claims something the evidence never said."""
        d = json.loads(self.digest)
        tail = d["discover"]["competitors"][-1]
        self.assertIn("more not shown", json.dumps(tail))

    def test_internal_bookkeeping_is_still_dropped(self):
        self.assertNotIn("_trace", self.digest)

    def test_it_stays_within_its_budget(self):
        self.assertLessEqual(len(self.digest), 60000)

    def test_a_small_report_is_handed_over_nearly_whole(self):
        """The tightening is a response to size, not a tax on every report."""
        small = {"profile": {"name": "x"}, "market_sizing": {"tam": {"value_usd": 1}}}
        d = json.loads(self.iteration._digest(small))
        self.assertEqual(d, small)


class TheNextRunGetsTheWholeCorrection(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old

    LONG = ("Our rent is 7800 a month, not 5000. That is the single biggest input to "
            "break-even and the whole economics section is built on the wrong number. We "
            "signed a five year lease in March at 7800 with a 3% annual escalator, so year "
            "two is 8034 and year three is 8275. Redo the break-even and the three year "
            "scenarios with those figures, and note the escalator explicitly because it "
            "changes the year three picture materially.")
    QUOTE = ("Fixed cost of $5,000/mo covers rent, utilities and insurance for a 1,200 "
             "square foot space on a three year lease")

    def _brief(self):
        import iteration
        iteration.add_annotation("j1", section="Economics", quote=self.QUOTE,
                                 comment=self.LONG)
        return iteration.build_revision_brief("j1", "A bookshop in Sellwood, Portland.")

    def test_the_correction_rides_in_full(self):
        """434 characters of first-hand knowledge, previously cut to 200."""
        self.assertIn(self.LONG, self._brief())

    def test_the_marked_passage_rides_in_full(self):
        """A quote cut at 80 characters often does not identify the sentence it is about."""
        self.assertIn(self.QUOTE, self._brief())

    def test_the_original_brief_is_still_there(self):
        self.assertIn("A bookshop in Sellwood, Portland.", self._brief())

    def test_each_mark_is_a_separate_numbered_instruction(self):
        """Five corrections joined by semicolons read as one vague complaint."""
        import iteration
        for i in range(3):
            iteration.add_annotation("j2", section="Economics", quote=f"passage {i}",
                                     comment=f"correction number {i}")
        brief = iteration.build_revision_brief("j2", "A bookshop.")
        for i in (1, 2, 3):
            self.assertIn(f"({i})", brief)

    def test_it_says_what_addressing_a_mark_means(self):
        """"Must address" without a definition is satisfied by ignoring it."""
        brief = self._brief()
        self.assertIn("say plainly in the report why the original still stands", brief)

    def test_the_section_is_named(self):
        self.assertIn("in Economics", self._brief())

    def test_no_marks_means_no_instructions(self):
        import iteration
        self.assertEqual(iteration.build_revision_brief("empty", "A bookshop."),
                         "A bookshop.")


if __name__ == "__main__":
    unittest.main()
