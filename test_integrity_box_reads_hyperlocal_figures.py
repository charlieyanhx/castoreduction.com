"""
The "how to trust these numbers" box told run9's buyer there were no sources at all.

MEASURED on out/live/run9.html: directly above TAM math that is genuinely Census ACS x
TIGERweb x BLS — three live federal fetches, figures stamped data_origin census/derived/osm in
the same JSON — the report's one trust panel rendered:

    "Sourced: 0/0 — no sources at all · Model-estimated origins: llm"

THE CAUSE: plan.build_integrity_summary counts only the NATIONAL triangulation shape —
tam.method_top_down / method_bottom_up / method_analog. A hyperlocal (trade_area_catchment)
report has none of those; its sourcing lives in market_sizing.figures[].data_origin, which the
function never read. So methods=[] -> n_total=0 -> "0/0", and origins=[] -> the template's
'llm' fallback. The box was built for one sizing shape and is vacuously wrong on the other —
the D53 under-claiming class (a real Census figure presented as unsourced) surfacing in the UI,
where it damages trust most: a skeptical buyer reads "no sources at all" and stops.

THE FIX: when the sizing method is trade_area_catchment, ground the provenance counts in
figures[].data_origin. 'derived' counts toward n_total but NOT n_grounded — SAM is arithmetic
on the TAM, and claiming arithmetic as a fetch would be the OVER-claiming mirror image.
The national 3-method path is untouched (its behaviour is pinned by test_integrity_summary).
"""
from __future__ import annotations

import json
import os
import unittest

from plan import build_integrity_summary


def _hyperlocal_result():
    return {"market_sizing": {
        "method": "trade_area_catchment",
        "tam": {"mid": 308_700_000.0},
        "figures": [
            {"label": "TAM_local", "value_usd": 308_700_000.0, "data_origin": "census"},
            {"label": "SAM_local", "value_usd": 108_000_000.0, "data_origin": "derived"},
            {"label": "SOM_demand", "value_usd": 630_000.0, "data_origin": "osm"},
        ],
        "validation": {"passed": True, "blocks": [], "warns": []},
    }}


class TestHyperlocalSourcingIsCounted(unittest.TestCase):
    def test_grounded_figures_are_no_longer_invisible(self):
        s = build_integrity_summary(_hyperlocal_result())
        prov = s["provenance"]
        self.assertGreater(prov["n_total"], 0,
                           "hyperlocal figures still invisible — the box will say 0/0 again")
        self.assertGreaterEqual(prov["n_grounded"], 2,
                                f"census+osm figures not counted as grounded: {prov}")

    def test_derived_counts_toward_total_but_not_grounded(self):
        """SAM is arithmetic on the TAM. Calling arithmetic a fetch is the over-claiming
        mirror of the bug being fixed."""
        s = build_integrity_summary(_hyperlocal_result())
        prov = s["provenance"]
        self.assertEqual(prov["n_total"], 3)
        self.assertEqual(prov["n_grounded"], 2)

    def test_origins_list_the_real_stamps(self):
        s = build_integrity_summary(_hyperlocal_result())
        self.assertIn("census", s["data_origins"])
        self.assertIn("osm", s["data_origins"])

    def test_the_grounded_flag_is_true_for_a_census_tam(self):
        self.assertTrue(build_integrity_summary(_hyperlocal_result())["grounded"])

    def test_run9s_real_artifact_no_longer_reads_as_unsourced(self):
        """The shipped defect, pinned on the stored artifact itself."""
        if not os.path.exists("out/live/run9.json"):
            self.skipTest("run9 not present")
        r = (json.load(open("out/live/run9.json")) or {}).get("result") or {}
        s = build_integrity_summary(r)
        self.assertGreater(s["provenance"]["n_total"], 0,
                           "run9 still renders 'Sourced: 0/0 — no sources at all' above "
                           "Census-grounded TAM math")
        self.assertTrue(s["grounded"])
        self.assertIn("census", s["data_origins"])

    def test_an_llm_only_hyperlocal_report_is_still_reported_as_unsourced(self):
        """The fix must not over-correct: a hyperlocal run whose figures are all LLM
        estimates has no grounding and the box must keep saying so."""
        r = _hyperlocal_result()
        for f in r["market_sizing"]["figures"]:
            f["data_origin"] = "llm"
        s = build_integrity_summary(r)
        self.assertEqual(s["provenance"]["n_grounded"], 0)
        self.assertFalse(s["grounded"])

    def test_figures_without_origin_stamps_do_not_count_as_grounded(self):
        r = _hyperlocal_result()
        for f in r["market_sizing"]["figures"]:
            f.pop("data_origin", None)
        s = build_integrity_summary(r)
        self.assertEqual(s["provenance"]["n_grounded"], 0)


class TestTheNationalPathIsUntouched(unittest.TestCase):
    def test_three_method_shape_counts_exactly_as_before(self):
        r = {"market_sizing": {"tam": {
            "mid": 5e9,
            "method_top_down": {"value_usd": 5e9, "source": "IBISWorld",
                                "data_origin": "census"},
            "method_bottom_up": {"value_usd": 4e9, "source": "", "data_origin": "llm"},
            "method_analog": {"value_usd": 6e9, "source": "Gartner", "data_origin": "llm"},
        }}}
        s = build_integrity_summary(r)
        self.assertEqual(s["provenance"]["n_total"], 3)
        self.assertEqual(s["provenance"]["n_grounded"], 1)
        self.assertEqual(s["provenance"]["n_cited"], 2)


if __name__ == "__main__":
    unittest.main()
