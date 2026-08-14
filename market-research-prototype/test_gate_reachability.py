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
REASON — the allowlist is the audit trail, not an escape hatch. And the most seductive reason,
"the corpus predates the key", is the one that owes a compensating artifact: see
_STALENESS_ALLOWLISTED.
"""
from __future__ import annotations

import glob
import json
import os
import unittest
from collections.abc import Callable

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
    "D60": "fires only when market_sizing.som_anchor.method == 'area_receipts_benchmark', "
           "which no stored report can carry: the Economic Census anchor did not exist "
           "when this corpus was generated, so every one of the 16 was anchored on the LLM "
           "estimate and D60 is N/A on all of them BY CONSTRUCTION. NOT a pass: "
           "test_d60_area_average_reaches_the_reader.py asserts it on current-shape "
           "payloads, and it is proven on a GENUINE END-TO-END RUN: out/live/run18.json, "
           "the first run produced with the Economic Census anchor, where D60 returns "
           "ok=True ('the SOM is disclosed as an area average, with its geography, its "
           "establishment count and its statistic') rather than N/A. The unit tests pin "
           "the negative half — ok=False when the geography, the establishment count or "
           "the statistic is dropped. Remove this entry after the next corpus "
           "regeneration.",
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
#
# That sentence used to be the whole mechanism, which is to say there was none: this set was
# read by nothing. TestTheStalenessAllowlistPaysItsCompensatingCheck below now RUNS each
# entry's demonstration and requires a real True/False verdict out of it, and refuses an
# entry that blames the corpus without joining this set. The rule and the check are the same
# object now.
_STALENESS_ALLOWLISTED = {"D49", "D54", "D60"}

# The phrase every staleness reason ends on. An entry promising that the next regeneration
# will fix it IS a staleness claim, whether or not whoever wrote it remembered this set.
_STALENESS_TELL = "after the next corpus regeneration"

_RUN2 = "out/live/run2.json"
_RUN18 = "out/live/run18.json"

# D49's compensating payload, shaped the way plan.py's hyperlocal mapping emits one today.
# Module-level because two things read it: the demonstration below and
# TestD49SpecificallyReachesReality, which must not drift from it.
_D49_CURRENT_SHAPE = {"market_sizing": {
    "scale": "hyperlocal", "radius_m": 3000, "catchment_km2": 28.27,
    "trade_area_households": 8872, "households_sourced": True,
    "tam": {"mid": 3.5e7}}}


def _load_one(path):
    """A report and its page, loaded the way the sweep loads one."""
    result = (json.load(open(path)) or {}).get("result") or {}
    html_path = path[:-5] + ".html"
    html = (open(html_path, encoding="utf-8").read()
            if os.path.exists(html_path) else None)
    return result, html


def _load_reports():
    return [(os.path.basename(p), *_load_one(p)) for p in _CORPUS]


def _d49_on_a_current_shape_payload():
    """No run on disk can carry D49's keys — plan.py began writing them after the last one —
    so the artifact has to be what the mapping produces today."""
    from gates import d49_trade_area_matches_its_radius as d49
    return d49(_D49_CURRENT_SHAPE, None)


def _d54_on_the_live_run():
    """run2 is a genuine end-to-end run whose ledger stamps `produces`; the corpus's events
    predate the stamping, so its 83 events reconcile against nothing."""
    from gates import d54_produced_output_reaches_the_report as d54
    return d54(*_load_one(_RUN2))


def _d60_on_the_live_run():
    """run18 is the first run produced with the Economic Census anchor — the one shape in
    which D60 is applicable at all."""
    from gates import d60_area_average_is_labelled as d60
    return d60(*_load_one(_RUN18))


# gate -> (artifact it needs on disk, if any; the check that proves the gate still answers).
_STALENESS_DEMONSTRATIONS: dict[str, tuple[str | None, Callable[[], gates.Finding]]] = {
    "D49": (None, _d49_on_a_current_shape_payload),
    "D54": (_RUN2, _d54_on_the_live_run),
    "D60": (_RUN18, _d60_on_the_live_run),
}


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


class TestTheStalenessAllowlistPaysItsCompensatingCheck(unittest.TestCase):
    """"The corpus predates this key" is the one unfalsifiable excuse on the allowlist.

    Every other reason can be argued with by reading the corpus. This one cannot — a key that
    was not being written when the reports were generated is genuinely absent, and no amount
    of looking will prove otherwise. Which is exactly why it is the reason a dead gate would
    reach for, and why the category has to cost something: a gate excused on staleness owes a
    verdict on an artifact that ISN'T stale.

    Not gated on the corpus being present, unlike the sweep above: this holds regardless.
    """

    def test_every_staleness_entry_is_on_the_allowlist_it_qualifies(self):
        orphans = sorted(_STALENESS_ALLOWLISTED - set(_KNOWN_UNREACHABLE))
        self.assertEqual(orphans, [],
                         "these gates claim the staleness exemption but are not allowlisted "
                         f"as unreachable at all, so the exemption exempts nothing: {orphans}")

    def test_an_entry_that_blames_the_corpus_is_categorised_as_staleness(self):
        """The leak. The allowlist is prose and this set is opt-in, so an entry could plead
        staleness in its reason and never join the set that owes an artifact — which is the
        blanket excuse the category exists to refuse."""
        uncategorised = sorted(gid for gid, why in _KNOWN_UNREACHABLE.items()
                               if _STALENESS_TELL in why
                               and gid not in _STALENESS_ALLOWLISTED)
        self.assertEqual(uncategorised, [],
                         "these entries expect the next corpus regeneration to fix them — "
                         "that is a staleness claim — but are not in _STALENESS_ALLOWLISTED, "
                         f"so nothing makes them prove reachability elsewhere: {uncategorised}")

    def test_every_staleness_entry_registers_a_demonstration(self):
        undemonstrated = sorted(_STALENESS_ALLOWLISTED - set(_STALENESS_DEMONSTRATIONS))
        self.assertEqual(undemonstrated, [],
                         "allowlisted for staleness with no compensating check registered in "
                         f"_STALENESS_DEMONSTRATIONS: {undemonstrated}. Either point one at a "
                         "current-shape payload or a live run, or the gate is simply dead.")

    def test_no_demonstration_outlives_the_entry_it_compensates_for(self):
        """A gate whose corpus caught up no longer needs one — leaving it here would keep
        proving a guarantee the sweep now proves properly."""
        stale = sorted(set(_STALENESS_DEMONSTRATIONS) - _STALENESS_ALLOWLISTED)
        self.assertEqual(stale, [],
                         f"demonstrations for gates no longer excused on staleness: {stale}")

    def test_each_demonstration_yields_a_real_verdict(self):
        """The check itself, and the only one of these that runs a gate: True or False on a
        payload of the current shape. None here means the compensating artifact has drifted
        out of shape too, and the gate is now unproven on BOTH."""
        for gid in sorted(_STALENESS_ALLOWLISTED & set(_STALENESS_DEMONSTRATIONS)):
            artifact, demonstrate = _STALENESS_DEMONSTRATIONS[gid]
            with self.subTest(gate=gid):
                if artifact and not os.path.exists(artifact):
                    self.skipTest(f"{gid}'s artifact {artifact} is not on disk")
                self.assertIsNotNone(
                    demonstrate().ok,
                    f"{gid} is excused from corpus reachability because the corpus is stale, "
                    "and declines to answer on a current-shape payload as well — so nothing "
                    "anywhere shows it can fire")


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestD49SpecificallyReachesReality(unittest.TestCase):
    """The gate that prompted the rule. Pinned by name, because a regression here means the
    372x county-scale error is unguarded again."""

    def test_d49_returns_a_real_verdict_on_a_current_shape_payload(self):
        """The compensating check for the staleness allowlist: the stored corpus cannot
        contain keys added after it was generated, so reachability is proven against what
        the pipeline produces TODAY. A verdict of True or False — never None."""
        from gates import d49_trade_area_matches_its_radius as d49
        current = _D49_CURRENT_SHAPE
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
