"""A section the table declares must be one the reports actually have.

MEASURED across the 19-report corpus: three of the 22 declared sections were present
ZERO times. Two of them were bugs of the same shape, and both were invisible:

  * "pricing_benchmark" -- the benchmark table lives at result["pricing"]["benchmark"],
    which is exactly where render_html reads it from. The provenance entry looked for a
    top-level "pricing_benchmark" key that nothing has ever written. So the section
    rendered on every report while its attribution silently matched nothing.
  * "integrity" -- build_integrity_summary(result) is called BY THE RENDERER. It is not a
    result key and never was, so a check for one could only ever fail.

build_section_provenance skips a section whose key is absent, so neither failure produced
an error, a warning or a gap. They produced silence -- on the two sections a buyer is most
likely to interrogate.

This is the same defect class as the verifier rendering nothing when it had not run: a
LOOKUP THAT MISSES is being read as A THING THAT DID NOT HAPPEN. The fix in both cases is
to make the absent case impossible to confuse with the empty one, and then to test that no
new declaration can quietly join them.

`research_brief` is the honest third case and is allowlisted: the research crew is DEEP
effort only, the corpus is standard effort, so 0/19 is correct rather than broken.
"""
from __future__ import annotations

import json
import pathlib
import unittest

from report.section_provenance import SECTION_SOURCES, _present

#: Sections legitimately absent from the standard-effort corpus, with the reason.
#: A section may only be added here with one, and adding one is the thing this file is
#: watching for -- see the count assertion below.
_LEGITIMATELY_ABSENT = {
    "research_brief": "the research crew runs on DEEP effort only; the corpus is standard",
}

_CORPUS = pathlib.Path("out/wave4_corpus")


def _reports() -> list[dict]:
    out = []
    for f in sorted(_CORPUS.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append(d.get("result") or d)
    return out


class TestEveryDeclaredSectionIsReachable(unittest.TestCase):
    def setUp(self):
        self.reports = _reports()
        if not self.reports:
            self.skipTest("no corpus available")

    def test_no_section_is_declared_and_never_present(self):
        """A section present in zero reports is either a wrong key or a dead declaration.
        Both are worth failing over, because neither announces itself."""
        never = [s.result_key for s in SECTION_SOURCES
                 if not any(_present(r, s.result_key) for r in self.reports)]
        self.assertEqual(sorted(set(never) - set(_LEGITIMATELY_ABSENT)), [],
                         "declared section(s) that no report has ever carried")

    def test_the_absent_allowlist_stays_small_and_reasoned(self):
        """Allowlisting is how this check stops working. Every entry needs a reason, and
        the count is pinned so a new one cannot be added quietly."""
        self.assertLessEqual(len(_LEGITIMATELY_ABSENT), 1)
        for key, reason in _LEGITIMATELY_ABSENT.items():
            self.assertTrue(reason.strip(), f"{key} is allowlisted with no reason")

    def test_the_two_repaired_sections_are_attributed_again(self):
        """The regression this file exists for, stated as the specific case.

        Both render on essentially every report. If either drops back to zero, the page
        is once more claiming a section with no producer behind it.
        """
        for key in ("pricing_benchmark", "integrity"):
            n = sum(1 for r in self.reports if _present(r, key))
            self.assertGreater(n, 0, f"{key} is attributed on no report")


class TestNestedAndDerivedSectionsResolve(unittest.TestCase):
    def test_a_nested_path_is_followed_rather_than_the_bare_key(self):
        by_key = {s.result_key: s for s in SECTION_SOURCES}
        src = by_key["pricing_benchmark"]
        self.assertEqual(src.path, ("pricing", "benchmark"),
                         "the entry must record where the data actually lives")
        self.assertTrue(_present({"pricing": {"benchmark": {"rows": [1]}}},
                                 "pricing_benchmark"))
        self.assertFalse(_present({"pricing": {}}, "pricing_benchmark"))
        self.assertFalse(_present({"pricing_benchmark": {"rows": [1]}}, "pricing_benchmark"),
                         "the bare top-level key is NOT where this data lives")

    def test_a_derived_section_needs_no_result_key(self):
        """`integrity` is computed by the renderer from the whole result. Present when
        there is a result; absent when there is not."""
        by_key = {s.result_key: s for s in SECTION_SOURCES}
        self.assertTrue(by_key["integrity"].derived)
        self.assertTrue(_present({"profile": {"summary": "x"}}, "integrity"))
        self.assertFalse(_present({}, "integrity"))

    def test_an_ordinary_section_still_uses_its_own_key(self):
        """The default must not have moved for the other twenty."""
        self.assertTrue(_present({"market_sizing": {"tam": {"mid": 1}}}, "market_sizing"))
        self.assertFalse(_present({"market_sizing": {}}, "market_sizing"))
        self.assertFalse(_present({"market_sizing": {"error": "boom"}}, "market_sizing"),
                         "an errored section does not render")


if __name__ == "__main__":
    unittest.main()
