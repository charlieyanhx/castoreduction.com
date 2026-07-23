"""
Rank 6 of the R4 fix order: differentiators fabricated before evidence (16/16).

Mechanism, from the panel's verified clusters:

  * Step 3d runs right after clustering — BEFORE competitor pricing, taste decodes,
    review themes, or any scrape exists. Its entire competitor input is name +
    120-char blobs; on geo-sourced ventures that is literally "Ginger Lily: ".
  * The prompt MANDATES production: "You MUST return at least 1 differentiator...
    your job is to FIND it, not validate it... never zero."
  * differentiation_strength is a pure function of entry count, which the mandate
    pins at 8-10 — so it is "high" on 16/16 ventures, and viability is told to
    ANCHOR its differentiation score to it.

A mandate to find differences plus no evidence to find them in equals fabrication —
reports asserted specific competitor pricing and booking behaviour as unhedged fact
for products that do not exist yet, next to 78-85 viability scores for "highly
defensible" positioning.

The inversion: evidence in, mandate out.
  * step 3d moves AFTER the evidence phase and receives what it produced;
  * every entry must cite what it stands on (evidence_ref); returning [] on a
    dimension the evidence says nothing about is the CORRECT answer;
  * entries are deduped by token-Jaccard before counting, and strength derives from
    DISTINCT, EVIDENCE-BACKED entries — not from a count the prompt structure pins.
"""
from __future__ import annotations

import glob
import json
import unittest
from unittest.mock import patch

from differentiators import (_dedupe_by_jaccard, _strength_from,
                             extract_differentiators)

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


class TestDedupe(unittest.TestCase):
    def test_near_identical_features_collapse(self):
        entries = [
            {"feature": "certified organic single-origin beans", "dimension": "product"},
            {"feature": "single-origin certified organic beans", "dimension": "brand"},
            {"feature": "on-site nitrogen roasting", "dimension": "product"},
        ]
        out = _dedupe_by_jaccard(entries)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["feature"], "certified organic single-origin beans")

    def test_distinct_features_survive(self):
        entries = [{"feature": "24h delivery window", "dimension": "channel"},
                   {"feature": "clinical-grade sensors", "dimension": "product"}]
        self.assertEqual(len(_dedupe_by_jaccard(entries)), 2)

    def test_empty_and_missing_features_are_dropped(self):
        self.assertEqual(_dedupe_by_jaccard([{"feature": ""}, {}]), [])


class TestStrengthIsEvidenceBacked(unittest.TestCase):
    def test_ten_unevidenced_entries_are_not_high(self):
        """The 16/16 shape: a pile of entries, none standing on anything."""
        entries = [{"feature": f"claim {i}", "dimension": d, "why_unique": "x"}
                   for i, d in enumerate(("product", "brand", "channel", "price", "cx") * 2)]
        strength, reasoning = _strength_from(entries)
        self.assertNotEqual(strength, "high")
        self.assertIn("0 evidence-backed", reasoning)

    def test_four_evidenced_entries_across_three_dimensions_are_high(self):
        entries = [
            {"feature": "a", "dimension": "product", "evidence_ref": "pricing: Souvla $40/bowl"},
            {"feature": "b", "dimension": "brand", "evidence_ref": "reviews: 'no organic option'"},
            {"feature": "c", "dimension": "channel", "evidence_ref": "channels: none ship nationally"},
            {"feature": "d", "dimension": "price", "evidence_ref": "pricing: median $12"},
        ]
        strength, _ = _strength_from(entries)
        self.assertEqual(strength, "high")

    def test_one_evidenced_entry_is_moderate_low(self):
        entries = [{"feature": "a", "dimension": "product", "evidence_ref": "reviews: x"},
                   {"feature": "b", "dimension": "brand"}]
        strength, _ = _strength_from(entries)
        self.assertEqual(strength, "moderate-low")

    def test_zero_entries_is_low(self):
        self.assertEqual(_strength_from([])[0], "low")


class TestPromptInversion(unittest.TestCase):
    def _prompts(self, evidence=None):
        seen = []

        def fake(system, user, max_tokens=0, **kw):
            seen.append(user)
            return {"differentiators": []}

        with patch("differentiators.call_json", side_effect=fake):
            extract_differentiators(
                profile={"name": "X", "category": "cafe"},
                our_features=["organic beans"],
                clustering={}, competitors=[{"brand": "Souvla", "thesis": "fast casual"}],
                evidence=evidence)
        return seen

    def test_the_mandate_is_gone(self):
        for p in self._prompts():
            self.assertNotIn("MUST return at least 1", p)
            self.assertNotIn("never zero", p)

    def test_empty_is_declared_a_correct_answer(self):
        self.assertTrue(any("empty list" in p and "correct" in p.lower()
                            for p in self._prompts()))

    def test_evidence_reaches_the_dimension_prompts(self):
        ev = {"competitor_pricing": {"Souvla": {"price": 40.0, "unit": "bowl"}},
              "review_themes": ["no organic option nearby"]}
        prompts = self._prompts(evidence=ev)
        self.assertTrue(all("Souvla" in p and "40" in p for p in prompts))
        self.assertTrue(any("no organic option" in p for p in prompts))

    def test_no_evidence_says_so_instead_of_pretending(self):
        self.assertTrue(any("no evidence" in p.lower() for p in self._prompts()))


class TestUnevidencedPriceClaimsAreStripped(unittest.TestCase):
    def test_a_dollar_claim_without_pricing_evidence_is_dropped(self):
        """The 16/16 gate case at the source: pricing prose invented when no
        competitor price was ever scraped."""
        def fake(system, user, max_tokens=0, **kw):
            return {"differentiators": [
                {"feature": "66% cheaper than Souvla's $40 bowl", "why_unique": "x"}]}

        with patch("differentiators.call_json", side_effect=fake):
            out = extract_differentiators(
                profile={"name": "X", "category": "cafe"}, our_features=[],
                clustering={}, competitors=[], evidence=None)
        feats = [d["feature"] for d in out.get("differentiators", [])]
        self.assertNotIn("66% cheaper than Souvla's $40 bowl", feats)

    def test_the_same_claim_with_pricing_evidence_survives(self):
        def fake(system, user, max_tokens=0, **kw):
            return {"differentiators": [
                {"feature": "66% cheaper than Souvla's $40 bowl", "why_unique": "x",
                 "evidence_ref": "pricing: Souvla $40/bowl"}]}

        ev = {"competitor_pricing": {"Souvla": {"price": 40.0, "unit": "bowl"}}}
        with patch("differentiators.call_json", side_effect=fake):
            out = extract_differentiators(
                profile={"name": "X", "category": "cafe"}, our_features=[],
                clustering={}, competitors=[], evidence=ev)
        self.assertEqual(len(out.get("differentiators", [])), 1)


class TestPipelineSequencing(unittest.TestCase):
    def test_step_3d_runs_after_the_evidence_join(self):
        import inspect
        import plan
        src = inspect.getsource(plan.run_plan)
        self.assertLess(src.index('result["competitor_pricing"]'),
                        src.index("extract_differentiators"),
                        "step 3d still runs before competitor pricing exists")

    def test_the_evidence_is_passed(self):
        import inspect
        import plan
        src = inspect.getsource(plan.run_plan)
        # Anchor on the CALL, not the import — the evidence dict is built between
        # them, and a window measured from the import missed the kwarg entirely.
        call_at = src.index("diffs = extract_differentiators(")
        self.assertIn("evidence=", src[call_at:call_at + 400])

    def test_customer_universe_no_longer_waits_on_differentiators(self):
        """Pre-evidence fabricated diffs were its search hints — garbage hints. It
        now runs without them rather than moving the whole universe later."""
        import inspect
        import plan
        src = inspect.getsource(plan.run_plan)
        universe_at = src.index("build_customer_universe")
        self.assertNotIn('result.get("differentiators")',
                         src[universe_at:universe_at + 400])


class TestGateD30(unittest.TestCase):
    def _r(self, feats, pricing=None, strength=None):
        diffs = {"differentiators": feats}
        if strength:
            diffs["differentiation_strength"] = strength
        r = {"differentiators": diffs}
        if pricing is not None:
            r["competitor_pricing"] = pricing
        return r

    def test_price_language_without_pricing_evidence_fails(self):
        import gates
        r = self._r([{"feature": "priced 66% below the $40 category norm",
                      "dimension": "price"}])
        f = gates.d30_differentiators_evidence_backed(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("price", f.detail.lower())

    def test_price_language_with_pricing_evidence_passes(self):
        import gates
        r = self._r([{"feature": "priced 66% below the $40 category norm",
                      "dimension": "price", "evidence_ref": "pricing: Souvla $40"}],
                    pricing={"competitors": [{"brand": "Souvla", "price": 40}]})
        self.assertIs(gates.d30_differentiators_evidence_backed(r, None).ok, True)

    def test_near_duplicate_entries_fail(self):
        import gates
        r = self._r([{"feature": "certified organic single-origin beans"},
                     {"feature": "single-origin certified organic beans"}])
        f = gates.d30_differentiators_evidence_backed(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("duplicate", f.detail.lower())

    def test_high_strength_with_zero_evidence_refs_fails(self):
        import gates
        r = self._r([{"feature": "a"}, {"feature": "distinct thing b"},
                     {"feature": "another c"}, {"feature": "fourth d"}],
                    strength="high")
        f = gates.d30_differentiators_evidence_backed(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("evidence", f.detail.lower())

    def test_na_without_differentiators(self):
        import gates
        self.assertIsNone(gates.d30_differentiators_evidence_backed({}, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D30", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_the_stored_corpus_fails_broadly(self):
        """Pins the premise: strength 'high' with zero evidence_refs everywhere,
        plus invented price prose. The panel counted 16/16 and 11/16."""
        import gates
        n_fail = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            if gates.d30_differentiators_evidence_backed(r, None).ok is False:
                n_fail += 1
        self.assertGreaterEqual(n_fail, 12)


if __name__ == "__main__":
    unittest.main()
