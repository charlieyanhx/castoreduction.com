"""The GMV-vs-revenue guard is implemented, documented, gated on — and has never had an input.

Audit C10. `market_sizing.SIZING_PROMPT` documents the field, twice, in the model's own
words:

    "unit": "revenue" or "gmv" — what value_usd MEASURES. For take-rate/commission
    models: total transaction volume is "gmv"; platform revenue (take rate applied)
    is "revenue". Mixing them 6x-inflates TAM.

`apply_tam_triangulation` reads it (`market_sizing.py:151`) and excludes conflicting units
from the headline. Both halves are real. The wire between them is not:

  - `SIZING_PROMPT` is interpolated into `shared_ctx` at :227, and `shared_ctx` is NEVER
    READ — one assignment, zero references in the module.
  - The three prompts that actually run (`tam_top_down`, `tam_bottom_up`, `tam_analog`) ask
    for `value_usd`, `calculation` and `source`. None asks for `unit`.
  - So `(m.get("unit") or "revenue")` takes its default on every method of every run, and
    the exclusion has never fired.

WHAT IT COSTS. A marketplace triangulates two GMV-shaped methods (top-down "category
transaction volume", analog "comparable's GMV") against one platform-revenue method, and the
report says "3-method triangulation … the methods roughly agree". They do not agree; they
measure different things, and the agreement is manufactured by mislabelling. The audit
measures the headline moving $42.1B -> $45.1B once the analog method is correctly excluded,
with the conflict disclosed rather than averaged away.

This is the same shape as C6 and C8 — a guard that cannot see its own input — except here
nothing diverged silently: the field simply never arrived, and the default made that look
like unanimity.
"""
from __future__ import annotations

import unittest


class TestTheLivePromptsAskForTheUnit(unittest.TestCase):
    """The wire. Every prompt that actually runs must request the field the triangulator
    reads, or the default silently answers for it."""

    def _layer_prompts(self):
        """The prompt bodies, captured from the module rather than restated here."""
        import inspect

        import market_sizing
        return inspect.getsource(market_sizing.estimate_market_size)

    def test_each_of_the_three_methods_requests_a_unit(self):
        src = self._layer_prompts()
        for method in ("method_top_down", "method_bottom_up", "method_analog"):
            block = src.split(method, 1)[1][:600] if method in src else ""
            self.assertIn('"unit"', block,
                          f"{method}'s prompt never asks for `unit`, so the triangulator's "
                          f'`(m.get("unit") or "revenue")` answers for it')

    def test_the_prompts_explain_the_distinction_they_are_asking_about(self):
        """A bare '"unit": "revenue" or "gmv"' invites a coin flip. The prompt has to say
        which is which for a take-rate model, because that is the only case where it
        matters and the only case that gets it wrong."""
        src = self._layer_prompts()
        self.assertIn("gmv", src.lower())
        self.assertIn("take", src.lower())

    def test_the_dead_shared_context_is_gone(self):
        """`shared_ctx` was assigned once and read never — a 40-line prompt built on every
        run and thrown away, and the reason the documented field looked wired.

        Matched on the ASSIGNMENT, not the name: the comment that replaced it mentions the
        identifier deliberately, and a test that forbids naming what was removed forbids
        explaining why."""
        import inspect
        import re

        import market_sizing
        src = inspect.getsource(market_sizing)
        self.assertIsNone(re.search(r"^\s*shared_ctx\s*=", src, re.M),
                          "the dead prompt build is still there")


class TestTheUnitSurvivesIntoTheMethodDicts(unittest.TestCase):
    """Asking is not enough — the answer has to reach `apply_tam_triangulation`.

    There is no merge helper to unit-test: the assembly is `block_copy = dict(v)` inside
    `estimate_market_size`, so the field survives by construction ONCE the model returns
    it. Exercised through the real function with the LLM mocked, because a test of a helper
    I invented would have proved nothing about the path that runs.
    """

    def _size(self, unit):
        from unittest.mock import patch

        import market_sizing

        def fake_call_json(system=None, user=None, max_tokens=None, **kw):
            for key, value in (("method_top_down", 2.0e9), ("method_bottom_up", 2.2e9),
                               ("method_analog", 42.1e9)):
                if key in (user or ""):
                    m = {"value_usd": value, "calculation": "c", "source": "s"}
                    if unit is not None:
                        m["unit"] = "gmv" if key == "method_analog" else "revenue"
                    return {key: m}
            return {}

        with patch.object(market_sizing, "call_json", fake_call_json):
            return market_sizing.estimate_market_size(
                {"name": "P", "category": "marketplace", "geography": "US"},
                [], {}, {}, {})

    def test_a_method_dict_keeps_the_unit_the_model_returned(self):
        tam = self._size(unit="gmv").get("tam") or {}
        self.assertEqual((tam.get("method_analog") or {}).get("unit"), "gmv")
        self.assertEqual((tam.get("method_top_down") or {}).get("unit"), "revenue")

    def test_the_conflict_is_excluded_end_to_end(self):
        tam = self._size(unit="gmv").get("tam") or {}
        self.assertTrue((tam.get("method_analog") or {}).get("excluded_from_headline"),
                        "a GMV analog was averaged into a platform-revenue TAM")

    def test_a_missing_unit_is_not_invented_at_assembly(self):
        """When the model declines to say, the field stays absent here — the default
        belongs in ONE place, the triangulator, so a reader auditing the artifact can tell
        an answer from an assumption."""
        tam = self._size(unit=None).get("tam") or {}
        self.assertIsNone((tam.get("method_analog") or {}).get("unit"))


class TestTheExclusionFiresWhenUnitsConflict(unittest.TestCase):
    """The half that already worked, pinned so wiring the input cannot break it."""

    def _tam(self, units):
        from market_sizing import apply_tam_triangulation
        block = {"mid": 0.0}
        for key, (value, unit) in units.items():
            block[key] = {"value_usd": value, "calculation": "c", "source": "s"}
            if unit:
                block[key]["unit"] = unit
        apply_tam_triangulation(block)
        return block

    def test_a_gmv_method_is_excluded_from_a_revenue_headline(self):
        block = self._tam({
            "method_top_down": (2.0e9, "revenue"),
            "method_bottom_up": (2.2e9, "revenue"),
            "method_analog": (42.1e9, "gmv"),
        })
        self.assertTrue(block["method_analog"].get("excluded_from_headline"),
                        "a GMV figure was averaged into a platform-revenue TAM")

    def test_methods_that_agree_on_units_are_all_kept(self):
        block = self._tam({
            "method_top_down": (2.0e9, "revenue"),
            "method_bottom_up": (2.2e9, "revenue"),
            "method_analog": (2.4e9, "revenue"),
        })
        for key in ("method_top_down", "method_bottom_up", "method_analog"):
            self.assertFalse(block[key].get("excluded_from_headline"))

    def test_the_headline_moves_when_the_conflict_is_seen(self):
        """The point of the whole item: with the field populated the headline is a
        different number, so this was never cosmetic."""
        mixed = self._tam({"method_top_down": (2.0e9, "revenue"),
                           "method_bottom_up": (2.2e9, "revenue"),
                           "method_analog": (42.1e9, "gmv")})
        blind = self._tam({"method_top_down": (2.0e9, None),
                           "method_bottom_up": (2.2e9, None),
                           "method_analog": (42.1e9, None)})
        self.assertNotEqual(round(mixed["mid"]), round(blind["mid"]),
                            "labelling the units changed nothing, so either the exclusion "
                            "or this fixture is wrong")


if __name__ == "__main__":
    unittest.main()
