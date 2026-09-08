"""
The shipped report credited its most load-bearing section to the wrong function.

MEASURED on run6..run11: the "How each section was produced" table said

    Market size | mixed | estimate_market_size | market_sizing

while the run's own append-only ledger — and gate D52 — recorded size_hyperlocal at
skills/sizing/hyperlocal.py as the producer. estimate_market_size is the legacy national
engine that skills/sizing superseded for this venture class; the static SECTION_SOURCES map
was never updated, and a declared map cannot drift-check itself. The table whose whole
purpose is telling a reader what produced each section was itself wrong about it.

Its own comment (section_provenance.py:46) already said the recorded producer should win
"wherever one exists" — the intent existed, the wiring did not. Now build_section_provenance
overlays report/trace.recorded_producers: where the ledger has a skill record for a section,
its name and module replace the declaration, attribution="recorded", and an overridden
declaration stays VISIBLE as declared_producer — drift is surfaced, never papered over.
"""
from __future__ import annotations

import json
import os
import unittest

from report.section_provenance import build_section_provenance


def _run11():
    if not os.path.exists("out/live/run11.json"):
        return None
    return (json.load(open("out/live/run11.json")) or {}).get("result") or {}


class TestTheLedgerWins(unittest.TestCase):
    def test_market_size_is_credited_to_the_function_that_ran(self):
        r = _run11()
        if r is None:
            self.skipTest("run11 not present")
        rows = {e["result_key"]: e for e in build_section_provenance(r)}
        ms = rows.get("market_sizing")
        self.assertIsNotNone(ms, "no Market size row at all")
        self.assertEqual(ms["produced_by"], "size_hyperlocal",
                         f"the table still credits {ms['produced_by']!r} — the drifted "
                         "declaration is back in front of the reader")
        self.assertEqual(ms.get("attribution"), "recorded")

    def test_the_overridden_declaration_stays_visible(self):
        """Silently swapping the name would hide that the map drifted — the drift is itself
        information (the map needs updating, and until then every pre-ledger artifact shows
        the old name)."""
        r = _run11()
        if r is None:
            self.skipTest("run11 not present")
        rows = {e["result_key"]: e for e in build_section_provenance(r)}
        self.assertEqual(rows["market_sizing"].get("declared_producer"),
                         "estimate_market_size")

    def test_sections_with_no_ledger_record_keep_the_declaration_and_say_so(self):
        r = _run11()
        if r is None:
            self.skipTest("run11 not present")
        rows = build_section_provenance(r)
        declared = [e for e in rows if e.get("attribution") == "declared"]
        self.assertTrue(declared, "every section claims a ledger record — plan.py's inline "
                                  "LLM sections have no @skill producer, so some must not")
        for e in declared:
            self.assertNotIn("declared_producer", e)

    def test_an_agreeing_record_does_not_invent_a_drift_marker(self):
        r = _run11()
        if r is None:
            self.skipTest("run11 not present")
        for e in build_section_provenance(r):
            if e.get("attribution") == "recorded" and "declared_producer" in e:
                self.assertNotEqual(e["produced_by"], e["declared_producer"],
                                    f"{e['result_key']}: drift marker present but the names "
                                    "agree")

    def test_a_result_with_no_trace_still_builds(self):
        """Pre-ledger artifacts and unit fixtures must keep working on declarations alone."""
        rows = build_section_provenance({"market_sizing": {"tam_usd": 1.0}})
        self.assertTrue(rows)
        self.assertEqual(rows[0].get("attribution"), "declared")


if __name__ == "__main__":
    unittest.main()
