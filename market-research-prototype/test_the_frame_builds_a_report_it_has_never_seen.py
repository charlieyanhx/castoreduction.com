"""Is core/ a frame, or is it this pipeline with good names?

The claim made all session is that core/section.py is REUSABLE: declare sections, get
ordering, bounded reads, write isolation, per-section verdicts and three kinds of absence,
for any report. That claim has only ever been exercised by market research, which is the
one domain it was extracted from -- the weakest possible evidence.

So this file builds a report the frame has never seen. A CITY TRAVEL GUIDE: neighbourhoods,
a walking route that depends on them, seasonal advice, a restaurant list gated on the trip
being a food trip, and a budget that reads two of the others. Nothing here imports the
market-research domain, and nothing in core/ is modified to accommodate it.

If a guarantee only holds for market research, it is a coincidence of that pipeline and
this file will not be able to demonstrate it.
"""
from __future__ import annotations

import unittest

from core.section import (FAILED, FLAGGED, NOT_APPLICABLE, OK, SKIPPED, Section, assemble,
                          plan, stale_reads, summarise)

# --------------------------------------------------------------------------- the domain
#: A trip the guide is being written for. Run-scoped facts, bound into the declarations
#: the same way a venture's profile and effort levers are in orchestrator/sections.py.
FOOD_TRIP = {"city": "Lisbon", "days": 3, "interests": ["food", "architecture"]}
BUSINESS_TRIP = {"city": "Lisbon", "days": 1, "interests": ["meetings"]}


def _walk_is_reachable_on_foot(payload) -> str | None:
    """A walking route whose legs exceed a day of walking is not a walking route.

    A domain invariant that answers from one payload, exactly like viability's score
    reconciliation. The frame never learns what a kilometre is.
    """
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    legs = payload.get("legs") or []
    total = sum(float(l.get("km") or 0) for l in legs)
    if total > 20:
        return f"the route totals {total:.1f} km across {len(legs)} legs, which is not a walk"
    return None


def _every_stop_is_a_real_neighbourhood(payload) -> str | None:
    """The route may only name neighbourhoods the guide actually described."""
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    named = {l.get("area") for l in (payload.get("legs") or [])}
    known = set(payload.get("known_areas") or [])
    invented = sorted(n for n in named if n and n not in known)
    if invented:
        return (f"the route walks through {', '.join(invented)}, which the guide never "
                f"described; the reader is sent somewhere the report does not cover")
    return None


def guide_sections(trip: dict, *, produce_overrides: dict | None = None) -> list[Section]:
    """A whole report type, declared. Compare orchestrator/sections.py: same shape."""
    over = produce_overrides or {}

    def neighbourhoods(ctx):
        return {"areas": ["Alfama", "Chiado"]}

    def walking_route(ctx):
        areas = (ctx["neighbourhoods"] or {}).get("areas") or []
        return {"legs": [{"area": a, "km": 2.5} for a in areas], "known_areas": areas}

    def restaurants(ctx):
        return {"picks": ["Cervejaria Ramiro"], "count": 1}

    def seasonal(ctx):
        return {"note": "Aim for shoulder season."}

    def budget(ctx):
        n = len((ctx.get("restaurants") or {}).get("picks") or [])
        return {"eur_per_day": 60 + 20 * n, "based_on": sorted(ctx)}

    bodies = {"neighbourhoods": neighbourhoods, "walking_route": walking_route,
              "restaurants": restaurants, "seasonal": seasonal, "budget": budget}
    bodies.update(over)

    return [
        # Declared LAST but must assemble after what it reads -- the ordering claim.
        Section(key="budget", produce=bodies["budget"], consumes=("neighbourhoods",),
                optional=("restaurants",), label="What it costs"),
        Section(key="walking_route", produce=bodies["walking_route"],
                consumes=("neighbourhoods",), label="A day on foot",
                invariants=(("reachable_on_foot", _walk_is_reachable_on_foot),
                            ("stops_are_real", _every_stop_is_a_real_neighbourhood))),
        Section(key="neighbourhoods", produce=bodies["neighbourhoods"],
                label="Where to stay"),
        Section(key="seasonal", produce=bodies["seasonal"], label="When to go"),
        Section(key="restaurants", produce=bodies["restaurants"], label="Where to eat",
                inapplicable=lambda: None if "food" in (trip.get("interests") or []) else
                f"this guide is written for a {trip['days']}-day "
                f"{'/'.join(trip.get('interests') or ['general'])} trip, which did not ask "
                f"for restaurant coverage"),
    ]


# ---------------------------------------------------------------------------- the tests
class TestOrderingIsDerivedForADomainItNeverSaw(unittest.TestCase):
    def test_a_consumer_assembles_after_what_it_consumes(self):
        order = [s.key for s in plan(guide_sections(FOOD_TRIP))]
        self.assertLess(order.index("neighbourhoods"), order.index("walking_route"))
        self.assertLess(order.index("neighbourhoods"), order.index("budget"))
        self.assertLess(order.index("restaurants"), order.index("budget"),
                        "an optional input must still order the assembly")

    def test_declaration_order_does_not_decide_the_guide(self):
        """budget is declared FIRST and reads two others, so it cannot assemble first.

        Not "must be last": budget and walking_route become ready in the same batch, and
        the sort is stable, so among sections with nothing left to wait for the declared
        order is kept. That stability is the property the prompt cache depends on -- what
        is guaranteed is that a consumer never precedes what it consumes, not that a
        particular section lands at a particular index.
        """
        order = [s.key for s in plan(guide_sections(FOOD_TRIP))]
        self.assertNotEqual(order[0], "budget")
        self.assertGreater(order.index("budget"), order.index("neighbourhoods"))
        self.assertGreater(order.index("budget"), order.index("restaurants"))


class TestTheGuaranteesHoldOutsideMarketResearch(unittest.TestCase):
    def test_a_producer_sees_only_what_it_declared(self):
        seen = {}
        secs = guide_sections(FOOD_TRIP, produce_overrides={
            "budget": lambda ctx: seen.update(ctx) or {"eur_per_day": 80}})
        assemble(secs, {"secret_notes": "do not show the reader"})
        self.assertEqual(sorted(seen), ["neighbourhoods", "restaurants"])

    def test_a_producer_cannot_rewrite_a_section_that_already_landed(self):
        def vandal(ctx):
            ctx["neighbourhoods"]["areas"] = ["Somewhere Else"]
            return {"eur_per_day": 80}

        result = {}
        res = assemble(guide_sections(FOOD_TRIP, produce_overrides={"budget": vandal}),
                       result)
        self.assertEqual(result["neighbourhoods"]["areas"], ["Alfama", "Chiado"])
        budget = next(r for r in res if r.key == "budget")
        self.assertEqual(budget.status, FLAGGED)
        self.assertIn("context_is_read_only", budget.findings[0])

    def test_one_broken_section_does_not_lose_the_guide(self):
        def explode(ctx):
            raise RuntimeError("the transit API is down")

        res = assemble(guide_sections(FOOD_TRIP, produce_overrides={"seasonal": explode}),
                       {})
        by = {r.key: r for r in res}
        self.assertEqual(by["seasonal"].status, FAILED)
        self.assertIn("transit API is down", by["seasonal"].reason)
        self.assertEqual(by["walking_route"].status, OK)

    def test_a_missing_required_input_skips_and_names_it(self):
        def nothing(ctx):
            return {}

        res = assemble(guide_sections(FOOD_TRIP,
                                      produce_overrides={"neighbourhoods": nothing}), {})
        by = {r.key: r for r in res}
        self.assertEqual(by["walking_route"].status, SKIPPED)
        self.assertIn("neighbourhoods", by["walking_route"].reason)

    def test_an_optional_input_going_missing_still_produces(self):
        """restaurants is inapplicable on a business trip; budget must still be written."""
        res = assemble(guide_sections(BUSINESS_TRIP), {})
        by = {r.key: r for r in res}
        self.assertEqual(by["restaurants"].status, NOT_APPLICABLE)
        self.assertEqual(by["budget"].status, OK)


class TestDomainInvariantsRunAtSectionTime(unittest.TestCase):
    def test_an_unwalkable_walking_route_is_flagged_where_it_is_built(self):
        def marathon(ctx):
            areas = ctx["neighbourhoods"]["areas"]
            return {"legs": [{"area": a, "km": 40.0} for a in areas], "known_areas": areas}

        res = assemble(guide_sections(FOOD_TRIP,
                                      produce_overrides={"walking_route": marathon}), {})
        route = next(r for r in res if r.key == "walking_route")
        self.assertEqual(route.status, FLAGGED)
        self.assertIn("not a walk", route.findings[0])
        self.assertIn("80.0 km", route.findings[0], "the finding carries the real number")

    def test_a_route_through_an_undescribed_area_is_flagged(self):
        """The travel-guide spelling of the defect this codebase keeps finding: prose
        that names something the report never actually covered."""
        def invented(ctx):
            areas = ctx["neighbourhoods"]["areas"]
            return {"legs": [{"area": "Atlantis", "km": 1.0}], "known_areas": areas}

        res = assemble(guide_sections(FOOD_TRIP,
                                      produce_overrides={"walking_route": invented}), {})
        route = next(r for r in res if r.key == "walking_route")
        self.assertIn("Atlantis", route.findings[0])

    def test_a_healthy_guide_flags_nothing(self):
        self.assertTrue(all(r.status in (OK, NOT_APPLICABLE)
                            for r in assemble(guide_sections(FOOD_TRIP), {})))


class TestTheThreeKindsOfAbsenceSurviveTheDomainChange(unittest.TestCase):
    def test_a_business_trip_is_told_why_it_has_no_restaurants(self):
        res = assemble(guide_sections(BUSINESS_TRIP), {})
        r = next(x for x in res if x.key == "restaurants")
        self.assertEqual(r.status, NOT_APPLICABLE)
        self.assertIn("meetings", r.reason, "the reason names this trip, not trips at all")

    def test_the_producer_never_runs_for_an_inapplicable_section(self):
        ran = []
        assemble(guide_sections(BUSINESS_TRIP, produce_overrides={
            "restaurants": lambda ctx: ran.append(1) or {}}), {})
        self.assertEqual(ran, [])

    def test_the_summary_separates_all_of_them(self):
        def explode(ctx):
            raise RuntimeError("down")

        s = summarise(assemble(guide_sections(BUSINESS_TRIP,
                                              produce_overrides={"seasonal": explode}), {}))
        self.assertEqual((s[OK], s[NOT_APPLICABLE], s[FAILED]), (3, 1, 1))
        self.assertEqual(s["total"], 5)


class TestStaleReadsAreDetectedHereToo(unittest.TestCase):
    def test_a_section_rewritten_after_a_consumer_read_it_is_reported(self):
        """The hole `consumes` cannot close, in a domain that has never heard of
        financials rewriting economics: something outside the assembly edits a key a
        section already read."""
        result = {}
        res = assemble(guide_sections(FOOD_TRIP), result)
        result["neighbourhoods"] = {"areas": ["Alfama", "Chiado", "Belem"]}   # a late edit
        msgs = stale_reads(res, result)
        self.assertTrue(any("walking_route was assembled from neighbourhoods" in m
                            for m in msgs))
        self.assertTrue(any("budget was assembled from neighbourhoods" in m for m in msgs))

    def test_an_untouched_guide_reports_nothing(self):
        result = {}
        self.assertEqual(stale_reads(assemble(guide_sections(FOOD_TRIP), result), result),
                         [])


class TestNoneOfThisReachedIntoTheOtherDomain(unittest.TestCase):
    def test_this_file_imports_no_market_research(self):
        """If building a second report type needed the first one's modules, the frame is
        not a frame. The only import above core/ is this test's own domain, which is
        defined in this file."""
        import ast
        import pathlib
        import sys

        tree = ast.parse(pathlib.Path(__file__).read_text())
        mods = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                mods.append(n.module or "")
        outside = [m for m in mods
                   if m.split(".")[0] not in sys.stdlib_module_names
                   and m.split(".")[0] != "core"]
        self.assertEqual(outside, [], "the travel guide reached into another domain")


if __name__ == "__main__":
    unittest.main()
