"""
Rank 13 of the R4 fix order: validation.warns rendered nowhere (9/16).

`validate_numbers` computes advisory `warns` ("grounded estimate 135,000,000 and
modeled estimate 1,500,000 diverge 11.1x — at least one is wrong"; "SOM estimates
diverge 97%") and stores them — but the template rendered only the hard `blocks`, and
only when `passed == false`. So 7 reports showed a green "✓ Validated — passed the
integrity gate" chip directly over a stored, unrendered 11x-divergence warning.

The fix renders the warns list whenever it is non-empty, and the integrity chip turns
amber "Validated with warnings" (not green) when advisory warnings exist. Gate d36
fails a report whose stored warns are absent from the HTML or sit under a plain-green
Validated chip.
"""
from __future__ import annotations

import glob
import json
import re
import unittest

from jinja2 import Environment, FileSystemLoader

import api

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


def _render_marked(marker_start, marker_end, **ctx):
    env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                      undefined=api.SafeUndefined)
    src = env.loader.get_source(env, "report.html")[0]
    start = src.index(marker_start)
    end = src.index(marker_end)
    html = env.from_string(src[start:end]).render(**ctx)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class TestWarnsRender(unittest.TestCase):
    def test_warns_are_rendered_when_present(self):
        ms = {"validation": {"passed": True, "warns": [
            {"msg": "grounded estimate 135,000,000 and modeled estimate 1,500,000 "
                    "diverge 11.1x — at least one is wrong"}]},
              "error": None}
        html = _render_marked("<!-- VALIDATION WARNS -->",
                              "<!-- END VALIDATION WARNS -->", market_sizing=ms)
        self.assertIn("diverge 11.1x", html)
        self.assertIn("at least one is wrong", html)

    def test_no_warns_no_block(self):
        ms = {"validation": {"passed": True, "warns": []}, "error": None}
        html = _render_marked("<!-- VALIDATION WARNS -->",
                              "<!-- END VALIDATION WARNS -->", market_sizing=ms)
        self.assertEqual(html, "")


class TestGateD36(unittest.TestCase):
    _WARN = {"msg": "SOM estimates diverge 97% (demand 9,389 vs supply 280,000)"}

    def _r(self):
        return {"market_sizing": {"validation": {"passed": True, "warns": [self._WARN]}}}

    def test_warn_absent_from_html_fails(self):
        import gates
        html = "<h2>Market Size</h2><span>✓ Validated</span>"
        f = gates.d36_validation_warns_surfaced(self._r(), html)
        self.assertIs(f.ok, False)

    def test_green_validated_chip_over_a_warn_fails(self):
        import gates
        html = ("<span>✓ Validated<span>passed the integrity gate</span></span>"
                "<li>SOM estimates diverge 97% (demand 9,389 vs supply 280,000)</li>")
        f = gates.d36_validation_warns_surfaced(self._r(), html)
        self.assertIs(f.ok, False)
        self.assertIn("chip", f.detail.lower())

    def test_rendered_warn_and_amber_chip_passes(self):
        import gates
        html = ("<span>Validated with warnings</span>"
                "<li>SOM estimates diverge 97% (demand 9,389 vs supply 280,000)</li>")
        self.assertIs(gates.d36_validation_warns_surfaced(self._r(), html).ok, True)

    def test_na_without_warns(self):
        import gates
        r = {"market_sizing": {"validation": {"passed": True, "warns": []}}}
        self.assertIsNone(gates.d36_validation_warns_surfaced(r, "<html></html>").ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D36", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_stored_reports_hide_their_warns(self):
        import gates
        n_fail = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            html = open(f[:-5] + ".html", encoding="utf-8", errors="replace").read()
            if gates.d36_validation_warns_surfaced(r, html).ok is False:
                n_fail += 1
        self.assertGreaterEqual(n_fail, 5)


if __name__ == "__main__":
    unittest.main()
