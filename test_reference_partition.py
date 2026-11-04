"""
Rank 10 of the R4 fix order: self-flagged junk relabeled, never excluded (14/16).

`_apply_relevance_to_ranking` (B4/D19) demoted an off-category or non-competitor entry
to `relevance: "reference"` and sorted it last — but LEFT it in `ranked_opportunities`,
so it still inflated `competitor_density`, occupied a dot on the PCA map, and could
name a positioning pole. The misattribution verdict ("this is a cryptography firm, not
a superconductor company") travelled in the free-text `thesis`, never a flag. On the
corpus, 6 rosters carried 19 `reference`-relevance entries counted as competitors.

The fix partitions the roster: `ranked_opportunities` keeps only real competitors
(relevance direct/adjacent, on-category, is_competitor != false); everything else moves
to `reference_cases`. Because rank 9 derives density and the map from
`ranked_opportunities`, both now count only real competitors — no separate surface to
keep in sync. A structured `is_competitor` flag replaces the free-text verdict.
"""
from __future__ import annotations

import glob
import json
import unittest

from discover import _partition_reference_cases

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


class TestPartition(unittest.TestCase):
    def test_references_and_off_category_move_out(self):
        entries = [
            {"brand": "A", "relevance": "direct"},
            {"brand": "B", "relevance": "adjacent"},
            {"brand": "C", "relevance": "reference"},
            {"brand": "D", "relevance": "direct", "off_category": True},
            {"brand": "E", "relevance": "direct", "is_competitor": False},
        ]
        comps, refs = _partition_reference_cases(entries)
        self.assertEqual([c["brand"] for c in comps], ["A", "B"])
        self.assertEqual({r["brand"] for r in refs}, {"C", "D", "E"})

    def test_all_competitors_partition_cleanly(self):
        entries = [{"brand": "A", "relevance": "direct"},
                   {"brand": "B", "relevance": "adjacent"}]
        comps, refs = _partition_reference_cases(entries)
        self.assertEqual(len(comps), 2)
        self.assertEqual(refs, [])

    def test_missing_relevance_is_kept_as_competitor(self):
        # No verdict available — do not exclude what wasn't classified.
        entries = [{"brand": "A"}, {"brand": "B", "relevance": "direct"}]
        comps, refs = _partition_reference_cases(entries)
        self.assertEqual(len(comps), 2)
        self.assertEqual(refs, [])

    def test_is_competitor_false_is_the_structured_flag(self):
        # A strong-signal wrong-entity candidate: relevance says direct, but the
        # structured misattribution flag overrides the free-text.
        entries = [{"brand": "CryptoCo", "relevance": "direct", "is_competitor": False,
                    "thesis": "actually a cryptography firm, not superconductors"}]
        comps, refs = _partition_reference_cases(entries)
        self.assertEqual(comps, [])
        self.assertEqual(refs[0]["brand"], "CryptoCo")


class TestGateD34(unittest.TestCase):
    def _r(self, roster):
        return {"discover": {"synthesis": {"ranked_opportunities": roster}}}

    def test_a_reference_in_the_competitor_roster_fails(self):
        import gates
        r = self._r([{"brand": "A", "relevance": "direct"},
                     {"brand": "C", "relevance": "reference"}])
        f = gates.d34_roster_excludes_references(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("non-competitor", f.detail.lower())

    def test_an_off_category_in_the_roster_fails(self):
        import gates
        r = self._r([{"brand": "A", "relevance": "direct"},
                     {"brand": "D", "relevance": "direct", "off_category": True}])
        self.assertIs(gates.d34_roster_excludes_references(r, None).ok, False)

    def test_a_clean_competitor_roster_passes(self):
        import gates
        r = self._r([{"brand": "A", "relevance": "direct"},
                     {"brand": "B", "relevance": "adjacent"}])
        self.assertIs(gates.d34_roster_excludes_references(r, None).ok, True)

    def test_na_without_roster(self):
        import gates
        self.assertIsNone(gates.d34_roster_excludes_references({}, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D34", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_stored_rosters_carry_references(self):
        """6 stored rosters list reference-relevance entries as competitors."""
        import gates
        n_fail = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            if gates.d34_roster_excludes_references(r, None).ok is False:
                n_fail += 1
        self.assertGreaterEqual(n_fail, 5)


if __name__ == "__main__":
    unittest.main()
