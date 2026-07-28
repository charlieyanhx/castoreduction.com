"""
THE STANDING RULE: a gate must be able to fire on a real shipped report.

A gate that returns N/A on every report it will ever see is not a safeguard, it is a
comforting entry in a scorecard. This file enforces reachability against the STORED CORPUS —
real pipeline output, not a synthetic fixture — because that is exactly the check that was
missing when the rule was written.

It was written because D49 failed it. D49 exists to catch a measured 372x error: a whole
county's household count used as one premise's trade area. It reads
`market_sizing.trade_area_households` and `market_sizing.radius_m`. `size_hyperlocal`
publishes both; `plan.py`'s payload mapping did not carry them through. Measured: 0 of 16
stored reports carried either key, so the gate guarding the single largest error in this
codebase's history was N/A on every report in existence — and its unit tests all passed,
because they were written against synthetic dicts that had the keys.

Every gate D46-D50 got synthetic tests. Only this check would have caught it.

WHY REACHABILITY IS THE RIGHT RULE, and not "every gate must FAIL somewhere":
a healthy gate legitimately passes on a clean corpus. What it must not do is decline to
answer. So the assertion is that at least one report yields True or False rather than None.
A gate that is genuinely inapplicable to the current corpus can be allowlisted below WITH A
REASON — the allowlist is the audit trail, not an escape hatch.
"""
from __future__ import annotations

import glob
import json
import os
import unittest

import gates

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))

# Gates that are legitimately N/A on the CURRENT corpus. Each entry needs a reason and,
# ideally, the venture shape that would exercise it — an entry with no path to ever firing
# is a gate that should be deleted, not allowlisted.
_KNOWN_UNREACHABLE: dict[str, str] = {
    "D22": "its density-claim regex matches none of the phrasings the pipeline actually "
           "writes — 11/17 reports state a numeric competitor count in viability prose and "
           "the pattern misses all of them. A real defect in the DETECTOR, tracked "
           "separately; allowlisted so this rule does not block on a pre-existing bug.",
    "D49": "reads market_sizing.trade_area_households / radius_m, which plan.py only began "
           "carrying AFTER this corpus was generated — so no stored report can contain "
           "them and corpus reachability is unprovable for it by construction. NOT a pass: "
           "TestD49SpecificallyReachesReality below enforces the same guarantee at the "
           "PRODUCER, asserting the mapping writes the keys and that the gate returns a "
           "real verdict on a current-shape payload. Remove this entry after the next "
           "corpus regeneration.",
    "D54": "reconciles the ledger's PRODUCER records against the report, and the stored "
           "corpus has none. Measured: all 16 reports carry a _trace (83 events in the "
           "first), but 0 of those events carry a `produces` key -- they predate the "
           "producer stamping, so recorded_producers() returns {} and there is nothing to "
           "reconcile. run2 has 10. NOT a pass: TestTheGateCatchesItOnTheLiveRun in "
           "test_produced_output_reaches_the_report.py proves it fires on out/live/run2.json "
           "-- a genuine end-to-end run -- catching all 3 measured silent drops. Remove "
           "this entry after the next corpus regeneration.",
}


# Corpus staleness is the one honest reason a gate can be unprovable here, and it must not
# become a blanket excuse. A gate allowlisted for staleness has to demonstrate reachability
# against a CURRENT-SHAPE payload instead — same guarantee, different artifact.
_STALENESS_ALLOWLISTED = {"D49", "D54"}


def _load_reports():
    out = []
    for path in _CORPUS:
        result = (json.load(open(path)) or {}).get("result") or {}
        html_path = path[:-5] + ".html"
        html = (open(html_path, encoding="utf-8").read()
                if os.path.exists(html_path) else None)
        out.append((os.path.basename(path), result, html))
    return out


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestEveryGateCanFireOnARealReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reports = _load_reports()

    def _verdicts(self, inv):
        seen = set()
        for _name, result, html in self.reports:
            try:
                seen.add(inv.check(result, html).ok)
            except Exception as e:                      # noqa: BLE001 - reported, not raised
                seen.add(f"RAISED:{type(e).__name__}")
        return seen

    def test_no_gate_is_not_applicable_on_every_stored_report(self):
        """The rule. A gate that never answers is not protecting anything."""
        dead = []
        for inv in gates.INVARIANTS:
            if inv.id in _KNOWN_UNREACHABLE:
                continue
            if self._verdicts(inv) <= {None}:
                dead.append(f"{inv.id} ({inv.name})")
        self.assertEqual(dead, [],
                         "these gates return N/A on all "
                         f"{len(self.reports)} stored reports, so they cannot fire on real "
                         f"pipeline output: {dead}. Either the keys they read are dropped "
                         "before the report is stored, or the detector is looking for "
                         "something the pipeline never writes.")

    def test_the_allowlist_only_contains_genuinely_unreachable_gates(self):
        """An allowlist that outlives its reason becomes a way to hide new dead gates."""
        stale = [gid for gid in _KNOWN_UNREACHABLE
                 if gid in {i.id for i in gates.INVARIANTS}
                 and not (self._verdicts(next(i for i in gates.INVARIANTS if i.id == gid))
                          <= {None})]
        self.assertEqual(stale, [],
                         f"allowlisted gates that now DO fire — remove them: {stale}")

    def test_the_allowlist_names_only_registered_gates(self):
        registered = {i.id for i in gates.INVARIANTS}
        self.assertEqual([g for g in _KNOWN_UNREACHABLE if g not in registered], [])

    def test_no_gate_raises_on_a_real_report(self):
        """run_gate isolates exceptions now, so a raising detector degrades to one failed
        cell rather than killing the sweep — which means a raise is easy to never notice."""
        raisers = []
        for inv in gates.INVARIANTS:
            for v in self._verdicts(inv):
                if isinstance(v, str) and v.startswith("RAISED"):
                    raisers.append(f"{inv.id}:{v}")
        self.assertEqual(raisers, [])

    def test_the_reachable_count_is_reported(self):
        """A visible number, so a drop is noticeable in CI output rather than silent."""
        reachable = sum(1 for inv in gates.INVARIANTS
                        if not (self._verdicts(inv) <= {None}))
        self.assertGreaterEqual(reachable, len(gates.INVARIANTS) - len(_KNOWN_UNREACHABLE),
                                f"only {reachable}/{len(gates.INVARIANTS)} gates can fire")


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestD49SpecificallyReachesReality(unittest.TestCase):
    """The gate that prompted the rule. Pinned by name, because a regression here means the
    372x county-scale error is unguarded again."""

    def test_d49_returns_a_real_verdict_on_a_current_shape_payload(self):
        """The compensating check for the staleness allowlist: the stored corpus cannot
        contain keys added after it was generated, so reachability is proven against what
        the pipeline produces TODAY. A verdict of True or False — never None."""
        from gates import d49_trade_area_matches_its_radius as d49
        current = {"market_sizing": {
            "scale": "hyperlocal", "radius_m": 3000, "catchment_km2": 28.27,
            "trade_area_households": 8872, "households_sourced": True,
            "tam": {"mid": 3.5e7}}}
        self.assertIsNotNone(d49(current, None).ok,
                             "D49 declines to answer even on a current-shape payload")
        county_scale = dict(current)
        county_scale["market_sizing"] = {**current["market_sizing"],
                                        "trade_area_households": 3_300_000}
        self.assertFalse(d49(county_scale, None).ok,
                         "D49 does not catch a county-scale count on the current shape")

    def test_the_hyperlocal_mapping_carries_the_scale_keys(self):
        """plan.py's payload mapping is where they were being dropped."""
        import inspect

        import plan
        src = inspect.getsource(plan)
        for key in ("trade_area_households", "radius_m", "catchment_km2"):
            self.assertIn(f'"{key}"', src,
                          f"plan.py does not carry {key} through to the stored report")


if __name__ == "__main__":
    unittest.main()
