"""
W6-1: report/verifier.py — the pre-publication verification pass, with a seeded suite.

gates.py runs the 22 deterministic invariants as a CI-style sweep across a CORPUS: it
tells a developer whether a batch of reports is healthy. It has never run inside a
single run, before that run's report is handed to a buyer.

That is what this is. One entry point over one report, findings ranked by severity,
each naming the invariant that fired and what the buyer would have read.

## Why the suite is seeded into a REAL report

The first draft of this file hand-built a "clean" report dict. It was wrong in a way
worth recording: the fixture used `market_sizing.tam_usd` where the pipeline actually
emits `market_sizing.tam.mid`, `competitor_density` at the top level where it lives
under `discover`, and so on. Seven detectors saw nothing to check and returned N/A —
the suite passed for the wrong reason, proving only that the fixture matched itself.

So each case seeds ONE defect into a REAL corpus report and asserts the DELTA: the
finding was absent before the mutation and present after. That is schema-faithful by
construction, and it cannot be satisfied by a detector that fires on everything.
"""
from __future__ import annotations

import copy
import glob
import json
import os
import unittest

from report.verifier import Severity, verify_report

# Only COMPLETE pairs. A corpus regen writes the .json before the .html, so a
# half-written record would be picked as a base whose HTML is None — and every
# html-reading detector would return N/A, silently making those cases vacuous.
def _complete(pattern):
    return [p for p in sorted(glob.glob(pattern)) if os.path.exists(p[:-5] + ".html")]


_CORPUS = _complete("out/wave4_corpus/*.json") or _complete("out/wave2_corpus/*.json")


def _load(path):
    d = json.load(open(path))
    r = d.get("result") or d
    hp = path[:-5] + ".html"
    html = open(hp, encoding="utf-8", errors="replace").read() if os.path.exists(hp) else None
    return r, html


def _pick(predicate, without=None):
    """First corpus report matching `predicate` that does NOT already report
    `without` — otherwise the seeded case would pass on a finding it didn't cause."""
    for p in _CORPUS:
        r, html = _load(p)
        if not predicate(r, html):
            continue
        if without and without in ids(verify_report(r, html)):
            continue
        return r, html
    return None, None


def ids(res):
    return {f.invariant for f in res.findings}


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class SeededCase(unittest.TestCase):
    """Base: assert a mutation ADDS a specific finding that wasn't there before."""

    def assert_seeds(self, base, html, mutate, invariant, mutate_html=None):
        before = ids(verify_report(base, html))
        self.assertNotIn(invariant, before,
                         f"{invariant} already fires on the unmutated report — "
                         "this case proves nothing")
        r = copy.deepcopy(base)
        mutate(r)
        after = ids(verify_report(r, mutate_html(html) if mutate_html else html))
        self.assertIn(invariant, after,
                      f"{invariant} not caught after seeding; got {sorted(after)}")


class TestSeededBugs(SeededCase):
    """Defects this program has actually shipped. Each must be caught."""

    # 1 — audit M4/M5: subscription framing rendered on a never-recurring venture.
    def test_bug01_subscription_bleed_into_the_rendered_report(self):
        base, html = _pick(lambda r, h: h and r.get("business_model_kind") == "marketplace"
                           and "/mo per " not in h, without="D06")
        if base is None:
            self.skipTest("no marketplace report with clean HTML")
        self.assert_seeds(base, html, lambda r: None, "D06",
                          mutate_html=lambda h: h.replace("</body>",
                                                          "<p>$185/mo per account</p></body>"))

    # 2 — funnel ordering: SOM above SAM.
    def test_bug02_som_exceeds_sam(self):
        base, html = _pick(lambda r, h: (r.get("market_sizing") or {}).get("som", {}).get("mid"), without="D04")
        if base is None:
            self.skipTest("no report with a full funnel")

        def m(r):
            ms = r["market_sizing"]
            ms["som"]["mid"] = ms["tam"]["mid"] * 10
        self.assert_seeds(base, html, m, "D04")

    # 3 — B1: viability reasons from a competitor count nothing produced.
    def test_bug03_density_contradicts_the_ranked_set(self):
        base, html = _pick(lambda r, h: (r.get("discover") or {}).get("competitor_density"), without="D16")
        if base is None:
            self.skipTest("no report with a density")

        def m(r):
            r["discover"]["competitor_density"] = 1
        self.assert_seeds(base, html, m, "D16")

    # 4 — C2/D21: two 4Ps sections disagree on the average transaction value.
    def test_bug04_sections_disagree_on_price(self):
        base, html = _pick(lambda r, h: (r.get("four_ps") or {}).get("place", {}).get("narrative")
                           and r.get("business_model_kind") == "marketplace", without="D21")
        if base is None:
            self.skipTest("no marketplace report with a place narrative")

        def m(r):
            # Must match the detector's shape: "$X average booking".
            r["four_ps"]["place"]["narrative"] += (
                " Assume $99,999 average booking across the network.")
        self.assert_seeds(base, html, m, "D21")

    # 5 — R2: a formula whose arithmetic contradicts its own printed result.
    def test_bug05_formula_does_not_compute_to_its_value(self):
        base, html = _pick(lambda r, h: (r.get("market_sizing") or {}).get("tam", {}).get("mid"))
        if base is None:
            self.skipTest("no report with a TAM")

        def m(r):
            r["market_sizing"]["figures"] = [{
                "label": "top_down", "value_usd": 4_590_000_000, "source": "IBISWorld",
                "formula": "$30.6B US home services (IBISWorld 2023) * 15% "
                           "handyman share * 15% take rate = $4.59B"}]
        self.assert_seeds(base, html, m, "formula_reconciliation")

    # 6 — D22: viability prose invents a competitor count.
    def test_bug06_viability_reasoning_invents_a_count(self):
        base, html = _pick(lambda r, h: (r.get("discover") or {}).get("competitor_density", 0) > 4
                           and isinstance(r.get("viability"), dict), without="D22")
        if base is None:
            self.skipTest("no report with a dense competitor set")

        def m(r):
            r["viability"]["summary"] = ("With only two competitors in the category, "
                                         "entry is straightforward.")
        self.assert_seeds(base, html, m, "D22")

    # 7 — W4-2: a checkable claim carrying no citation marker at all.
    def test_bug07_uncited_dated_claim(self):
        base, html = _pick(lambda r, h: (r.get("four_ps") or {}).get("product", {}).get("narrative"))
        if base is None:
            self.skipTest("no report with a product narrative")

        def m(r):
            r["four_ps"]["product"]["narrative"] += (
                " The category grew 23% in 2024 across every segment.")
        # uncited_claims already fires on real reports (68% attribution), so assert
        # the COUNT rises rather than the presence of the invariant.
        before = len([f for f in verify_report(base, html).findings
                      if f.invariant == "uncited_claims"])
        r = copy.deepcopy(base)
        m(r)
        after = len([f for f in verify_report(r, html).findings
                     if f.invariant == "uncited_claims"])
        self.assertEqual(after, before + 1)

    # 8 — W4-2: a marker resolving to a citation that was never emitted.
    def test_bug08_dangling_citation_marker(self):
        base, html = _pick(lambda r, h: (r.get("four_ps") or {}).get("price", {}).get("narrative"))
        if base is None:
            self.skipTest("no report with a price narrative")

        def m(r):
            r["four_ps"]["price"]["narrative"] += " Pricing follows the benchmark⁹⁹."
        self.assert_seeds(base, html, m, "dangling_citations")

    # 9 — D09: validation failed but the report published anyway.
    def test_bug09_failed_validation_still_publishes(self):
        base, html = _pick(lambda r, h: isinstance(r.get("market_sizing"), dict),
                           without="D09")
        if base is None:
            self.skipTest("no report with sizing")

        def m(r):
            r["market_sizing"]["validation"] = {"passed": False,
                                                "blocks": [{"msg": "SOM > TAM"}]}
            r["market_sizing"]["publishable"] = True
        self.assert_seeds(base, html, m, "D09")

    # 10 — D15/audit C1: the SAM narrative cites a TAM that is not the headline.
    def test_bug10_tam_incoherent_across_sections(self):
        base, html = _pick(lambda r, h: (r.get("market_sizing") or {}).get("tam", {}).get("mid"),
                           without="D15")
        if base is None:
            self.skipTest("every corpus report already trips D15")

        def m(r):
            sam = r["market_sizing"]["sam"]
            # Must match the detector's shape: a "TAM $X" figure inside the SAM
            # derivation that disagrees with the headline tam.mid.
            sam["calculation"] = "TAM $77B * 3% serviceable share"
        self.assert_seeds(base, html, m, "D15")


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestSeverityAndOrdering(unittest.TestCase):
    def setUp(self):
        self.base, self.html = _load(_CORPUS[0])

    def test_findings_are_ranked_most_severe_first(self):
        sevs = [f.severity for f in verify_report(self.base, self.html).findings]
        self.assertEqual(sevs, sorted(sevs, key=Severity.rank))

    def test_a_blocking_finding_makes_the_report_unpublishable(self):
        r = copy.deepcopy(self.base)
        ms = r.get("market_sizing") or {}
        if not (ms.get("som") or {}).get("mid"):
            self.skipTest("no funnel to break")
        ms["som"]["mid"] = ms["tam"]["mid"] * 10
        self.assertFalse(verify_report(r, self.html).publishable)

    def test_every_finding_names_what_the_buyer_would_have_read(self):
        for f in verify_report(self.base, self.html).findings:
            self.assertTrue(f.detail.strip(), f"{f.invariant} reported no detail")

    def test_summary_counts_by_severity(self):
        s = verify_report(self.base, self.html).summary()
        self.assertEqual(set(s) - {"publishable", "coverage"},
                         {Severity.BLOCK, Severity.ADVISORY, Severity.INFO})

    def test_summary_also_reports_coverage(self):
        """`coverage` is load-bearing, not incidental: a Finding is only recorded when an
        invariant returns False, so a gate that could not answer was indistinguishable from
        one that passed. Measured: run_plan verified with html=None and 10 fail-severity
        gates were silently absent from every verdict."""
        s = verify_report(self.base, self.html).summary()
        self.assertIn("answered", s["coverage"])
        self.assertIn("not_applicable", s["coverage"])


class TestRobustness(unittest.TestCase):
    def test_an_empty_report_does_not_crash_the_verifier(self):
        self.assertIsNotNone(verify_report({}, None))

    def test_a_detector_that_raises_is_reported_not_fatal(self):
        """A crashing detector must not be able to hide the other twenty-one."""
        from unittest.mock import patch
        import report.verifier as v
        with patch.object(v, "_DETERMINISTIC", [("BOOM", _raises)]):
            res = v.verify_report({}, None)
        self.assertIn("BOOM", ids(res))

    def test_llm_review_is_off_unless_asked(self):
        from unittest.mock import patch
        with patch("report.verifier._llm_review") as m:
            verify_report({}, None)
        m.assert_not_called()

    def test_llm_review_runs_when_asked_and_its_failure_is_survivable(self):
        from unittest.mock import patch
        with patch("report.verifier._llm_review", side_effect=RuntimeError("api down")):
            res = verify_report({}, None, use_llm=True)
        self.assertIsNotNone(res)


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestPipelineWiring(unittest.TestCase):
    """The verifier only matters if the pipeline runs it and the report shows it."""

    def test_run_plan_attaches_a_verification_block(self):
        import inspect
        import plan
        src = inspect.getsource(plan.run_plan)
        self.assertIn("verify_report", src)
        self.assertIn('result["verification"]', src)

    def test_verification_renders_into_the_report(self):
        from jinja2 import Environment, FileSystemLoader
        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index("{% if verification and verification.summary %}")
        end = src.index("{% endif %}", src.index("{% endfor %}", start)) + len("{% endif %}")
        end = src.index("{% endif %}", end) + len("{% endif %}")
        tpl = env.from_string(src[start:end])
        html = tpl.render(verification={
            "summary": {"block": 1, "advisory": 2, "info": 0, "publishable": False},
            "findings": [{"invariant": "D16", "severity": "block",
                          "detail": "density=1 vs 9 ranked competitors"}]})
        self.assertIn("D16", html)
        self.assertIn("1 blocking", html)
        self.assertIn("density=1", html)

    def test_a_clean_verification_says_so(self):
        from jinja2 import Environment, FileSystemLoader
        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index("{% if verification and verification.summary %}")
        end = src.index("{% endif %}", src.index("{% endfor %}", start)) + len("{% endif %}")
        end = src.index("{% endif %}", end) + len("{% endif %}")
        html = env.from_string(src[start:end]).render(verification={
            "summary": {"block": 0, "advisory": 0, "info": 0, "publishable": True},
            "findings": []})
        self.assertIn("No blocking issue was found", html)

    def test_the_renderer_passes_verification_to_the_template(self):
        """The invariant is unchanged: the verification block must reach the template. Only
        its address moved — the render was extracted out of the FastAPI route into
        report/render_html.py so run_plan can render (and therefore verify) a real page
        before it ships. Asserting on the route's source would now pass vacuously."""
        import inspect

        from report import render_html
        self.assertIn('verification=r.get("verification")',
                      inspect.getsource(render_html.render_report_html))

    def test_the_route_still_reaches_that_renderer(self):
        """Guards the other half: an extracted renderer nothing calls is worse than an
        inline one."""
        import inspect

        import api
        self.assertIn("render_report_html",
                      inspect.getsource(api.get_job_report_html))


def _raises(r, html):
    raise RuntimeError("detector exploded")


if __name__ == "__main__":
    unittest.main()
