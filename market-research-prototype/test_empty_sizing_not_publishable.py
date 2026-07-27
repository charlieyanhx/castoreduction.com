"""
A sizing with no numbers must never be publishable.

Measured on a live run: size_hyperlocal returned `tam: {}, sam: {}, som: {}` with its own
note "households or spend unavailable — TAM not computed", and the gate said:

    validation: {"passed": true, "blocks": [], "warns": []}
    publishable: True

The gate passed because there was NOTHING TO CHECK. Every invariant in `_check` is guarded
by `_num(...)` — SOM <= SAM <= TAM, the share ceiling, formula reconciliation, provenance —
so absent numbers satisfy all of them vacuously. That is the same failure shape as prose in
the formula slot earlier in this program: `None` means "no-op", and a no-op is
indistinguishable from a healthy pass.

It is the more dangerous half of the bug, worse than the missing numbers themselves: a
report shipped with no market size AND no signal that anything went wrong. The report has a
loud unpublishable banner and downstream consumers can refuse an unpublishable sizing —
none of it fires when the gate calls emptiness a pass.

The rule: a sizing that produced no TAM, SAM or SOM value has not been validated, it has
been skipped, and it must say so.
"""
from __future__ import annotations

import glob
import json
import unittest

from skills.sizing.validate import validate_numbers

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


class TestEmptyFailsValidation(unittest.TestCase):
    def test_a_sizing_with_no_numbers_at_all_is_blocked(self):
        ev = validate_numbers({})
        self.assertFalse(ev.payload["passed"])
        self.assertTrue(any(b["check"] == "no_numbers" for b in ev.payload["blocks"]),
                        f"no no_numbers block: {ev.payload['blocks']}")

    def test_the_live_run_shape_is_blocked(self):
        """Exactly what the live run produced: the keys exist but hold nothing."""
        ev = validate_numbers({"tam_usd": None, "sam_usd": None, "som_usd": None,
                               "figures": [], "segmentation": []})
        self.assertFalse(ev.payload["passed"])

    def test_the_error_says_no_numbers_were_produced(self):
        ev = validate_numbers({})
        self.assertIn("no", (ev.error or "").lower())
        self.assertTrue(ev.error, "an empty sizing must carry an error string")

    def test_a_tam_alone_is_enough_to_be_validated(self):
        """The rule is 'produced nothing', not 'produced everything'. A partial sizing is a
        real result and its own checks still govern it."""
        ev = validate_numbers({"tam_usd": 1_000_000.0,
                               "figures": [{"value_usd": 1e6, "label": "TAM",
                                            "source": "Census"}]})
        self.assertTrue(ev.payload["passed"])
        self.assertFalse(any(b["check"] == "no_numbers" for b in ev.payload["blocks"]))

    def test_a_som_alone_is_enough(self):
        ev = validate_numbers({"som_usd": 250_000.0})
        self.assertFalse(any(b["check"] == "no_numbers" for b in ev.payload["blocks"]))

    def test_a_zero_tam_is_a_number_not_an_absence(self):
        """0 is a finding — a market genuinely sized at zero — not a failure to size."""
        ev = validate_numbers({"tam_usd": 0.0})
        self.assertFalse(any(b["check"] == "no_numbers" for b in ev.payload["blocks"]))

    def test_a_non_numeric_value_does_not_count_as_a_number(self):
        ev = validate_numbers({"tam_usd": "unknown", "sam_usd": True})
        self.assertTrue(any(b["check"] == "no_numbers" for b in ev.payload["blocks"]),
                        "a string and a bool were accepted as sizing numbers")


class TestPublishableIsFalse(unittest.TestCase):
    def test_gate_and_annotate_marks_an_empty_sizing_unpublishable(self):
        from plan import gate_and_annotate_sizing
        out = gate_and_annotate_sizing({"tam": {}, "sam": {}, "som": {}}, {})
        self.assertFalse(out["publishable"])

    def test_a_real_sizing_stays_publishable(self):
        from plan import gate_and_annotate_sizing
        out = gate_and_annotate_sizing(
            {"tam": {"mid": 1e9, "method_top_down": {"value_usd": 1e9,
                                                     "calculation": "$10B * 10%",
                                                     "source": "Gartner"}},
             "sam": {"mid": 3e8}, "som": {"mid": 3e7}}, {})
        self.assertTrue(out["publishable"])

    def test_the_live_run_result_flips_to_unpublishable(self):
        """The saved live run, re-gated. This is the regression that started it."""
        import os
        from plan import gate_and_annotate_sizing
        path = ("/private/tmp/claude-501/-Users-charlieyan-Downloads-castor-advisories/"
                "2584ff3c-84f2-497f-86fb-84b3249b7aaa/scratchpad/live_run.json")
        if not os.path.exists(path):
            self.skipTest("live run not on disk")
        ms = (json.load(open(path)) or {}).get("market_sizing") or {}
        self.assertTrue(ms.get("publishable"), "fixture should show the old behaviour")
        self.assertFalse(gate_and_annotate_sizing(dict(ms), {})["publishable"])


class TestGateD50(unittest.TestCase):
    def _gate(self, result):
        from gates import d50_no_publishable_sizing_without_numbers
        return d50_no_publishable_sizing_without_numbers(result, None)

    def test_publishable_with_no_tam_fails(self):
        f = self._gate({"market_sizing": {"tam": {}, "sam": {}, "som": {},
                                          "publishable": True}})
        self.assertFalse(f.ok)
        self.assertIn("no", f.detail.lower())

    def test_unpublishable_with_no_tam_is_honest_and_passes(self):
        """Producing no numbers is allowed. Claiming they are publishable is not."""
        f = self._gate({"market_sizing": {"tam": {}, "publishable": False}})
        self.assertIsNot(f.ok, False)

    def test_a_real_sizing_passes(self):
        self.assertTrue(self._gate({"market_sizing": {"tam": {"mid": 1e9},
                                                      "publishable": True}}).ok)

    def test_not_applicable_without_a_sizing(self):
        self.assertIsNone(self._gate({}).ok)


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestNoFalseFiresOnTheCorpus(unittest.TestCase):
    def test_every_stored_report_with_real_numbers_still_passes(self):
        from gates import d50_no_publishable_sizing_without_numbers as gate
        from skills.sizing.validate import validate_numbers as vn
        bad = []
        for path in _CORPUS:
            r = (json.load(open(path)) or {}).get("result") or {}
            ms = r.get("market_sizing") or {}
            tam_mid = (ms.get("tam") or {}).get("mid")
            if isinstance(tam_mid, (int, float)) and gate(r, None).ok is False:
                bad.append(path.split("/")[-1])
        self.assertEqual(bad, [], f"D50 false-fired on reports with a real TAM: {bad}")

    def test_a_corpus_sizing_still_validates_as_before(self):
        """The new block must not change the verdict on any sizing that has numbers."""
        from skills.sizing.validate import validate_numbers as vn
        flipped = []
        for path in _CORPUS:
            r = (json.load(open(path)) or {}).get("result") or {}
            ms = r.get("market_sizing") or {}
            tam_mid = (ms.get("tam") or {}).get("mid")
            if not isinstance(tam_mid, (int, float)):
                continue
            ev = vn({"tam_usd": tam_mid, "sam_usd": (ms.get("sam") or {}).get("mid"),
                     "som_usd": (ms.get("som") or {}).get("mid")})
            if any(b["check"] == "no_numbers" for b in ev.payload["blocks"]):
                flipped.append(path.split("/")[-1])
        self.assertEqual(flipped, [])


if __name__ == "__main__":
    unittest.main()


class TestFiguresCountAsNumbers(unittest.TestCase):
    """My first version scoped "produced a number" to tam/sam/som and returned early. It
    broke five existing checks that run on figure-only payloads — provenance, formula
    reconciliation, external grounding — and those tests were right: a payload of figures
    carrying value_usd has very much produced numbers. Scoping too narrowly replaced one
    silent pass with a narrower one."""

    def test_a_figure_only_payload_is_not_called_empty(self):
        from skills.sizing.validate import _check
        blocks, _ = _check({"figures": [{"value_usd": 5, "label": "X", "source": "Census"}]},
                           0.4)
        self.assertFalse(any(b["check"] == "no_numbers" for b in blocks))

    def test_a_figure_only_payload_still_gets_its_own_checks(self):
        """The regression: an early return suppressed provenance/reconciliation entirely."""
        from skills.sizing.validate import _check
        blocks, _ = _check({"figures": [{"value_usd": 5, "label": "X", "source": ""}]}, 0.4)
        self.assertTrue(any(b["check"] == "provenance" for b in blocks))

    def test_triangulation_side_values_count_as_numbers(self):
        from skills.sizing.validate import _check
        blocks, warns = _check({"som_demand_usd": 100, "som_supply_usd": 1000}, 0.4)
        self.assertFalse(any(b["check"] == "no_numbers" for b in blocks))
        self.assertTrue(any(w["check"] == "triangulation" for w in warns))

    def test_a_genuinely_empty_payload_is_still_caught(self):
        from skills.sizing.validate import _check
        blocks, _ = _check({"figures": [], "segmentation": [], "notes": ["nothing"]}, 0.4)
        self.assertTrue(any(b["check"] == "no_numbers" for b in blocks))

    def test_the_block_does_not_suppress_other_findings(self):
        """Append, never short-circuit — a silent pass is what this fix exists to remove."""
        from skills.sizing.validate import _check
        blocks, _ = _check({"figures": [{"value_usd": None, "label": "X", "source": ""}]},
                           0.4)
        checks = {b["check"] for b in blocks}
        self.assertIn("no_numbers", checks)
