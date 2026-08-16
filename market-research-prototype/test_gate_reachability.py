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
from typing import NamedTuple

import gates

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))

# Gates that are legitimately N/A on the CURRENT corpus. Each entry needs a reason and,
# ideally, the venture shape that would exercise it — an entry with no path to ever firing
# is a gate that should be deleted, not allowlisted.
#
# RETIRED, #98: D49 and D54 lived here saying "remove this entry after the next corpus
# regeneration", and three live runs on the free backend did it — a subscription venture
# (a kind the 16-report corpus did not contain AT ALL), a five-store chain, and a Lisbon
# bakery. D49 now answers on the chain's real trade area (51,643 households over 7.07 km²);
# D54 answers on the new runs' producer-stamped ledgers. Both are genuine NEW evidence.
#
# D60 stays, and the reason is worth stating because the tempting move is available and
# wrong: adding out/live/run18.json to the corpus WOULD clear it — but run18 IS D60's
# compensating demonstration. Promoting the same artifact from "demonstration" to "corpus"
# and then deleting the demonstration that pointed at it proves nothing new, relabels one
# file, and removes the portable floor that still runs on a fresh clone where out/ is
# gitignored. D60 needs a NEW run that anchors on the Economic Census.
_KNOWN_UNREACHABLE: dict[str, str] = {
    "D22": "its density-claim regex matches none of the phrasings the pipeline actually "
           "writes — 11/17 reports state a numeric competitor count in viability prose and "
           "the pattern misses all of them. A real defect in the DETECTOR, tracked "
           "separately; allowlisted so this rule does not block on a pre-existing bug.",
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
_STALENESS_ALLOWLISTED = {"D60"}

# The phrase every staleness reason ends on. An entry promising that the next regeneration
# will fix it IS a staleness claim, whether or not whoever wrote it remembered this set.
_STALENESS_TELL = "after the next corpus regeneration"

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




def _d60_on_a_current_shape_payload():
    """Borrowed from D60's own unit tests rather than restated here, so a change to the
    anchor's shape cannot leave a stale copy passing in this file."""
    from gates import d60_area_average_is_labelled as d60
    from test_d60_area_average_reaches_the_reader import _ANCHOR, _HONEST, _run
    return d60(_run(calculation=_HONEST, anchor=_ANCHOR), None)



def _d60_on_the_live_run():
    """run18 is the first run produced with the Economic Census anchor — the one shape in
    which D60 is applicable at all."""
    from gates import d60_area_average_is_labelled as d60
    return d60(*_load_one(_RUN18))


class _Demonstration(NamedTuple):
    """How a gate excused on staleness proves it can still answer.

    TWO ARTIFACTS, and the difference between them is the subject of this whole file.
    `constructed` is a payload of the shape the allowlist entry claims the pipeline writes
    today. `live` is a genuine end-to-end run. The live one is strictly stronger, because a
    constructed payload can only show the DETECTOR answers — it cannot show the PIPELINE
    emits that shape, which is the exact gap D49 fell through: its synthetic tests all passed
    while the gate was N/A on all 16 real reports.

    So the constructed payload is not the guarantee, it is the FLOOR. out/ is gitignored, so
    on a fresh clone the live half is simply unavailable and the floor is what remains
    standing. Where a live run can carry the shape, one is named and run; D49's cannot exist
    by construction, which is why it is allowlisted in the first place.
    """
    constructed: Callable[[], gates.Finding]
    live: tuple[str, Callable[[], gates.Finding]] | None = None


_STALENESS_DEMONSTRATIONS: dict[str, _Demonstration] = {
    "D60": _Demonstration(_d60_on_a_current_shape_payload, (_RUN18, _d60_on_the_live_run)),
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
        """The check itself: True or False on a payload of the current shape. None here means
        the compensating artifact has drifted out of shape too, and the gate is now unproven
        on BOTH. Runs everywhere — no artifact on disk, nothing to skip."""
        for gid in sorted(_STALENESS_ALLOWLISTED & set(_STALENESS_DEMONSTRATIONS)):
            with self.subTest(gate=gid):
                self.assertIsNotNone(
                    _STALENESS_DEMONSTRATIONS[gid].constructed().ok,
                    f"{gid} is excused from corpus reachability because the corpus is stale, "
                    "and declines to answer on a current-shape payload as well — so nothing "
                    "anywhere shows it can fire")

    def test_the_live_proof_still_holds_where_a_live_run_can_carry_the_shape(self):
        """The half a constructed payload cannot do: show the PIPELINE emits the shape, not
        just that the detector reads it. Skipped rather than failed when out/ is absent —
        it is gitignored, so a clone has no live runs — and that skip is the honest report
        that only the floor above is holding, not a pass."""
        for gid in sorted(gid for gid, d in _STALENESS_DEMONSTRATIONS.items() if d.live):
            artifact, demonstrate = _STALENESS_DEMONSTRATIONS[gid].live
            with self.subTest(gate=gid, artifact=artifact):
                if not os.path.exists(artifact):
                    self.skipTest(f"{artifact} is not on disk; {gid} rests on its "
                                  "constructed payload alone")
                self.assertIsNotNone(
                    demonstrate().ok,
                    f"{gid} returns N/A on {artifact} — a genuine run of the shape its "
                    "allowlist entry claims the pipeline now produces")


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
