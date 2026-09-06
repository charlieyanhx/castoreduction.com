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

from core.section import (FAILED, FLAGGED, NOT_APPLICABLE, OK, SKIPPED, Section,
                          assemble, plan, summarise)


def _s(key, produce=None, consumes=(), optional=(), invariants=(), inapplicable=None):
    return Section(key=key, consumes=tuple(consumes), optional=tuple(optional),
                   invariants=tuple(invariants), inapplicable=inapplicable,
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


class TestAProducerCannotWriteUpstream(unittest.TestCase):
    """Bounding the READS was only half the isolation, and I shipped the other half broken.

    The first version handed out live references, so a producer could reach through its
    bounded context and rewrite a section that had ALREADY been produced and verified:

        def four_ps(ctx):
            ctx["sizing"]["som"] = 999_999      # silently corrupts a checked section

    Both sections reported `ok` and the wrong number shipped. That is strictly worse than
    the stale-read bug this module exists for -- run14 merely NARRATED an empty dict; this
    writes a false value into a section that already passed its invariants.

    Measured before choosing the fix: deep-copying the declared subset of a real 132 KB
    result costs 0.08-0.35 ms, and the whole result under 1 ms, against a run that takes
    minutes. Isolation at that price is not a trade.
    """

    def test_mutating_the_context_does_not_reach_the_result(self):
        def sneaky(ctx):
            ctx["sizing"]["som"] = 999_999
            return {"narrative": "fine"}

        result = {}
        assemble([_s("sizing", produce=lambda c: {"som": 120}),
                  _s("four_ps", consumes=("sizing",), produce=sneaky)], result)
        self.assertEqual(result["sizing"]["som"], 120, "upstream section was corrupted")

    def test_the_attempt_is_reported_rather_than_silently_discarded(self):
        """A no-op that looks like working code is its own trap. The author gets told."""
        def sneaky(ctx):
            ctx["sizing"]["som"] = 1
            return {"ok": True}

        res = assemble([_s("sizing", produce=lambda c: {"som": 120}),
                        _s("four_ps", consumes=("sizing",), produce=sneaky)], {})
        four_ps = next(r for r in res if r.key == "four_ps")
        self.assertEqual(four_ps.status, FLAGGED)
        self.assertIn("context_is_read_only", four_ps.findings[0])
        self.assertIn("sizing", four_ps.findings[0], "the finding names what it tried to write")

    def test_a_well_behaved_producer_is_not_flagged(self):
        """Reading the context must stay free; only writing to it is the offence."""
        res = assemble([_s("sizing", produce=lambda c: {"som": 120}),
                        _s("four_ps", consumes=("sizing",),
                           produce=lambda ctx: {"n": ctx["sizing"]["som"]})], {})
        self.assertTrue(all(r.status == OK for r in res), [r.as_dict() for r in res])


class TestAnOptionalInputDoesNotGateTheSection(unittest.TestCase):
    """The tier the first real migration forced into existence.

    viability reads seven result keys and deliberately treats two as absences it can
    narrate: "None reaches the prompt as 'not measured'", written after a Reddit outage
    became "zero target audience confidence" and docked the score. With only `consumes`,
    an honest declaration of those seven would have SKIPPED viability on 16 of the 19
    corpus reports that ship it -- so the choice was a declaration that destroyed the
    section or one that lied about what it reads. Neither is a frame.
    """

    def test_a_missing_optional_input_still_produces_the_section(self):
        [r] = assemble([_s("viability", consumes=("four_ps",), optional=("audience",))],
                       {"four_ps": {"p": 1}})
        self.assertEqual(r.status, OK, "an enrichment going missing skipped the section")

    def test_a_missing_required_input_still_skips(self):
        """The gate must keep working, or the tier is just a hole in it."""
        [r] = assemble([_s("viability", consumes=("four_ps",), optional=("audience",))],
                       {"audience": {"a": 1}})
        self.assertEqual(r.status, SKIPPED)
        self.assertIn("four_ps", r.reason)

    def test_an_optional_input_reaches_the_producer_when_present(self):
        """Optional means "may be absent", not "withheld"."""
        seen = {}
        sec = _s("v", consumes=("four_ps",), optional=("audience",),
                 produce=lambda ctx: seen.update(ctx) or {"ok": 1})
        assemble([sec], {"four_ps": 1, "audience": 2, "secret": 3})
        self.assertEqual(sorted(seen), ["audience", "four_ps"])

    def test_an_optional_input_still_orders_the_assembly(self):
        """Optional does not mean unordered. If the enrichment IS going to be produced,
        the consumer must run after it -- otherwise it reports "not measured" about a
        section that was about to exist, which is run14's bug wearing an honest label."""
        # `audience` must be BEHIND something, or it lands early on its own and the test
        # passes whether or not the optional edge exists. The first version of this test
        # did exactly that: deleting the edge from plan() left it green.
        secs = [_s("viability", consumes=("four_ps",), optional=("audience",)),
                _s("four_ps"), _s("audience", consumes=("evidence",)), _s("evidence")]
        order = [x.key for x in plan(secs)]
        self.assertLess(order.index("audience"), order.index("viability"))


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


class TestASectionThatNeverAppliedIsNotAFailure(unittest.TestCase):
    """The distinction the second migration surfaced, one report before it shipped wrong.

    Step 5 builds a B2B customer universe and returns immediately for a direct-to-consumer
    venture -- correctly. MEASURED: 14 of 19 corpus reports have no customer_universe and
    ALL 14 are non-B2B. Declaring the dependency turned 14 silent absences into 14 that
    said "declared input absent or empty: customer_universe" -- true, and read by a DTC
    founder as fourteen things that broke. That is the same conflation the gate layer
    already refuses, where wrong-shape and data-missing were summed and withheld a good
    report.
    """

    def test_an_inapplicable_section_is_not_skipped_and_not_produced(self):
        [r] = assemble([_s("segments", inapplicable=lambda: "this venture is not B2B")], {})
        self.assertEqual(r.status, NOT_APPLICABLE)
        self.assertIn("not B2B", r.reason)

    def test_applicability_is_asked_before_missing_inputs(self):
        """A section that was never going to exist has no missing input worth naming.
        Reporting one answers a question nobody asked, in words that read like a fault."""
        [r] = assemble([_s("segments", consumes=("customer_universe",),
                           inapplicable=lambda: "not a B2B venture")], {})
        self.assertEqual(r.status, NOT_APPLICABLE)
        self.assertNotIn("customer_universe", r.reason)

    def test_an_applicable_section_is_untouched_by_the_check(self):
        [r] = assemble([_s("segments", inapplicable=lambda: None)], {})
        self.assertEqual(r.status, OK)

    def test_a_broken_applicability_rule_lets_the_section_run(self):
        """A predicate that raises must not decide the report. The safe reading of a broken
        rule is that the section DOES apply, so the producer runs and any real problem
        surfaces as itself instead of as a section quietly declared irrelevant."""
        def boom():
            raise RuntimeError("rule bug")

        [r] = assemble([_s("segments", inapplicable=boom)], {})
        self.assertEqual(r.status, OK)

    def test_the_producer_never_runs_for_an_inapplicable_section(self):
        ran = []
        assemble([_s("segments", produce=lambda ctx: ran.append(1) or {},
                     inapplicable=lambda: "not B2B")], {})
        self.assertEqual(ran, [], "an inapplicable section still paid for its producer")

    def test_the_summary_counts_it_separately_from_everything_else(self):
        s = summarise(assemble([_s("a"), _s("b", inapplicable=lambda: "not B2B"),
                                _s("c", consumes=("nope",))], {}))
        self.assertEqual((s[OK], s[NOT_APPLICABLE], s[SKIPPED]), (1, 1, 1))


class TestTheSummaryIsWhatAReaderSees(unittest.TestCase):
    def test_it_counts_every_status_and_keeps_the_detail(self):
        """'18 of 22 verified, 2 flagged, 2 not checked' says something a boolean cannot."""
        secs = [_s("a"), _s("b", consumes=("nope",)),
                _s("c", invariants=(("x", lambda p: "bad"),))]
        s = summarise(assemble(secs, {}))
        self.assertEqual((s[OK], s[FLAGGED], s[SKIPPED], s["total"]), (1, 1, 1, 3))
        self.assertEqual(len(s["sections"]), 3)


class TestTheRegistryRefusesToShadow(unittest.TestCase):
    """`register` raises on a duplicate name, and nothing tested that until now.

    The three decorators used to do `REGISTRY[name] = Meta(...)`, so two capabilities
    sharing a name silently shadowed each other and the winner depended on import order.
    register() closes that -- but a guard with no test is a guard that can regress in
    silence, which is the same failure it exists to prevent one level up.
    """

    def test_a_duplicate_name_is_refused(self):
        from core import DuplicateRegistration, Registry

        reg = Registry("widget")
        reg.register("a", object())
        with self.assertRaises(DuplicateRegistration) as cm:
            reg.register("a", object())
        self.assertIn("widget", str(cm.exception), "the message names the kind")
        self.assertIn("import order", str(cm.exception), "and why it matters")

    def test_assignment_stays_permissive_for_fixtures(self):
        """__setitem__ is the seam tests use to install and remove a fixture. Locking it
        down would make the registry untestable, which is why register() is the strict
        door and assignment is not."""
        from core import Registry

        reg = Registry("widget")
        reg.register("a", 1)
        reg["a"] = 2                      # deliberate override
        self.assertEqual(reg["a"], 2)
        del reg["a"]
        self.assertEqual(len(reg), 0)


class TestTheAssemblerIsFrameCode(unittest.TestCase):
    def test_it_carries_no_knowledge_of_any_domain(self):
        """core/ is the floor: standard library only. If this module knew what a TAM was
        it would not be reusable, which is the entire point of it."""
        import ast
        import pathlib

        import sys

        tree = ast.parse(pathlib.Path("core/section.py").read_text())
        imported = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                imported.append(n.module or "")
        # The RULE is "standard library only", so ask Python what that is rather than
        # keeping a hand-written allowlist. The list version failed the moment `copy` was
        # added for write isolation -- a correct import rejected by a stale enumeration,
        # which is a test that costs edits without catching anything.
        outside = [m for m in imported
                   if m.split(".")[0] not in sys.stdlib_module_names]
        self.assertEqual(outside, [], "core/section.py reached outside the standard library")


if __name__ == "__main__":
    unittest.main()
