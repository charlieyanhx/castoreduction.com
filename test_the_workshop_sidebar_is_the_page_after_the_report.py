"""The workshop sidebar is what the owner gets after the report.

THE DESIGN (owner, 2026-09-14): post-generation is an editing, polishing and workshop
session, and its interface is a sidebar chat. The founder asks the analyst who wrote the
report; a selected passage becomes an Explain (one credit) or a Note (free, kept for the
rewrite); a Rewrite runs the writing again (ten credits); a Re-run recomputes the facts
(one included, then a report credit). One pool pays for all of it and the balance is
always on screen. The refine layer it replaces (a rules bar, five marks and five questions
with their own packs, a batch answer button, a six-minute regeneration as the only way
forward) is gone from the page.

What is pinned here is the SHAPE, not the styling: who gets the sidebar, that its markup
carries every id its script reaches for, that nothing in it types a price, and that the
scripts it ships parse. The behaviour behind it is tested at the routes.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).parent
_FIXTURE = _HERE / "tests" / "fixtures" / "synthesis" / "diag01_result.json"
_OPUS_REPORT = _HERE / "tests" / "fixtures" / "synthesis" / "diag01_opus.md"


def _result() -> dict:
    r = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    r["synthesis"] = {"markdown": _OPUS_REPORT.read_text(encoding="utf-8"),
                      "model": "claude-opus-5", "style": "full"}
    return r


def _render(**kw) -> str:
    from report.render_html import render_report_html
    return render_report_html(_result(), job_id="j-sidebar", **kw)


class TheOwnerGetsIt(unittest.TestCase):
    def test_the_owner_page_carries_the_sidebar_and_its_toggle(self):
        page = _render(annotate=1)
        self.assertIn('id="ws"', page)
        self.assertIn('class="chrome-act chrome-act--primary ws-tog"', page)
        self.assertIn('id="wsSel"', page, "the Explain / Note pill for a selected passage")

    def test_a_published_copy_and_the_sample_do_not(self):
        """annotate=0 is a stranger reading a published copy or the sample; public=1 is the
        library. Neither is a page anybody can work on, so neither ships the workshop."""
        for kw in ({"annotate": 0}, {"annotate": 0, "public": 1}, {"annotate": 1, "public": 1}):
            page = _render(**kw)
            self.assertNotIn('id="ws"', page, kw)
            self.assertNotIn("ws-tog", page, kw)

    def test_the_refine_layer_is_gone(self):
        page = _render(annotate=1)
        for old in ('id="rfTop"', 'id="rfAnswer"', 'id="rfBuyMarks"', 'id="rfBuyQs"',
                    'id="rfAgainSec"', "Refine mode", "0 of 5 marks"):
            self.assertNotIn(old, page, old)

    def test_the_share_offer_and_the_feedback_box_stay(self):
        page = _render(annotate=1)
        self.assertIn('id="shareBlock"', page)
        self.assertIn('id="feedbackBlock"', page)
        self.assertIn("initShare();", page, "drawn once the owner is known, not on finalize")


class TheMarkupMatchesTheScript(unittest.TestCase):
    """Every $("wsX") the script reaches for is an element the markup has. A renamed id
    is a control that silently does nothing, which is the failure this page has had
    before (rfAnswer existed in the copy and had no caller for weeks)."""

    def setUp(self):
        self.tpl = (_HERE / "templates" / "workshop.html").read_text(encoding="utf-8")
        self.js = (_HERE / "web" / "workshop.js").read_text(encoding="utf-8")
        self.css = (_HERE / "web" / "workshop.css").read_text(encoding="utf-8")

    def test_every_id_the_script_uses_exists(self):
        script = self.js
        markup = self.tpl
        used = set(re.findall(r'\$\("(ws[A-Za-z0-9]+)"\)', script))
        self.assertGreater(len(used), 20)
        for id_ in sorted(used):
            if id_ in ("wsPending", "wsBuy"):
                continue                       # created by the script itself
            self.assertIn(f'id="{id_}"', markup, f"$(\"{id_}\") has no element")

    def test_the_verbs_the_founder_can_reach(self):
        for needle in ('id="wsForm"', 'id="wsInput"', 'id="wsSend"',       # ask
                       'data-verb="explain"', 'data-verb="note"',         # on a passage
                       'id="wsRewrite"', 'id="wsStyle"',                  # rewrite
                       'id="wsRerun"', 'id="wsRerunGo"',                  # re-run
                       'id="wsBal"', 'id="wsOffer"'):                     # the pool
            self.assertIn(needle, self.tpl, needle)

    def test_no_price_or_cost_is_typed_into_the_page(self):
        """The costs, the pack and the balance ride GET /iteration. The page draws what it
        is told, so repricing a verb is a constant in iteration.py, not a redeploy."""
        for typed in ("$5", "30 credits", "10 credits", "· 10", "· 1<"):
            self.assertNotIn(typed, self.tpl + self.js, typed)
        for read in ("st.workshop.costs", "st.workshop.pack", "st.workshop.balance",
                     'cost("rerun"'):
            self.assertIn(read, self.js, read)

    def test_the_page_hands_the_script_its_constants(self):
        """The script is a static file; the job id, the survey facts, the current style
        and whether there is a written report at all come from the template."""
        self.assertIn("window.WORKSHOP = {", self.tpl)
        for key in ("job:", "facts:", "style:", "hasWriting:"):
            self.assertIn(key, self.tpl, key)
        self.assertIn('<script src="/workshop.js" defer>', self.tpl)
        self.assertIn('<link rel="stylesheet" href="/workshop.css">', self.tpl)
        self.assertIn("window.WORKSHOP", self.js)

    def test_the_sidebar_never_prints(self):
        self.assertIn('class="ws no-print"', self.tpl)
        self.assertIn("@media print", self.css)

    def test_no_em_dashes(self):
        for text in (self.tpl, self.js, self.css):
            self.assertNotIn(chr(0x2014), text)
            self.assertNotIn(chr(0x2013), text)

    def test_reduced_motion_is_honoured(self):
        self.assertIn("prefers-reduced-motion", self.css)


@unittest.skipUnless(shutil.which("node"), "node is not installed here")
class TheScriptsParse(unittest.TestCase):
    """A template with this much JavaScript can ship a syntax error the server never
    sees; the browser then loads a page with a dead sidebar. node --check is the cheapest
    guard there is."""

    def test_every_script_on_the_owner_page_parses(self):
        page = _render(annotate=1)
        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        self.assertGreaterEqual(len(scripts), 2)
        scripts.append((_HERE / "web" / "workshop.js").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as d:
            for i, js in enumerate(scripts):
                f = Path(d) / f"s{i}.js"
                f.write_text(js, encoding="utf-8")
                r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"script {i}: {r.stderr[:600]}")

    def test_the_assets_are_served(self):
        from fastapi.testclient import TestClient
        import api
        c = TestClient(api.app)
        for path, kind in (("/workshop.js", "javascript"), ("/workshop.css", "css")):
            r = c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn(kind, r.headers.get("content-type", ""), path)
            self.assertIn("no-cache", r.headers.get("cache-control", ""), path)


if __name__ == "__main__":
    unittest.main()
