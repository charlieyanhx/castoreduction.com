"""Assembly is ordered by declaration, bounded by declaration, and verified as it goes.

THE DEFECT THIS EXISTS TO MAKE IMPOSSIBLE. Sizing and the 4Ps once ran concurrently as
"the pipeline's most expensive pair". `_four_ps_task` reads result["market_sizing"] at
execution time, that key was EMPTY mid-join, and the hyperlocal override landed after the
join entirely. run14 proved it: ms_competitors=null, ms_som_mid=null, so the volume
ladder's SOM rung never once reached a prompt. The report narrated numbers that did not
exist yet.

It was fixed by hand-sequencing two calls and writing a paragraph explaining why. That
works until the fifteenth step, or until someone reorders for a good reason and does not
know about run14. Declaring `consumes` makes the ordering derivable and the stale read
loud, which is a different class of guarantee from a comment.

BOUNDED CONTEXT IS ALSO THE LLM DESIGN. A producer sees only what it declared, in sorted
key order. Smaller and more relevant beats larger and complete for prompt quality, and
sorted keys keep the prefix byte-stable so the KV cache actually hits — the same property
harness/agent.py and context/reminders.py already protect deliberately.
"""
from __future__ import annotations

import unittest

from core.section import (FAILED, FLAGGED, OK, SKIPPED, Section, assemble, plan,
                          summarise)


def _s(key, produce=None, consumes=(), invariants=()):
    return Section(key=key, consumes=tuple(consumes), invariants=tuple(invariants),
                   produce=produce or (lambda ctx: {"v": 1}))


class TestOrderIsDerivedNotDeclared(unittest.TestCase):
    def test_a_consumer_runs_after_what_it_consumes(self):
        """The run14 guarantee, stated as an ordering rather than a comment."""
        secs = [_s("four_ps", consumes=("sizing",)), _s("sizing"), _s("roster")]
        order = [s.key for s in plan(secs)]
        self.assertLess(order.index("sizing"), order.index("four_ps"))

    def test_declaration_order_does_not_decide_report_order(self):
        """four_ps is declared FIRST and must still assemble last."""
        secs = [_s("four_ps", consumes=("sizing",)), _s("sizing", consumes=("roster",)),
                _s("roster")]
        self.assertEqual([s.key for s in plan(secs)], ["roster", "sizing", "four_ps"])

    def test_the_order_is_stable_across_calls(self):
        """A prompt cache keyed on a prefix cannot tolerate an order that wobbles."""
        secs = [_s("a"), _s("b", consumes=("a",)), _s("c", consumes=("a",)), _s("d")]
        first = [s.key for s in plan(secs)]
        for _ in range(5):
            self.assertEqual([s.key for s in plan(secs)], first)

    def test_a_cycle_raises_rather_than_picking_a_winner(self):
        """Two sections that each need the other cannot both be right. Choosing by sort
        order would hide a declaration bug behind a plausible report."""
        secs = [_s("a", consumes=("b",)), _s("b", consumes=("a",))]
        with self.assertRaises(ValueError) as cm:
            plan(secs)
        self.assertIn("circular", str(cm.exception))


class TestAProducerSeesOnlyWhatItDeclared(unittest.TestCase):
    def test_context_is_exactly_the_declared_keys(self):
        seen = {}
        sec = _s("x", consumes=("a", "b"), produce=lambda ctx: seen.update(ctx) or {"ok": 1})
        assemble([sec], {"a": 1, "b": 2, "c": 3, "secret": 4})
        self.assertEqual(sorted(seen), ["a", "b"],
                         "an undeclared key reached the producer")

    def test_context_keys_are_sorted_for_a_stable_prompt_prefix(self):
        sec = _s("x", consumes=("zebra", "alpha", "middle"))
        ctx = sec.context({"zebra": 1, "alpha": 2, "middle": 3})
        self.assertEqual(list(ctx), ["alpha", "middle", "zebra"])


class TestASectionIsVerifiedAsItLands(unittest.TestCase):
    def test_a_failing_invariant_flags_the_section_and_names_it(self):
        sec = _s("sizing", produce=lambda ctx: {"som": -5},
                 invariants=(("som_positive",
                              lambda p: None if p["som"] > 0 else f"SOM is {p['som']}"),))
        [r] = assemble([sec], {})
        self.assertEqual(r.status, FLAGGED)
        self.assertIn("som_positive", r.findings[0])
        self.assertIn("-5", r.findings[0], "the finding must carry the actual value")

    def test_a_passing_invariant_leaves_the_section_ok(self):
        sec = _s("sizing", produce=lambda ctx: {"som": 5},
                 invariants=(("som_positive", lambda p: None if p["som"] > 0 else "bad"),))
        self.assertEqual(assemble([sec], {})[0].status, OK)

    def test_a_detector_that_raises_becomes_a_finding_not_a_crash(self):
        """Per-detector isolation, for the reason the corpus sweep already documents: the
        apparatus that judges honesty has to degrade one cell at a time."""
        def boom(_payload):
            raise RuntimeError("detector bug")

        [r] = assemble([_s("x", invariants=(("boom", boom),))], {})
        self.assertEqual(r.status, FLAGGED)
        self.assertIn("detector raised", r.findings[0])


class TestOneBadSectionDoesNotKillTheReport(unittest.TestCase):
    def test_a_raising_producer_is_recorded_and_assembly_continues(self):
        def explode(_ctx):
            raise ValueError("upstream API died")

        results = assemble([_s("bad", produce=explode), _s("good")], {})
        by = {r.key: r for r in results}
        self.assertEqual(by["bad"].status, FAILED)
        self.assertIn("upstream API died", by["bad"].reason)
        self.assertEqual(by["good"].status, OK, "one failure must not stop the rest")

    def test_a_missing_declared_input_skips_with_a_named_reason(self):
        """The absence-explains-itself rule, at section granularity. A reader is told
        which input never arrived, not shown a gap."""
        [r] = assemble([_s("viability", consumes=("economics",))], {})
        self.assertEqual(r.status, SKIPPED)
        self.assertIn("economics", r.reason)

    def test_an_input_that_exists_but_is_empty_counts_as_missing(self):
        """A section cannot narrate from a key that is present and holds nothing. This is
        the same distinction Evidence draws between skeleton and error."""
        [r] = assemble([_s("v", consumes=("economics",))], {"economics": {}})
        self.assertEqual(r.status, SKIPPED)


class TestTheSummaryIsWhatAReaderSees(unittest.TestCase):
    def test_it_counts_every_status_and_keeps_the_detail(self):
        """'18 of 22 verified, 2 flagged, 2 not checked' says something a boolean cannot."""
        secs = [_s("a"), _s("b", consumes=("nope",)),
                _s("c", invariants=(("x", lambda p: "bad"),))]
        s = summarise(assemble(secs, {}))
        self.assertEqual((s[OK], s[FLAGGED], s[SKIPPED], s["total"]), (1, 1, 1, 3))
        self.assertEqual(len(s["sections"]), 3)


class TestTheAssemblerIsFrameCode(unittest.TestCase):
    def test_it_carries_no_knowledge_of_any_domain(self):
        """core/ is the floor: standard library only. If this module knew what a TAM was
        it would not be reusable, which is the entire point of it."""
        import ast
        import pathlib

        tree = ast.parse(pathlib.Path("core/section.py").read_text())
        imported = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                imported.append(n.module or "")
        self.assertEqual([m for m in imported if m not in
                          ("dataclasses", "typing", "__future__")], [])


if __name__ == "__main__":
    unittest.main()
