"""Four places where a string tells the model something the pipeline cannot back.

Audit C11 + C12. Grouped because they are one failure mode: prose written for one venture
shape, emitted for all of them, ordering the sections to state figures nothing computes.

C11 — HYBRID'S RECURRING LEG IS PROMISED AND NEVER COMPUTED.
`four_ps.model_directive("hybrid")` tells every section:

    "The recurring leg is REAL and defined by the profile — show it as a clearly LABELED
     SECONDARY line (its retention / recurring revenue belong there ... never dropped)."

Grepping business_model.py, economics_step.py and financials.py for a recurring leg —
`recurring_leg`, `attach_rate`, `mrr` — returns nothing. `_PER_UNIT_KINDS` routes hybrid to
`retail_unit_economics`, a single-stream model, and `financials_step` collapses it to
"transactional". So any recurring figure a section states is necessarily invented, and the
prompt is what asked for it.

The audit offers two fixes and says take the cheap one first: compute the leg, or stop
promising it. Shipping a directive the spine cannot satisfy is the worse of the two. This
takes the second and follows the pattern the ad_supported branch already established —
name it an operator unknown, the way eCPM and fill-rate are named.

C12a — `fixed_cost_basis: "single-site rent + staff + utilities"`, unconditional
(business_model.py:382), on a consultancy and a DTC brand alike. `estimate_cost_structure`
ALREADY computes the right basis ("early-stage company overhead (team + infrastructure +
tooling)") and `economics_step` drops it: two producers, zero consumers.

C12b — `"project"` sits in `_MARKETPLACE_UNITS`, so `benchmark_validation_note` tells EVERY
services venture to "sample rival take-rates ... validate against comparable marketplaces".
An agency has no take-rate. This is the same model bleed the function's own docstring exists
to prevent, running in the other direction.

C12c — `four_ps._r_citation_discipline`'s third string segment is not f-prefixed, so the
literal characters `{unit}` ship in the citation rule of every 4Ps prompt of every run. Two
characters. The same example also reads "units/day" for a subscription, which is the #100
period defect surviving in a hardcoded string.
"""
from __future__ import annotations

import unittest


class TestTheHybridDirectiveDoesNotOrderAnInvention(unittest.TestCase):
    def _directive(self, kind="hybrid"):
        from four_ps import model_directive
        return model_directive(kind)

    def test_nothing_computes_a_recurring_leg(self):
        """The premise, checked rather than assumed — if a later change starts computing
        one, this test should fail and the directive should go back to promising it."""
        import business_model
        import financials
        from orchestrator.steps import economics_step
        import inspect
        blob = "".join(inspect.getsource(m) for m in
                       (business_model, financials, economics_step))
        for token in ("recurring_leg", "attach_rate"):
            self.assertNotIn(token, blob,
                             f"{token} exists now — the directive may promise it again")

    def test_the_recurring_leg_is_named_an_operator_unknown(self):
        d = self._directive().lower()
        self.assertTrue("operator" in d,
                        "the directive asks for a figure the spine never computes without "
                        "saying who must supply it")

    def test_it_no_longer_asserts_the_leg_is_real_and_defined(self):
        d = self._directive()
        self.assertNotIn("is REAL and defined by the profile", d)
        self.assertNotIn("never dropped", d)

    def test_it_still_refuses_the_collapse_into_a_subscription(self):
        """The defect this directive was written for — a hybrid rendered as pure SaaS —
        must stay fixed. Softening the promise is not dropping the guard."""
        d = self._directive().lower()
        self.assertIn("subscription", d)
        self.assertIn("one-time", d)

    def test_other_models_are_untouched(self):
        for kind in ("transactional", "subscription", "marketplace", "ad_supported"):
            with self.subTest(kind=kind):
                self.assertTrue(self._directive(kind).strip())


class TestTheCostBasisIsTheOneThatWasComputed(unittest.TestCase):
    """C12a. Two producers of this string, zero consumers of the right one."""

    def _basis(self, **kw):
        """`fixed_cost_basis` lives inside `at_som_volume`, which is only built when an
        annual revenue is supplied. Reading it off the top level returns "" — and an
        `assertNotIn` against "" passes vacuously, which is how the first draft of this
        test went green while proving nothing."""
        from business_model import retail_unit_economics
        econ = retail_unit_economics(annual_revenue_usd=900_000.0, **kw)
        at_som = econ.get("at_som_volume") or {}
        self.assertTrue(at_som, "the at-SOM block did not build, so nothing was checked")
        return at_som.get("fixed_cost_basis", "")

    def test_a_services_venture_is_not_told_it_pays_rent(self):
        basis = self._basis(
            price_per_unit=12_000.0, variable_cost_per_unit=4_000.0,
            monthly_fixed_cost=60_000.0, unit="project", kind="services",
            cost_source="estimated: early-stage company overhead (team + infrastructure "
                        "+ tooling)")
        self.assertNotIn("single-site rent", basis,
                         "a consultancy's fixed cost was described as shop rent")
        self.assertIn("overhead", basis, "the basis that WAS computed never arrived")

    def test_a_physical_venture_still_says_rent(self):
        basis = self._basis(
            price_per_unit=6.50, variable_cost_per_unit=2.0,
            monthly_fixed_cost=28_500.0, unit="drink", kind="transactional",
            cost_source="estimated: single-site rent + staff + utilities")
        self.assertIn("rent", basis)

    def test_no_cost_source_falls_back_by_kind(self):
        self.assertIn("rent", self._basis(
            price_per_unit=6.50, variable_cost_per_unit=2.0, monthly_fixed_cost=28_500.0,
            unit="drink", kind="transactional", cost_source=""))
        self.assertNotIn("rent", self._basis(
            price_per_unit=12_000.0, variable_cost_per_unit=4_000.0,
            monthly_fixed_cost=60_000.0, unit="project", kind="services", cost_source=""))


class TestAnAgencyIsNotAMarketplace(unittest.TestCase):
    """C12b. `project` is a services unit that happens to appear in marketplace listings."""

    def test_a_services_venture_is_not_told_to_sample_take_rates(self):
        from business_model import benchmark_validation_note
        note = benchmark_validation_note(
            "project", category="design agency", business_model="project-based retainer")
        self.assertNotIn("take-rate", note.lower(),
                         "an agency with no take-rate was told to benchmark against one")

    def test_a_real_marketplace_still_gets_the_take_rate_note(self):
        from business_model import benchmark_validation_note
        note = benchmark_validation_note(
            "booking", category="home services platform",
            business_model="we take 15% commission on each booking")
        self.assertIn("take-rate", note.lower())

    def test_a_marketplace_selling_projects_is_still_a_marketplace(self):
        """The unit noun is not the signal; the model is. A platform that brokers projects
        must keep the take-rate note."""
        from business_model import benchmark_validation_note
        note = benchmark_validation_note(
            "project", category="freelance platform",
            business_model="two-sided marketplace, commission on each project")
        self.assertIn("take-rate", note.lower())


class TestThePromptDoesNotShipAFormatPlaceholder(unittest.TestCase):
    """C12c. Two characters, in every 4Ps prompt of every run since it was written."""

    def _rule(self, unit="drink"):
        from four_ps import _r_citation_discipline
        return _r_citation_discipline({"economics": {"unit": unit}})

    def test_the_unit_is_interpolated_everywhere_it_appears(self):
        rule = self._rule("booking")
        self.assertNotIn("{unit}", rule,
                         "the literal characters {unit} ship inside the citation rule")
        self.assertIn("booking", rule)

    def test_the_example_uses_the_ventures_own_unit(self):
        self.assertIn("drink", self._rule("drink"))


if __name__ == "__main__":
    unittest.main()
