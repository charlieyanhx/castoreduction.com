"""Treadle phase 1: every kind of report goes through one door.

Before this, "run a report" meant "call plan.run_plan", and every surface that started a
run named market research by hand. A second report type had nowhere to go. Now a report
type is a declaration in a registry and `treadle.run_report(key, brief)` is the door.

Two packs prove the door is general rather than market research with a new name:

  travel_guide     declares its sections; the engine orders, bounds and verifies them.
  market_research  brings its own runner (plan.run_plan, unedited); the engine dispatches.

And one property proves the split holds: importing the engine, or the travel guide, does
not import a single market-research module.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
import unittest

import treadle
from core.section import FAILED, NOT_APPLICABLE, OK, Section
from treadle import ReportType, run_report
from treadle.registry import REPORT_TYPES, load_pack

FOOD_TRIP = {"city": "Lisbon", "days": 3, "interests": ["food", "architecture"],
             "diet": "none", "currency": "EUR"}
BUSINESS_TRIP = {"city": "Lisbon", "days": 1, "interests": ["meetings"], "currency": "EUR"}


class AReportTypeIsWellFormed(unittest.TestCase):
    def test_it_declares_sections_or_a_runner(self):
        with self.assertRaises(ValueError):
            ReportType(key="nothing", label="Nothing")

    def test_not_both(self):
        with self.assertRaises(ValueError):
            ReportType(key="both", label="Both", sections=lambda b: [],
                       runner=lambda b, progress=None: {})

    def test_the_key_is_a_slug(self):
        for bad in ("", "Has Space", "UPPER", "dash-ed", "travel_guide\n"):
            with self.assertRaises(ValueError, msg=bad):
                ReportType(key=bad, label="x", sections=lambda b: [])

    def test_a_key_registers_once(self):
        rt = ReportType(key="twice_for_test", label="x", sections=lambda b: [])
        treadle.register(rt)
        try:
            with self.assertRaises(ValueError):
                treadle.register(rt)
        finally:
            del REPORT_TYPES["twice_for_test"]

    def test_an_unknown_type_names_what_is_known(self):
        load_pack("travel_guide")
        with self.assertRaises(KeyError) as ctx:
            run_report("no_such_report", {})
        self.assertIn("travel_guide", str(ctx.exception))


class ASectionPackRunsThroughTheEngine(unittest.TestCase):
    def setUp(self):
        load_pack("travel_guide")

    def test_the_food_trip_assembles_every_section(self):
        report = run_report("travel_guide", FOOD_TRIP)
        self.assertEqual(report.type, "travel_guide")
        self.assertTrue(report.ok)
        statuses = {s["key"]: s["status"] for s in report.sections["sections"]}
        self.assertEqual(set(statuses.values()), {OK}, statuses)
        self.assertEqual(report.result["brief"], FOOD_TRIP)

    def test_a_section_the_brief_did_not_ask_for_is_not_applicable(self):
        report = run_report("travel_guide", BUSINESS_TRIP)
        statuses = {s["key"]: s["status"] for s in report.sections["sections"]}
        self.assertEqual(statuses["restaurants"], NOT_APPLICABLE)
        self.assertTrue(report.ok, "not applicable is an answer, not a failure")

    def test_the_brief_cannot_be_rewritten_by_the_run(self):
        brief = {"city": "Lisbon", "days": 2, "interests": ["food"]}
        run_report("travel_guide", brief)
        self.assertEqual(brief, {"city": "Lisbon", "days": 2, "interests": ["food"]})

    def test_a_failed_section_makes_the_report_not_ok(self):
        def boom(ctx):
            raise RuntimeError("no data")
        rt = ReportType(key="broken_for_test", label="x",
                        sections=lambda b: [Section(key="only", produce=boom)])
        treadle.register(rt)
        try:
            report = run_report("broken_for_test", {})
        finally:
            del REPORT_TYPES["broken_for_test"]
        self.assertFalse(report.ok)
        self.assertEqual(report.sections["sections"][0]["status"], FAILED)

    def test_progress_is_streamed_per_section(self):
        seen = []
        run_report("travel_guide", FOOD_TRIP, progress=seen.append)
        self.assertEqual(len(seen), 5)


class TheProductIsDispatchedNotRewritten(unittest.TestCase):
    def setUp(self):
        load_pack("market_research")

    def test_the_brief_reaches_run_plan_as_its_own_arguments(self):
        import plan
        calls = []

        def fake_run_plan(description, **kw):
            calls.append((description, kw))
            return {"profile": {"description": description}}

        real, plan.run_plan = plan.run_plan, fake_run_plan
        try:
            report = run_report("market_research",
                                {"description": "a bakery in Lisbon", "geo": "PT",
                                 "effort": "deep"}, progress=print)
        finally:
            plan.run_plan = real
        self.assertEqual(calls, [("a bakery in Lisbon",
                                  {"geo": "PT", "effort": "deep", "progress": print})])
        self.assertEqual(report.result, {"profile": {"description": "a bakery in Lisbon"}})
        self.assertIsNone(report.sections, "the product assembles itself; nothing to claim")

    def test_a_run_that_returns_an_error_is_not_ok(self):
        import plan
        real, plan.run_plan = plan.run_plan, lambda description, **kw: {"error": "x"}
        try:
            report = run_report("market_research", {"description": "d"})
        finally:
            plan.run_plan = real
        self.assertFalse(report.ok)

    def test_an_unknown_brief_field_is_refused_before_the_run(self):
        with self.assertRaises(ValueError) as ctx:
            run_report("market_research", {"description": "d", "colour": "blue"})
        self.assertIn("colour", str(ctx.exception))

    def test_a_brief_without_a_description_is_refused(self):
        with self.assertRaises(ValueError):
            run_report("market_research", {"geo": "US"})

    def test_the_adapter_knows_every_argument_run_plan_takes(self):
        # If run_plan grows a parameter, the adapter must learn it or refuse it knowingly.
        import plan
        from packs.market_research import BRIEF_FIELDS
        params = set(inspect.signature(plan.run_plan).parameters) - {"progress"}
        self.assertEqual(set(BRIEF_FIELDS), params)


class TheEngineDoesNotImportTheProduct(unittest.TestCase):
    SOUL = ("plan", "four_ps", "discover", "intake", "intake_tree", "orchestrator",
            "business_model", "market_sizing", "financials", "customer_universe", "api")

    def _loaded_after(self, code: str) -> list[str]:
        probe = (f"import sys; {code}; "
                 f"print(','.join(m for m in {self.SOUL!r} if m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                             check=True).stdout.strip()
        return [m for m in out.split(",") if m]

    def test_importing_treadle_loads_no_market_research(self):
        self.assertEqual(self._loaded_after("import treadle"), [])

    def test_running_the_travel_guide_loads_no_market_research(self):
        self.assertEqual(self._loaded_after(
            "import treadle; treadle.load_pack('travel_guide'); "
            "treadle.run_report('travel_guide', {'city': 'X', 'days': 1, 'interests': []})"),
            [])

    def test_registering_market_research_does_not_run_or_import_it(self):
        # Listing what reports exist must not cost the 35k-line domain import.
        self.assertEqual(self._loaded_after(
            "import treadle; treadle.load_pack('market_research')"), [])


if __name__ == "__main__":
    unittest.main()
