"""The product has two front doors. Every deterministic reader must work at both (#98).

A report can be commissioned two ways:

  POST /plan          a brief the operator WROTE — prose
  the chat intake     a brief the product ASSEMBLED from extracted fields

Both end up as one string, and five deterministic readers pull the load-bearing facts back
out of it: extract_location, extract_unit_price, extract_device_price,
extract_stated_price, classify_market_scale. Nothing has ever checked that they behave the
same on the two shapes.

WHAT THAT COST, measured (fixed in bcdc96f). The assembler wrote the site as a label —
"... Geography: Mission District, San Francisco, CA." — and extract_location needs a
prepositional phrase, so it returned None on every brief the chat has ever produced.
Downstream that is not a warning:

    size_by_scale        `if not location: return None`   no trade-area sizing at all
    geo_competitor_opps  `if not location: return []`     no local competitor census

so a venture the classifier CALLED hyperlocal shipped with neither, labelled with a method
that never ran. D52 forbids exactly that and returns ok=False on the shape — it never fired
because every stored artifact in this repo comes from tools.run_live, which passes prose
straight through. 61 invariants, all correct, all blind to half the product.

THE GENERAL RULE, which is what this file enforces: a fact stated to one entry point and a
fact stated to the other must reach the pipeline identically. Not "the assembler emits the
right string" — that pins today's phrasing and would have to be rewritten every time the
copy changes. The property is AGREEMENT: whatever the two shapes look like, the readers
must extract the same facts from them.

The price is deliberately included even though it already worked. Half the brief parsing
correctly is exactly why the location bug survived — nothing looked broken — so the passing
half belongs in the same guard as the failing one.
"""
from __future__ import annotations

import unittest

# One venture, stated both ways. The facts are identical; only the register differs.
_FACTS = {
    "product": "Specialty coffee shop serving espresso and pour-over",
    "target_customer": "Local residents and remote workers",
    "business_model": "Brick-and-mortar retail",
    "geography": "Mission District, San Francisco, CA",
    "pricing": "$5.50 per drink",
    "stage": "idea",
}

_PROSE = ("An independent specialty coffee shop opening in the Mission District, San "
          "Francisco, CA. It serves espresso and pour-over to local residents and remote "
          "workers. Brick-and-mortar retail at $5.50 per drink.")


def _assembled() -> str:
    from intake import _synthesize_from_extracted
    return _synthesize_from_extracted(dict(_FACTS))


class TestTheTwoBriefsAgree(unittest.TestCase):
    """Same facts in, same facts out — whichever door they came through."""

    def test_both_yield_a_location(self):
        import plan
        for label, brief in (("assembled", _assembled()), ("prose", _PROSE)):
            with self.subTest(shape=label):
                got = plan.extract_location(brief)
                self.assertIsNotNone(
                    got, f"the {label} brief hides the site from the pipeline: {brief}")
                self.assertIn("Mission", got)

    def test_both_yield_the_same_unit_price(self):
        import plan
        self.assertEqual(plan.extract_unit_price(_assembled()),
                         plan.extract_unit_price(_PROSE))

    def test_both_yield_the_same_location_count(self):
        """A count read from one door and not the other multiplies the TAM by 5 through one
        entry point and not the other. Both shapes are single-site here, so both must say
        None — and the chain shapes below must agree with each other too."""
        import plan
        self.assertIsNone(plan.extract_location_count(_assembled()))
        self.assertIsNone(plan.extract_location_count(_PROSE))

    def test_a_chain_reads_the_same_through_both_doors(self):
        from intake import _synthesize_from_extracted
        import plan
        facts = dict(_FACTS, product="A five-store specialty coffee chain")
        prose = ("A five-store specialty coffee chain in the Mission District, San "
                 "Francisco, CA, at $5.50 per drink.")
        self.assertEqual(plan.extract_location_count(_synthesize_from_extracted(facts)),
                         plan.extract_location_count(prose),
                         "the assembled brief and the prose brief disagree on how many "
                         "premises this venture operates — one of them sizes 5 trade "
                         "areas and the other sizes 1")
        self.assertEqual(plan.extract_location_count(prose), 5)

    def test_both_route_to_the_same_sizing_skill(self):
        """The decision that picks which market model runs at all."""
        from skills.sizing.classify import classify_market_scale
        a = classify_market_scale(_assembled(), "US").payload
        p = classify_market_scale(_PROSE, "US").payload
        self.assertEqual(a["sizing_skill"], p["sizing_skill"])
        self.assertEqual(a["scale"], p["scale"])

    def test_neither_invents_a_monthly_price(self):
        """extract_stated_price is monthly-only by construction. A per-drink venture must
        read as None from BOTH shapes — a phantom /mo price would put a transactional
        venture on subscription economics."""
        import plan
        self.assertIsNone(plan.extract_stated_price(_assembled()))
        self.assertIsNone(plan.extract_stated_price(_PROSE))

    def test_neither_invents_a_device_price(self):
        import plan
        self.assertEqual(plan.extract_device_price(_assembled()),
                         plan.extract_device_price(_PROSE))


class TestTheAgreementIsNotVacuous(unittest.TestCase):
    """A conformance test that passes because both sides return None proves nothing."""

    def test_the_prose_brief_really_does_carry_the_facts(self):
        import plan
        self.assertIsNotNone(plan.extract_location(_PROSE))
        self.assertEqual(plan.extract_unit_price(_PROSE), 5.50)

    def test_the_guard_would_have_caught_the_shipped_bug(self):
        """The exact string the assembler used to produce. If a future change reintroduces
        a label form, this is the failure it produces."""
        import plan
        old_shape = (f"{_FACTS['product']} Target customer: {_FACTS['target_customer']}. "
                     f"Business model: {_FACTS['business_model']}. "
                     f"Geography: {_FACTS['geography']}. Pricing: {_FACTS['pricing']}.")
        self.assertIsNone(plan.extract_location(old_shape),
                          "the label shape now parses — good, but this test's premise is "
                          "stale and its docstring needs rewriting")
        self.assertIsNotNone(plan.extract_location(_assembled()),
                             "the assembler regressed to a shape the pipeline cannot read")


class TestEveryDescriptionReaderIsCovered(unittest.TestCase):
    """The guard's own coverage. A reader added later, and not added here, is a fact that
    can silently disagree between the two doors — which is how this whole class of bug
    stays alive."""

    #: Everything called with the raw description in run_plan, by grep of `(description)`.
    _READERS = ("extract_location", "extract_unit_price", "extract_device_price",
                "extract_stated_price", "extract_location_count")

    def test_the_readers_named_here_still_exist(self):
        import plan
        for name in self._READERS:
            self.assertTrue(callable(getattr(plan, name, None)), name)

    def test_no_new_description_reader_has_appeared_unguarded(self):
        """Greps run_plan's own source for `extract_*(description)` calls and fails if one
        is not in _READERS. Cheap, and it is the only thing that keeps this file honest as
        the pipeline grows."""
        import re
        from pathlib import Path
        src = Path("plan.py").read_text()
        called = set(re.findall(r"\b(extract_[a-z_]+)\(description\)", src))
        missing = sorted(called - set(self._READERS))
        self.assertEqual(missing, [],
                         f"these read the description and are not covered by the "
                         f"two-door agreement test: {missing}")


if __name__ == "__main__":
    unittest.main()
