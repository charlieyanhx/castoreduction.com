"""
W5-5: context/reminders.py — one registry for the cross-section guardrails.

four_ps.py grew four of these by hand: model_directive, price_anchor_directive,
competitive_density_directive, unit_economics_rubric. Each exists for the same reason
— a section that never receives a fact will invent one, and two sections inventing
independently contradict each other (a $6/drink cafe with "$12K MRR", a marketplace
priced "/mo per booking", Place quoting $200 where Price says $150).

Each was also wired by hand at each call site, with the `+ _md + _pa + _cd` suffix
repeated per prompt. Adding a fifth means editing every site and hoping none is
missed — and a missed site is exactly the failure the directives exist to prevent.

This registry makes the set explicit and applies it uniformly. The test that matters
is the last one: EVERY registered section prompt carries EVERY applicable reminder.
"""
from __future__ import annotations

import unittest

from context.reminders import Reminders, reminder

# Registration happens at four_ps import time, so import it here rather than inside a
# test — otherwise whichever test runs first decides whether the registry is populated.
import four_ps  # noqa: E402

FACTS = {"business_model_kind": "marketplace", "economics": {"unit": "booking"},
         "van_westendorp": {"optimal_price_point": 150}, "competitor_density": 9,
         "active_signal_density": 4}


class TestRegistry(unittest.TestCase):
    def test_registered_reminders_are_discoverable(self):
        names = Reminders.names()
        for expected in ("monetization_model", "price_anchor", "competitive_density"):
            self.assertIn(expected, names)

    def test_a_reminder_declares_what_it_needs(self):
        r = Reminders.get("price_anchor")
        self.assertTrue(r.requires, "a reminder with no declared inputs cannot be skipped safely")

    def test_registering_a_duplicate_name_is_an_error(self):
        """Silent overwrite would let a new reminder shadow an existing guardrail."""
        with self.assertRaises(ValueError):
            @reminder("price_anchor", requires=("x",))
            def _dupe(facts):
                return "x"


class TestRendering(unittest.TestCase):
    def test_assemble_returns_text_for_the_applicable_reminders(self):
        text = Reminders.assemble(FACTS)
        self.assertIn("MARKETPLACE", text.upper())
        self.assertIn("150", text)

    def test_missing_facts_skip_a_reminder_rather_than_crash(self):
        text = Reminders.assemble({"business_model_kind": "marketplace"})
        self.assertIn("MARKETPLACE", text.upper())

    def test_no_facts_yields_no_text_beyond_the_always_on_ones(self):
        text = Reminders.assemble({})
        self.assertIsInstance(text, str)

    def test_assembly_is_byte_stable(self):
        self.assertEqual(Reminders.assemble(FACTS), Reminders.assemble(FACTS))

    def test_order_does_not_depend_on_dict_insertion_order(self):
        shuffled = {k: FACTS[k] for k in reversed(list(FACTS))}
        self.assertEqual(Reminders.assemble(FACTS), Reminders.assemble(shuffled))

    def test_a_reminder_that_raises_is_skipped_not_fatal(self):
        @reminder("boom_test", requires=("boom",))
        def _boom(facts):
            raise RuntimeError("nope")
        try:
            text = Reminders.assemble({**FACTS, "boom": 1})
            self.assertIn("MARKETPLACE", text.upper())
        finally:
            Reminders.unregister("boom_test")


class TestFourPsUsesTheRegistry(unittest.TestCase):
    """The point of the registry: no section can miss a guardrail."""

    def test_every_section_prompt_carries_every_applicable_reminder(self):
        import four_ps
        expected = four_ps.section_reminders(
            business_model_kind="marketplace",
            economics={"unit": "booking"},
            van_westendorp={"optimal_price_point": 150},
            competitor_density=9,
            active_signal_density=4,
        )
        self.assertTrue(expected.strip(), "no reminders assembled at all")
        prompts = four_ps.build_section_prompts(
            {"product": "p", "price": "pr", "place": "pl", "promotion": "pm"}, expected)
        self.assertEqual(set(prompts), {"product", "price", "place", "promotion"})
        for name, prompt in prompts.items():
            self.assertIn(expected, prompt, f"{name} is missing the reminder block")


if __name__ == "__main__":
    unittest.main()
