"""R5 (88b416f6 audit): a failed fetch is UNAVAILABLE, never "returned no data".

MEASURED: the requests-cache bug nulled every Reddit call (20/20 skeleton) and one
each of stackexchange/devto/lobsters/vertical_pubs. The report rendered that outage
as "Only 0 of 6 queried customer-voice sources returned data — opinion signals are
thin" and docked 0.10 confidence — infrastructure failure presented as a research
result, the codebase's own recurring absence-read-as-answer mistake (#70 fixed
skipped-by-design; this fixes asked-and-FAILED).

Contract pinned: raw source functions return None on transport failure and [] only
for a genuine empty result (the reddit_search (results, error) precedent); the
Evidence wrappers turn None into skeleton+error; the multisrc persist records
unavailable sources; and the validation flag reports outages separately without
docking confidence for them.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestRawSourcesReturnNoneOnTransportFailure(unittest.TestCase):
    def setUp(self):
        # These sources are @cached; the cache is bypassed so a prior run's poison
        # (including this test's own RED run) can never decide a verdict.
        self._nc = patch("cache.get", return_value=None)
        self._np = patch("cache.put")
        self._nc.start(); self._np.start()
        self.addCleanup(self._nc.stop)
        self.addCleanup(self._np.stop)

    def test_stackexchange_transport_failure_is_none(self):
        import tools.sources.forums as f
        with patch.object(f.mrp_http, "get", side_effect=OSError("down")):
            self.assertIsNone(f.stackexchange_mentions("Acme"))

    def test_stackexchange_http_error_is_none(self):
        import tools.sources.forums as f
        with patch.object(f.mrp_http, "get",
                          return_value=MagicMock(status_code=503)):
            self.assertIsNone(f.stackexchange_mentions("Acme"))

    def test_stackexchange_empty_success_stays_a_list(self):
        import tools.sources.forums as f
        ok = MagicMock(status_code=200)
        ok.json.return_value = {"items": []}
        with patch.object(f.mrp_http, "get", return_value=ok):
            self.assertEqual(f.stackexchange_mentions("Acme"), [])

    def test_devto_and_lobsters_and_vertical_follow_the_same_contract(self):
        import tools.sources.articles as a
        with patch.object(a.mrp_http, "get", side_effect=OSError("down")):
            self.assertIsNone(a.devto_mentions("Acme"))
            self.assertIsNone(a.lobsters_mentions("Acme"))
        import tools.sources.vertical as v
        with patch("scrape.search.search", side_effect=OSError("down")):
            self.assertIsNone(v.vertical_publication_mentions("Acme", "logistics"))


class TestEvidenceWrappersNameTheFailure(unittest.TestCase):
    def test_none_becomes_skeleton_with_error(self):
        import tools.customer_voice as cv
        with patch("sources.stackexchange_mentions", return_value=None):
            ev = cv.stackexchange_mentions("Acme")
        self.assertTrue(ev.skeleton)
        self.assertIn("unavailable", (ev.error or "").lower())

    def test_empty_list_is_count_zero_not_skeleton(self):
        import tools.customer_voice as cv
        with patch("sources.stackexchange_mentions", return_value=[]):
            ev = cv.stackexchange_mentions("Acme")
        self.assertFalse(ev.skeleton)
        self.assertEqual(ev.count, 0)


class TestTheFlagSeparatesOutageFromThinness(unittest.TestCase):
    def _r(self, unavailable):
        return {
            "_steps_completed": ["viability"],
            "viability": {"viability_score": 50},
            "reddit_signal": {"threads_found": 0},
            "hn_signal": {"hits_found": 0},
            "multi_source_signal": {
                "counts": {"stackoverflow": 0, "devto": 0, "lobsters": 0,
                           "vertical_pubs": 0},
                "queried": {"stackoverflow": True, "devto": True, "lobsters": True,
                            "vertical_pubs": True},
                "unavailable": unavailable,
            },
        }

    def test_all_sources_down_is_an_outage_flag_and_shrinks_the_denominator(self):
        from plan import _validation_gate
        out = _validation_gate(self._r(
            {"stackoverflow": True, "devto": True, "lobsters": True,
             "vertical_pubs": True}))
        joined = " ".join(out["flags"])
        self.assertIn("unavailable", joined.lower())
        # reddit+hn genuinely returned empty, so a thin flag over those TWO working
        # sources stays honest — but the four dead ones must not pad the denominator
        # (the 88b416f6 report said "0 of 6").
        self.assertNotIn("of 6", joined)
        self.assertIn("4 customer-voice source(s) unavailable", joined)

    def test_fetched_and_genuinely_empty_still_reads_thin(self):
        from plan import _validation_gate
        out = _validation_gate(self._r({}))
        self.assertIn("opinion signals are thin", " ".join(out["flags"]))


if __name__ == "__main__":
    unittest.main()
