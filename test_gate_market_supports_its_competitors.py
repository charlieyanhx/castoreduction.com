"""
D57: a market that cannot feed its own existing competitors is mis-sized.

THE MEASUREMENT THAT MOTIVATES IT. run9 published TAM $12.5M for a trade area it also said
contains 102 operating cafes — $122,433 of total food-away spend per existing cafe, below SF
rent for the storefront alone. Every internal gate passed, because every internal gate checks
CONSISTENCY: the arithmetic downstream of a wrong input was exact. The wrongness was only
visible from OUTSIDE the model — 102 real businesses were already surviving on a market the
report said could not sustain them. (The input was wrong: the trade-area cap inversion shipped
a 25x-low household count. Fixed separately; this gate exists so the NEXT wrong input, whatever
its cause, cannot ship a verdict.)

THE INVARIANT: if a trade-area TAM and a geo competitor count are both published, then
TAM / competitors must clear a survival floor. The floor is deliberately far below any real
revenue-per-venue figure — US urban food-service venues do not survive on less than $250K/yr of
ADDRESSABLE market around them (their revenue is a slice of that, so the true bar is much
higher; the floor only catches order-of-magnitude nonsense, not tight markets).

ok=None ONLY when there is no trade-area TAM or no competitor count to divide by — and a
published hyperlocal TAM whose competitor count is missing is itself reported, because "we
could not check" must never read as "checked and fine".

MEASURED ON STORED ARTIFACTS at authoring time: run5 $8.45M/102, run6 $12.5M/102,
run7/8/9 $12.5M/102-103 — all between $82K and $122K per cafe, all False. The corpus's six
hyperlocal reports predate geo competitor counts in this shape or fail for the same reason.
A post-fix run (corrected households ~53K -> TAM ~$309M -> ~$3M per cafe) passes with an
order of magnitude to spare.
"""
from __future__ import annotations

import glob
import json
import os
import unittest

from gates import d57_market_supports_its_competitors as d57


def _ms(tam_mid=None, competitors=None, method="trade_area_catchment", **extra):
    ms = {"method": method}
    if tam_mid is not None:
        ms["tam"] = {"mid": tam_mid}
    if competitors is not None:
        ms["competitors"] = competitors
    ms.update(extra)
    return {"market_sizing": ms}


class TestTheRun9ShapeFails(unittest.TestCase):
    def test_run9s_numbers_fail(self):
        f = d57(_ms(tam_mid=12_488_197.0, competitors=102), None)
        self.assertIs(f.ok, False)
        self.assertIn("122", f.detail.replace(",", ""),
                      f"the finding does not show the per-competitor arithmetic: {f.detail}")

    def test_a_corrected_sizing_passes(self):
        """~53K households x $5,830 -> $309M across 102 cafes = ~$3M each."""
        f = d57(_ms(tam_mid=308_700_000.0, competitors=102), None)
        self.assertIs(f.ok, True, f.detail)

    def test_the_floor_is_not_a_tight_market_detector(self):
        """$400K per venue is a hard market but a REAL one — the gate must only catch
        order-of-magnitude nonsense, or it will block honest reports about crowded areas."""
        f = d57(_ms(tam_mid=40_000_000.0, competitors=100), None)
        self.assertIs(f.ok, True, f.detail)

    def test_stored_runs_get_the_verdict_their_numbers_deserve(self):
        """CORRECTED FROM MY OWN FIRST DRAFT, which asserted every stored live run fails —
        true when written (run5-9 all shipped ~$82-122K/cafe), and wrong the moment the
        trade-area cap fix produced run10, which PASSES at ~$3M/cafe. A gate test must pin
        the INVARIANT (the verdict follows the ratio), not the historical accident that every
        artifact happened to be broken on the day the gate was born.

        The reachability rule still holds: the pre-fix runs keep exercising the False branch,
        and any post-fix run exercises True."""
        verdicts = {}
        for p in sorted(glob.glob("out/live/run*.json")):
            r = (json.load(open(p)) or {}).get("result") or {}
            ms = r.get("market_sizing") or {}
            if (ms.get("method") or "") != "trade_area_catchment":
                continue
            tam = (ms.get("tam") or {}).get("mid") or ms.get("tam_usd")
            comp = ms.get("competitors")
            if not tam or not isinstance(comp, (int, float)) or not comp:
                continue
            f = d57(r, None)
            verdicts[os.path.basename(p)] = f.ok
            want = (tam / comp) >= 250_000
            self.assertIs(f.ok, want,
                          f"{os.path.basename(p)}: ${tam / comp:,.0f}/competitor should be "
                          f"{'a pass' if want else 'a fail'}, got {f.ok}")
        self.assertGreaterEqual(sum(1 for v in verdicts.values() if v is False), 4,
                                "the pre-fix runs stopped exercising the False branch")
        self.assertGreaterEqual(len(verdicts), 5,
                                "the stored live runs stopped exercising this gate")


class TestNotApplicableCannotSwallowTheFailure(unittest.TestCase):
    def test_non_hyperlocal_is_none(self):
        self.assertIsNone(d57(_ms(tam_mid=5e9, competitors=40, method="top_down"), None).ok)

    def test_no_tam_is_none(self):
        self.assertIsNone(d57(_ms(competitors=102), None).ok)

    def test_a_missing_competitor_count_is_reported_not_skipped(self):
        """A published hyperlocal TAM with no competitor count is UNCHECKABLE, and the repo's
        dominant bug is uncheckable reading as fine. It must not be ok=None silence — the
        detail must say the check could not run. (ok=True with a could-not-check detail would
        be a lie; ok=None with a NAMED reason is the honest verdict, and D55's coverage
        accounting will show it.)"""
        f = d57(_ms(tam_mid=12_488_197.0), None)
        self.assertIsNone(f.ok)
        self.assertIn("competitor", (f.detail or "").lower(),
                      "the not-applicable reason does not say WHAT was missing")

    def test_zero_competitors_is_a_pass_with_disclosure(self):
        """A genuinely empty market (no OSM competitors) has nobody to divide by; the TAM may
        be small legitimately. Must not divide by zero, must not fail."""
        f = d57(_ms(tam_mid=500_000.0, competitors=0), None)
        self.assertIsNot(f.ok, False)

    def test_tam_usd_shape_is_also_read(self):
        """Engine payloads carry tam_usd; reshaped reports carry tam.mid. Both must work —
        a gate vacuous on one shape is how D49 went blind."""
        f = d57({"market_sizing": {"method": "trade_area_catchment",
                                   "tam_usd": 12_488_197.0, "competitors": 102}}, None)
        self.assertIs(f.ok, False)


if __name__ == "__main__":
    unittest.main()
