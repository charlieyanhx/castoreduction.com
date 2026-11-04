"""
size_hyperlocal fetched local income and sized every neighbourhood on the national average.

MEASURED before the change. `acs_demographics` returned median_hh_income at tract AND county
level on every hyperlocal run, and nothing in the repo read it — the only other occurrence was a
test asserting the value existed. TAM_local was households x $3,945, the BLS CEX *national*
all-consumer-units average (CXUFOODAWAYLB0101M), so a cafe in a $32k-median tract and one in a
$250k-median tract got identical per-household spend.

The arithmetic behind the fix lives in skills/sizing/spend_index.py and is tested in
test_local_spend_index.py (integrate the CEX quintile curve over the ACS B19001 bracket
distribution, ratio-anchored to the national integral). THIS file tests the WIRING: that the
adjustment fires when it can, refuses out loud when it cannot, and never silently becomes 1.0.

WHY THE SCOPE IS WHAT IT IS — each boundary was measured, not chosen for taste. A patched copy
of hyperlocal.py was run against the three end-to-end hyperlocal test files (178 passing) under
four variants:

    V1  adjust only when local income is present, record a reason when absent   178 pass, 0 fail
    V2  V1 + ratchet confidence down when income is absent                      1 failure
    V3  V2 + also adjust a caller-provided spend                                2 failures
    V4  fail loud: no income -> no spend -> no TAM                              9 failures

V1 shipped. V4 is not a test-update problem but a design error: it destroys the invariant
(cycle36, and audit high #4's fix) that a geocode or ACS failure must STILL produce a
trade-area-scaled TAM rather than collapsing to nothing. V3 was rejected because a
caller-provided spend is an explicit override and must not be second-guessed. V2's single
failure is real but out of scope: using the national average is exactly what the pipeline did
before, so it is not newly less confident.

The decisive fact that shaped all of it: ZERO existing tests supply ACS median_hh_income. Every
ACS stub in the suite returns {"households": N}, so without this file the adjusted path would
have no coverage at all and 100% of hyperlocal tests would silently exercise only the fallback.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.registry import Evidence

# The real BLS CEX 2024 curve, measured live: CXUFOODAWAY/CXUINCBEFTX LB0102..LB0106.
CURVE_POINTS = [[16658.0, 1655.0], [42925.0, 2448.0], [74474.0, 3277.0],
                [121548.0, 4682.0], [264510.0, 7652.0]]
NATIONAL_SPEND = 3945.0

# Real ACS 2022 5-yr B19001_002E.._017E. Counts reconcile exactly to B11001_001E households.
US_BRACKETS = [6192080.0, 4743710.0, 4235151.0, 4587937.0, 4701374.0, 4608052.0,
               4479480.0, 4465681.0, 4518761.0, 8700174.0, 11528244.0, 16085302.0,
               12442523.0, 9024401.0, 11075396.0, 14348087.0]
US_AGGREGATE = 13307060156700.0
US_HOUSEHOLDS = 125736353.0

# Mission District tract 06/075/022901 — the venture this program has been tracking.
MISSION_BRACKETS = [27.0, 92.0, 26.0, 39.0, 59.0, 109.0, 37.0, 17.0, 24.0, 60.0,
                    100.0, 125.0, 122.0, 138.0, 119.0, 333.0]
MISSION_AGGREGATE = 256171300.0
MISSION_HOUSEHOLDS = 1427.0
MISSION_MEDIAN = 96964.0


def _ev(payload, **kw):
    return Evidence(source="t", category="geo", count=1, payload=payload, **kw)


def _dist_payload(brackets, aggregate, households, level, median=None):
    return {"bracket_households": list(brackets), "aggregate_income": aggregate,
            "households": households, "median_hh_income": median, "level": level,
            "vintage": 2022, "source": f"US Census ACS 5-yr 2022 B19001/B19025 ({level})"}


class _Harness(unittest.TestCase):
    """Runs the real size_hyperlocal with the tool layer stubbed to real-shaped payloads."""

    def _run(self, *, curve=None, curve_error=None, local_dist="mission",
             national_dist=True, tract="022901", fips=("06", "075"),
             address="2000 Mission St, San Francisco CA", spend=(NATIONAL_SPEND, True),
             caller_spend=None):
        from skills.sizing import hyperlocal as H

        def fake_get_tool(name):
            class _T:
                pass
            t = _T()
            if name == "geocode_address":
                t.fn = lambda addr: _ev({
                    "lat": 37.76, "lng": -122.42,
                    "state_fips": fips[0] if fips else None,
                    "county_fips": fips[1] if fips else None,
                    "tract": tract, "matched_address": addr})
            elif name == "acs_demographics":
                t.fn = lambda **kw: _ev({"households": 1427.0,
                                         "median_hh_income": MISSION_MEDIAN,
                                         "level": "tract" if kw.get("tract") else "county"})
            elif name == "census_land_area":
                t.fn = lambda **kw: _ev({"land_km2": 1.6})
            elif name == "poi_competition":
                t.fn = lambda **kw: Evidence(source="t", category="geo", count=12,
                                             payload={"count": 12})
            elif name == "cex_income_quintile_curve":
                if curve_error:
                    t.fn = lambda **kw: _ev(None, skeleton=True, error=curve_error)
                else:
                    t.fn = lambda **kw: _ev({
                        "points": curve if curve is not None else CURVE_POINTS,
                        "all_units_spend": NATIONAL_SPEND, "all_units_income": 104207.0,
                        "vintage": "2024", "from_cache": False,
                        "source": "BLS Consumer Expenditure Survey 2024"})
            elif name == "acs_income_distribution":
                def _dist(**kw):
                    is_national = not (kw.get("state_fips") and kw.get("county_fips"))
                    if is_national:
                        if not national_dist:
                            return _ev(None, skeleton=True, error="ACS national unavailable")
                        return _ev(_dist_payload(US_BRACKETS, US_AGGREGATE, US_HOUSEHOLDS,
                                                 "us", 75149.0))
                    if local_dist is None:
                        return _ev(None, skeleton=True, error="ACS local unavailable")
                    if local_dist == "empty":
                        return _ev(_dist_payload([0.0] * 16, 0.0, 0.0, "tract", None))
                    return _ev(_dist_payload(MISSION_BRACKETS, MISSION_AGGREGATE,
                                             MISSION_HOUSEHOLDS, "tract", MISSION_MEDIAN))
                t.fn = _dist
            else:
                t.fn = lambda **kw: _ev({})
            return t

        with patch.object(H, "get_tool", side_effect=fake_get_tool), \
             patch.object(H, "resolve_annual_spend", return_value=spend), \
             patch.object(H, "_estimate_unit_revenue", return_value=1_500_000.0), \
             patch.object(H, "_estimate_households", return_value=2142.0):
            return H.size_hyperlocal(address=address, radius_m=3000,
                                     annual_spend_per_hh=caller_spend)

    @staticmethod
    def _adj(ev):
        return ((ev.payload or {}).get("spend_income_adjustment")) or {}

    @staticmethod
    def _tam_fig(ev):
        figs = (ev.payload or {}).get("figures") or []
        return next((f for f in figs if f.get("label") == "TAM_local"), None)


class TestTheAdjustmentFires(_Harness):
    def test_a_richer_tract_raises_spend_above_the_national_average(self):
        ev = self._run()
        adj = self._adj(ev)
        self.assertTrue(adj.get("applied"), f"adjustment did not fire: {adj.get('reason')}")
        self.assertGreater(adj["multiplier"], 1.0,
                           "a tract 29% above the national median got no uplift")
        self.assertGreater((ev.payload or {})["spend_per_hh_usd"], NATIONAL_SPEND)

    def test_the_multiplier_matches_the_measured_mission_value(self):
        """Measured 1.150 when built. Pinned so a curve or bracket change is visible rather
        than quietly repricing every hyperlocal report."""
        adj = self._adj(self._run())
        self.assertAlmostEqual(adj["multiplier"], 1.150, delta=0.02,
                               msg=f"Mission multiplier drifted to {adj['multiplier']:.4f}")

    def test_the_adjusted_spend_is_the_national_figure_times_the_multiplier(self):
        """One owner per number: the adjusted spend must be reproducible from the two
        quantities the report publishes, not an independently-computed third value."""
        ev = self._run()
        adj = self._adj(ev)
        self.assertAlmostEqual((ev.payload or {})["spend_per_hh_usd"],
                               adj["national_spend"] * adj["multiplier"], places=6)

    def test_the_income_geography_matches_the_household_geography(self):
        """A tract-derived household count multiplied by a county-derived income would mix
        scales. Whatever geography won the density race must be the one priced."""
        ev = self._run()
        self.assertEqual((ev.payload or {}).get("density_geography"), "tract")
        self.assertEqual(self._adj(ev).get("geography"), "tract")

    def test_the_formula_shows_the_national_figure_and_the_multiplier(self):
        """Publishing only the adjusted number would present it as if BLS had published it."""
        fig = self._tam_fig(self._run())
        self.assertIn("national", fig["formula"])
        self.assertIn("income index", fig["formula"])
        self.assertIn("3,945", fig["formula"])

    def test_the_source_string_names_both_sources(self):
        fig = self._tam_fig(self._run())
        self.assertIn("BLS", fig["source"])
        self.assertIn("ACS", fig["source"])

    def test_the_tam_still_claims_census_origin(self):
        """D53: an adjusted figure is MORE sourced, not less. Under-claiming is the bug the
        gate caught last time."""
        self.assertEqual(self._tam_fig(self._run()).get("data_origin"), "census")

    def test_the_tam_equals_households_times_the_adjusted_spend(self):
        ev = self._run()
        p = ev.payload or {}
        self.assertAlmostEqual(p["tam_usd"], p["trade_area_households"] * p["spend_per_hh_usd"],
                               delta=1.0)


class TestItRefusesOutLoudAndKeepsTheTam(_Harness):
    """Every refusal must (a) say why, (b) leave spend at the national figure, and (c) still
    publish a trade-area TAM. Losing the TAM here would undo audit high #4's fix."""

    def _refused(self, ev, needle=None):
        adj = self._adj(ev)
        self.assertFalse(adj.get("applied"), "adjustment fired when it should have refused")
        self.assertTrue(adj.get("reason"), "refused without recording a reason")
        if needle:
            self.assertIn(needle, adj["reason"].lower(),
                          f"reason does not name the cause: {adj['reason']}")
        self.assertEqual((ev.payload or {}).get("spend_per_hh_usd"), NATIONAL_SPEND)
        self.assertIsNotNone((ev.payload or {}).get("tam_usd"),
                             "a failed income lookup cost the run its TAM")
        return adj

    def test_no_fips_refuses(self):
        self._refused(self._run(fips=None, tract=None), "fips")

    def test_an_unavailable_bls_curve_refuses(self):
        self._refused(self._run(curve_error="BLS REQUEST_NOT_PROCESSED: daily threshold"),
                      "curve unavailable")

    def test_an_unavailable_local_distribution_refuses(self):
        self._refused(self._run(local_dist=None), "local acs")

    def test_an_unavailable_national_reference_refuses(self):
        """Without the anchor there is no ratio — and defaulting the anchor to the local
        value would silently yield exactly 1.0."""
        self._refused(self._run(national_dist=False), "national")

    def test_an_empty_local_distribution_refuses(self):
        self._refused(self._run(local_dist="empty"))

    def test_a_one_point_curve_refuses_instead_of_extrapolating(self):
        self._refused(self._run(curve=[[74474.0, 3277.0]]))

    def test_a_caller_provided_spend_is_never_second_guessed(self):
        ev = self._run(caller_spend=5000.0)
        adj = self._adj(ev)
        self.assertFalse(adj.get("applied"))
        self.assertEqual((ev.payload or {}).get("spend_per_hh_usd"), 5000.0)

    def test_an_unsourced_llm_spend_is_not_adjusted(self):
        """Multiplying a guess by a real multiplier launders the guess."""
        ev = self._run(spend=(4100.0, False))
        self.assertFalse(self._adj(ev).get("applied"))
        self.assertEqual((ev.payload or {}).get("spend_per_hh_usd"), 4100.0)

    def test_a_non_us_address_refuses_because_the_sources_are_us_only(self):
        """ACS and BLS CEX are US-only. Scaling a Lisbon cafe by a US income curve would be
        worse than not adjusting, and the repo already knows how to detect this."""
        self._refused(self._run(address="Rua da Prata 100, Lisbon, Portugal"), "non-us")

    def test_a_raising_tool_does_not_abort_sizing(self):
        from skills.sizing import hyperlocal as H
        with patch.object(H, "adjust_spend_for_local_income",
                          wraps=H.adjust_spend_for_local_income):
            with patch("skills.sizing.spend_index.local_spend_multiplier",
                       side_effect=RuntimeError("boom")):
                ev = self._run()
        adj = self._adj(ev)
        self.assertFalse(adj.get("applied"))
        self.assertIn("errored", (adj.get("reason") or "").lower())
        self.assertIsNotNone((ev.payload or {}).get("tam_usd"))


class TestTheRecordIsAlwaysPresent(_Harness):
    """THE recurring bug class: absence reading as success. `spend_income_adjustment` must be
    present on EVERY hyperlocal payload so a reader can never mistake a national average for a
    local one by finding nothing."""

    def test_it_is_recorded_when_applied(self):
        self.assertIn("spend_income_adjustment", self._run().payload)

    def test_it_is_recorded_when_refused(self):
        self.assertIn("spend_income_adjustment", self._run(fips=None, tract=None).payload)

    def test_the_spend_and_its_source_are_always_published(self):
        for kw in ({}, {"fips": None, "tract": None}, {"local_dist": None}):
            with self.subTest(**kw):
                p = self._run(**kw).payload or {}
                self.assertIsNotNone(p.get("spend_per_hh_usd"))
                self.assertTrue(p.get("spend_per_hh_source"))

    def test_a_refusal_reason_is_never_the_empty_string(self):
        for kw in ({"fips": None, "tract": None}, {"local_dist": None},
                   {"national_dist": False}, {"curve_error": "x"}, {"local_dist": "empty"}):
            with self.subTest(**kw):
                adj = self._adj(self._run(**kw))
                self.assertGreater(len(adj.get("reason") or ""), 10,
                                   f"unactionable reason for {kw}: {adj.get('reason')!r}")


class TestACallerNumberDoesNotLaunderIntoCensusProvenance(_Harness):
    """A pre-existing bug found in the exact path this change touches, and fixed here.

    MEASURED before the fix, by execution: size_hyperlocal(annual_spend_per_hh=99999.0) —
    an absurd $99,999 per household — shipped TAM_local as

        source      "US Census ACS 5-yr 2022 + TIGERweb land area (tract density × catchment)
                     + caller-provided"
        data_origin "census"
        D53         Finding(ok=True, '1 agency citation(s), each with a proven origin')

    The gate that exists to refuse fabricated agency citations PASSED an arbitrary caller
    number dressed as Census data. The cause was one flag answering two questions:
    `spend_is_sourced` meant both "reliable enough to keep confidence high" and "published by
    an agency", and the caller branch set it True for the first sense while `_tam_origin` read
    it in the second. `spend_origin` now answers the provenance question separately."""

    def test_a_caller_provided_spend_is_not_census_origin(self):
        fig = self._tam_fig(self._run(caller_spend=99999.0))
        self.assertEqual(fig.get("data_origin"), "mixed",
                         "a caller's number is still being stamped as agency-sourced")

    def test_d53_no_longer_passes_it(self):
        from gates import d53_no_fabricated_agency_citation as d53
        p = self._run(caller_spend=99999.0).payload
        self.assertIsNot(d53({"market_sizing": p}, None).ok, True,
                         "D53 still approves a caller number as an agency citation")

    def test_a_genuine_bls_spend_keeps_census_origin(self):
        """The fix must not over-correct: households from ACS plus spend from BLS IS census,
        and under-claiming it is the D53 mirror-image bug."""
        self.assertEqual(self._tam_fig(self._run()).get("data_origin"), "census")

    def test_an_llm_spend_with_census_households_is_mixed(self):
        self.assertEqual(self._tam_fig(self._run(spend=(4100.0, False))).get("data_origin"),
                         "mixed")


class TestTheDisclosureSurvivesTheReportMapping(unittest.TestCase):
    """plan.py maps the engine payload into the stored report through an explicit ALLOWLIST, and
    that allowlist is where produced numbers go to die. Its own comment records the last time:
    trade-area scale keys were dropped there, so D49 was not-applicable on all 16 stored reports
    while its unit tests passed. D56 would have repeated it one function later.

    So this asserts the mapping itself, by reading plan.py's returned dict — not by trusting
    that a key added to the engine reaches the report."""

    def test_the_hyperlocal_mapping_carries_the_spend_disclosure(self):
        import inspect

        import plan
        src = inspect.getsource(plan)
        # Locate the hyperlocal mapping by a key only it publishes, then require the spend keys
        # in the same dict literal.
        self.assertIn('"trade_area_households": p.get("trade_area_households")', src)
        for key in ("spend_income_adjustment", "spend_per_hh_usd", "spend_per_hh_source"):
            self.assertIn(f'"{key}": p.get("{key}")', src,
                          f"plan.py drops {key} on the way to the report — D56 will fail on "
                          "every fresh run while these tests pass")

    def test_the_engine_actually_publishes_those_keys(self):
        """The other half: a mapping that carries keys the engine never sets is equally
        useless, and asserting only the mapping would not catch it. This executes the engine."""
        h = TestTheAdjustmentFires("test_a_richer_tract_raises_spend_above_the_national_average")
        p = h._run().payload or {}
        for key in ("spend_income_adjustment", "spend_per_hh_usd", "spend_per_hh_source"):
            self.assertIn(key, p, f"size_hyperlocal does not publish {key}")


class TestD56(unittest.TestCase):
    """The gate. MEASURED across 16 stored corpus reports + 5 live runs: 7 False, 14 None,
    0 True — reachable with no `_KNOWN_UNREACHABLE` entry, because it triggers on keys the
    stale corpus already carries (`method == "trade_area_catchment"` plus a published TAM)
    rather than on the key this change added, which is the mistake that put D49 on the
    staleness allowlist.

    The 7 Falses are correct, not false alarms: those reports each shipped a trade-area TAM
    priced at the $3,945 national average with nothing said about it.

    0 True is a real gap and is honest about itself — no stored artifact predates the change
    while carrying the record, so the passing state is proven here through executed code and
    will appear in stored output at the next corpus regeneration."""

    @staticmethod
    def _r(ms):
        return {"market_sizing": ms}

    def _d56(self, ms):
        from gates import d56_local_spend_is_grounded_or_says_it_is_not as d56
        return d56(self._r(ms), None)

    _BASE = {"method": "trade_area_catchment", "tam_usd": 8_450_190.0}

    def test_an_applied_adjustment_passes(self):
        f = self._d56(dict(self._BASE, spend_income_adjustment={
            "applied": True, "multiplier": 1.1504, "national_spend": 3945.0,
            "adjusted_spend": 4538.15, "geography": "tract"}))
        self.assertIs(f.ok, True, f.detail)

    def test_a_disclosed_refusal_passes(self):
        f = self._d56(dict(self._BASE, spend_income_adjustment={
            "applied": False,
            "reason": "no Census FIPS for the address, so no local income distribution"}))
        self.assertIs(f.ok, True, f.detail)

    def test_a_missing_record_fails(self):
        """The measured state of all 7 pre-change hyperlocal artifacts."""
        self.assertIs(self._d56(dict(self._BASE)).ok, False)

    def test_an_empty_record_fails(self):
        self.assertIs(self._d56(dict(self._BASE, spend_income_adjustment={})).ok, False)

    def test_a_refusal_with_no_reason_fails(self):
        for bad in (None, "", "   ", "nope"):
            with self.subTest(reason=bad):
                self.assertIs(self._d56(dict(self._BASE, spend_income_adjustment={
                    "applied": False, "reason": bad})).ok, False)

    def test_an_unusable_multiplier_fails(self):
        for bad in (None, 0, -1.2, "1.15"):
            with self.subTest(multiplier=bad):
                self.assertIs(self._d56(dict(self._BASE, spend_income_adjustment={
                    "applied": True, "multiplier": bad, "national_spend": 3945.0,
                    "adjusted_spend": 4538.0, "geography": "tract"})).ok, False)

    def test_an_applied_adjustment_without_a_geography_fails(self):
        self.assertIs(self._d56(dict(self._BASE, spend_income_adjustment={
            "applied": True, "multiplier": 1.15, "national_spend": 3945.0,
            "adjusted_spend": 4536.75})).ok, False)

    def test_arithmetic_that_does_not_reconcile_fails(self):
        """The self-refuting-number class: three figures published side by side where the
        product of two does not equal the third."""
        f = self._d56(dict(self._BASE, spend_income_adjustment={
            "applied": True, "multiplier": 1.15, "national_spend": 3945.0,
            "adjusted_spend": 9000.0, "geography": "tract"}))
        self.assertIs(f.ok, False)
        self.assertIn("reconcile", f.detail)

    def test_a_non_hyperlocal_report_is_not_applicable(self):
        self.assertIsNone(self._d56({"method": "top_down_share", "tam_usd": 1e9}).ok)

    def test_a_trade_area_sizing_with_no_tam_is_not_applicable(self):
        self.assertIsNone(self._d56({"method": "trade_area_catchment"}).ok)

    def test_the_not_applicable_set_cannot_swallow_the_real_failure(self):
        """The repo's dominant bug is a gate that passes vacuously when its subject is absent.
        A published trade-area TAM with no disclosure must be False, never None."""
        f = self._d56(dict(self._BASE))
        self.assertIsNotNone(f.ok, "an undisclosed local TAM read as not-applicable")
        self.assertIs(f.ok, False)

    def test_the_report_shape_with_tam_as_a_range_is_also_covered(self):
        """plan.py reshapes market_sizing so the TAM arrives as tam.mid, not tam_usd. A gate
        that only knew one shape would go vacuous on real shipped reports."""
        f = self._d56({"method": "trade_area_catchment", "tam": {"mid": 8_450_190.0}})
        self.assertIs(f.ok, False, "tam.mid shape was not recognised as a published TAM")

    def test_it_runs_on_every_stored_artifact_without_raising(self):
        import glob
        import json
        seen = {True: 0, False: 0, None: 0}
        for p in sorted(glob.glob("out/wave4_corpus/*.json")) + sorted(glob.glob("out/live/*.json")):
            r = (json.load(open(p)) or {}).get("result") or {}
            from gates import d56_local_spend_is_grounded_or_says_it_is_not as d56
            seen[d56(r, None).ok] += 1
        self.assertGreater(seen[False] + seen[True], 0,
                           "D56 is vacuous on every stored report — it would never fire")


if __name__ == "__main__":
    unittest.main()
