"""D55 counted a venture's SHAPE as if it were the report's incompleteness.

FOUND BY A USER'S OWN RUN. Job d62bc04f (a satellite orbital-mirror venture, classified
national_digital + subscription) was withheld with:

    "only 31/60 invariants (52%) could answer on this report, below the 55% floor — it is
     too incomplete to have been meaningfully verified"

MEASURED, on that stored artifact: of the 29 invariants that abstained, THIRTEEN abstain on
every healthy national_digital + subscription report in the corpus, because they are
hyperlocal-only or per-unit-only by construction:

    d07 "scale=national_digital (not hyperlocal)"      d40 "not a hyperlocal venture"
    d49/d52/d56/d57/d59/d60  "no trade area"           d05/d06/d17/d37 "not a per-unit model"
    d11 "US venture"

A subscription venture forfeits a third of the denominator before the run starts. Corrected,
that report answered 31 of 47 APPLICABLE invariants, and its verdict was 25 pass / 1 fail —
the 1 fail being D55 itself.

D55'S OWN DOCSTRING ALREADY REJECTS THIS, one level down:

    "Deliberately NOT a section checklist. Sections are legitimately conditional --
     customer_universe is B2B-only by design, and a hyperlocal cafe rightly skips it -- so
     counting sections would fail honest reports for being the shape they should be."

It refused to count sections for exactly this reason and then counted GATES with the
identical defect. A hyperlocal-only gate abstaining on a subscription venture IS
customer_universe being absent on a cafe.

WHY ONLY SHAPE LEAVES THE DENOMINATOR. The abstentions are three families, not two, and the
distinction is what keeps this gate sharp:

  1. SHAPE      — "not a per-unit model", "scale=national_digital". Cannot ever answer for
                  this venture. Not a completeness signal. EXCLUDED.
  2. NO CLAIM   — "no at-SOM claim", "nothing withheld", "viability names no competitor
                  count". These guard against a specific bad claim, and a hollow report makes
                  no claims at all, so all of them abstain together. That is precisely the
                  failure mode D55 exists to catch. KEPT IN.
  3. NO DATA    — "no differentiators", "no competitor pricing". KEPT IN.

Excluding family 1 only, measured across every artifact on disk:

    run2  43% -> 49%   still withheld   <- the case named in D55's own docstring
    run3  45% -> 51%   still withheld
    run4  45% -> 50%   still withheld
    c98_subscription  55% -> 70%   delivers
    becc8783          58% -> 74%   delivers
    3219f4db          60% -> 77%   delivers
    c98_nonus (hyperlocal)  73% -> 75%   delivers, oos=1

The hollow reports keep failing and the healthy ones stop being punished for their business
model. Excluding families 2 and 3 too would push run2 over the floor and retire the gate.

THE MECHANISM. Only the gate knows which family its own abstention belongs to, so the gate
says it: `not_applicable(...)` instead of `Finding(None, ...)`. Same shape as every other fix
this session — the module that owns the fact is the one that states it, and the counter stops
inferring what it cannot see.
"""
from __future__ import annotations

import inspect
import unittest


def _findings(r, html=None):
    """Every invariant's Finding, keyed by D-number, the way D55 itself collects them."""
    import gates
    out = {}
    for inv in gates.INVARIANTS:
        if inv.id == "D55":
            continue
        try:
            out[inv.id] = inv.check(r, html)
        except Exception as e:                       # noqa: BLE001 - recorded, not raised
            out[inv.id] = e
    return out


class TestAFindingCanSayWhyItAbstained(unittest.TestCase):
    def test_out_of_scope_is_a_distinct_state_from_plain_not_applicable(self):
        from gates import Finding
        self.assertFalse(Finding(None, "no differentiators").out_of_scope,
                         "a missing section must not read as out-of-scope")

    def test_the_helper_marks_it(self):
        from gates import not_applicable
        f = not_applicable("not a per-unit model")
        self.assertIsNone(f.ok)
        self.assertTrue(f.out_of_scope)
        self.assertEqual(f.detail, "not a per-unit model")

    def test_a_pass_or_fail_is_never_out_of_scope(self):
        from gates import Finding
        for ok in (True, False):
            self.assertFalse(Finding(ok, "x").out_of_scope)


class TestTheShapeAbstentionsAreMarked(unittest.TestCase):
    """The thirteen measured on the user's artifact. Each must declare itself out-of-scope
    when the venture is the wrong shape for it -- and NOT when its data is merely absent."""

    SUBSCRIPTION = {
        "profile": {"category": "orbital solar reflection service", "geography": "United States"},
        "business_model_kind": "subscription",
        "market_scale": {"scale": "national_digital", "sizing_skill": "size_national_digital"},
        "market_sizing": {"tam": {"mid": 1.0e9}, "sam": {"mid": 3.0e8}, "som": {"mid": 2.0e7},
                          "figures": [], "publishable": True},
        "economics": {"price_usd": 1450.0, "pricing_unit": "subscriber"},
        "four_ps": {}, "financials": {}, "viability": {}, "_steps_completed": [],
    }

    SHAPE_GATES = ("D05", "D06", "D07", "D11", "D17", "D37",
                   "D40", "D49", "D52", "D56", "D57", "D59", "D60")

    def test_each_abstains_out_of_scope_on_a_national_digital_subscription(self):
        found = _findings(self.SUBSCRIPTION)
        wrong = []
        for gid in self.SHAPE_GATES:
            f = found.get(gid)
            if isinstance(f, Exception):
                wrong.append(f"{gid} raised {type(f).__name__}: {f}")
            elif f is None:
                wrong.append(f"{gid} not registered")
            elif f.ok is not None:
                continue                       # answered outright: fine, it is in the count
            elif not f.out_of_scope:
                wrong.append(f"{gid} abstained without saying it is out of scope: {f.detail!r}")
        self.assertEqual(wrong, [], "\n  ".join([""] + wrong))

    def test_a_hyperlocal_venture_puts_the_trade_area_gates_back_in_scope(self):
        """The flag must depend on the VENTURE, not be hardcoded onto the gate -- or a
        hyperlocal report would stop being held to the trade-area rules."""
        hyperlocal = {
            "profile": {"category": "specialty coffee shop", "geography": "San Francisco, CA"},
            "business_model_kind": "transactional",
            "market_scale": {"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"},
            "market_sizing": {"tam": {"mid": 4.0e6}, "som": {"mid": 3.0e5},
                              "trade_area_households": 12000, "radius_m": 1500,
                              "catchment_km2": 7.07, "method": "trade_area_catchment",
                              "figures": [], "publishable": True},
            "economics": {"price_usd": 6.5, "pricing_unit": "drink"},
            "four_ps": {}, "financials": {}, "viability": {}, "_steps_completed": [],
        }
        found = _findings(hyperlocal)
        in_scope = [g for g in ("D07", "D40", "D49", "D52", "D56", "D57", "D59", "D60")
                    if not isinstance(found.get(g), Exception)
                    and found.get(g) is not None and not found[g].out_of_scope]
        self.assertTrue(in_scope,
                        "every trade-area gate declared itself out of scope on a HYPERLOCAL "
                        "venture -- the flag is hardcoded, not shape-derived")


class TestD55CountsOnlyWhatCouldHaveApplied(unittest.TestCase):
    def _d55(self, r, html=None):
        from gates import d55_report_is_complete_enough_to_have_been_checked as d55
        return d55(r, html)

    def test_the_denominator_drops_the_shape_gates(self):
        """The fix itself. The fixture below is a deliberately thin stub, so it SHOULD still
        be withheld — what must change is what it is measured against: 47 applicable
        invariants, not all 60. Asserting 'not withheld' on a stub I wrote to be hollow would
        have been a vacuous test; this checks the denominator, which is the actual defect."""
        import re
        f = self._d55(TestTheShapeAbstentionsAreMarked.SUBSCRIPTION)
        m = re.search(r"(\d+)/(\d+) applicable invariants", f.detail or "")
        self.assertIsNotNone(m, f"no applicable-denominator in the message: {f.detail!r}")
        total = int(m.group(2))
        self.assertLess(total, 60,
                        "the denominator is still every invariant, so a subscription venture "
                        "is still charged for the hyperlocal rules it can never satisfy")
        self.assertGreaterEqual(total, 40,
                                f"the denominator collapsed to {total} — too many abstentions "
                                "are being waved through as 'shape', which retires the gate")

    def test_the_real_measured_artifacts_stop_being_withheld_for_their_shape(self):
        """The regression, on real reports rather than a fixture. Measured before the fix:
        c98_subscription 55%, becc8783 58%, 3219f4db 60% — all within 5 points of the floor
        purely because they are subscriptions. Skips where the corpus is absent (out/ is
        gitignored), because a test that silently passes on no data is worse than no test."""
        import glob
        import json
        import os
        paths = sorted(glob.glob("out/wave4_corpus/*.json")) + sorted(glob.glob("out/live/*.json"))
        checked = []
        for p in paths:
            d = json.load(open(p))
            r = d.get("result") if isinstance(d, dict) and "result" in d else d
            if not isinstance(r, dict) or "profile" not in r:
                continue
            if r.get("business_model_kind") != "subscription":
                continue
            hp = p[:-5] + ".html"
            html = open(hp).read() if os.path.exists(hp) else None
            f = self._d55(r, html)
            checked.append((os.path.basename(p), f.ok, f.detail))
        if not checked:
            self.skipTest("no stored subscription report to measure against")
        withheld = [(n, d) for n, ok, d in checked if ok is False]
        self.assertEqual(withheld, [],
                         f"{len(withheld)} of {len(checked)} real subscription reports are "
                         "still withheld:\n  " + "\n  ".join(f"{n}: {d}" for n, d in withheld))

    def test_out_of_scope_gates_are_named_not_silently_dropped(self):
        """A reader must be able to see the denominator shrink and why, or this becomes a
        number nobody can audit -- the defect the original message had."""
        f = self._d55(TestTheShapeAbstentionsAreMarked.SUBSCRIPTION)
        self.assertRegex((f.detail or "").lower(), r"do(es)? not apply|out of scope|applicable",
                         f"the message does not disclose the excluded gates: {f.detail!r}")

    def test_a_hollow_report_is_still_withheld(self):
        """D55's entire purpose. out/live/run2 scored 23 pass / 0 fail and looked BETTER than
        the fuller run1 -- if this stops failing, the gate is retired, not fixed."""
        hollow = {"profile": {"category": "coffee shop", "geography": "Portland, OR"},
                  "business_model_kind": "transactional",
                  "market_scale": {"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"},
                  "_steps_completed": []}
        f = self._d55(hollow)
        self.assertIs(f.ok, False,
                      f"an almost-empty report passed the completeness floor: {f.detail!r}")

    def test_the_denominator_never_reaches_zero(self):
        from gates import d55_report_is_complete_enough_to_have_been_checked as d55
        self.assertIsNotNone(d55({}, None))          # must not ZeroDivisionError

    def test_d55_still_excludes_itself(self):
        src = inspect.getsource(
            __import__("gates").d55_report_is_complete_enough_to_have_been_checked)
        self.assertIn("D55", src, "the self-exclusion guard is gone; D55 would count itself")


if __name__ == "__main__":
    unittest.main()
