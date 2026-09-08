"""
10 of 14 footnotes in the shipped report pointed at the wrong source.

MEASURED on out/live/run7.html (9 of 14 on run5 and run6). The verifier reported 2 dangling
markers; the true damage was 12, because a MISRESOLVED citation looks resolved.

THE MECHANISM. four_ps runs in _mode="split": each of the four sections gets its own LLM call
and its own citation list, whose ids restart at 1. four_ps.py pools them into four_ps.citations
PRESERVING those per-section ids — so the pooled list carries ids [1,2, 1,2,3,4, 1,2,3, 1,2,3].
templates/report.html then rendered ONE flat <ol> numbered by loop.index (1..12), IGNORING c.id.
Prose superscripts are per-section, so:

    section     markers      landed on <ol> item     should be
    product     1-4          1-2 (right), 3-4 dangle 1-2
    price       1-4          1-4  WRONG              3-6
    place       1-3          1-3  WRONG              7-9
    promotion   1-3          1-3  WRONG              10-12

Reader-visible: price's superscript 1 pointed at "Max-Diff Feature Importance Ranking" (a
product source) when it means "PSM Pricing Output". promotion's superscript 3 pointed at "PSM
Pricing Output" when it means "San Francisco Coffee Density Analysis".

WHY NO CHECK SAW IT: report/citation.audit_sections resolves each marker against its OWN
section's citation list (correct), while the template resolved against the pooled global index.
Detector and renderer disagreed about what "3" means, and the renderer is the one a buyer reads.

THE FIX: render one footnote group per section, with each item's VISIBLE number forced to the
section's own citation id (<li value=...>), so the reader's number space is the same one the
LLM wrote and the detector validates. One id space, one owner.

These tests EXECUTE the real template over real stored run data — not a source-string grep —
because the defect lived precisely in the gap between what the code intended and what rendered.
"""
from __future__ import annotations

import json
import os
import re
import unittest

SUP = "¹²³⁴⁵⁶⁷⁸⁹"


def _render_citations_block(four_ps: dict) -> str:
    """Render the real template's CITATIONS block with the real Jinja environment."""
    from jinja2 import Environment, FileSystemLoader

    import api
    env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                      undefined=api.SafeUndefined)
    src = env.loader.get_source(env, "report.html")[0]
    start = src.index("<!-- CITATIONS -->")
    end = src.index("<!-- FEEDBACK WIDGET -->")
    return env.from_string(src[start:end]).render(four_ps=four_ps)


def _section_markers(section: dict) -> list[int]:
    """Superscript marker ids in a section's prose, the way a reader encounters them."""
    text = " ".join([str(section.get("narrative") or "")]
                    + [str(t) for t in (section.get("key_takeaways") or [])])
    out = []
    for m in re.finditer(f"[{SUP}]+", text):
        out.append(int("".join(str(SUP.index(ch) + 1) for ch in m.group())))
    return out


def _rendered_footnote_map(html: str) -> dict[tuple[str, int], str]:
    """(section, visible_number) -> source text, as a reader would resolve a marker.

    Parses the per-section groups the fixed template emits: each <li> carries
    id="cite-{section}-{id}" and value="{id}". If the anchors are absent (the old flat list),
    returns {} — which correctly fails every resolution assertion, because under the old
    rendering the reader HAS no per-section number space.
    """
    import html as _html
    out: dict[tuple[str, int], str] = {}
    for m in re.finditer(
            r'<li[^>]*id="cite-([a-z]+)-(\d+)"[^>]*>(.*?)</li>', html, re.S):
        section, num, body = m.group(1), int(m.group(2)), m.group(3)
        text = _html.unescape(re.sub(r"<[^>]+>", " ", body))
        out[(section, num)] = re.sub(r"\s+", " ", text).strip()
    return out


def _load_four_ps(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    r = (json.load(open(path)) or {}).get("result") or {}
    fp = r.get("four_ps")
    return fp if isinstance(fp, dict) and fp.get("citations") else None


class TestEveryResolvableMarkerLandsOnItsOwnSectionsSource(unittest.TestCase):
    """The core property, executed over the real stored runs: a marker N in section S must
    resolve to the citation with id N in section S — never to another section's footnote."""

    RUNS = ["out/live/run5.json", "out/live/run6.json", "out/live/run7.json"]

    def test_real_runs_resolve_per_section(self):
        checked = 0
        for path in self.RUNS:
            fp = _load_four_ps(path)
            if fp is None:
                continue
            html = _render_citations_block(fp)
            rendered = _rendered_footnote_map(html)
            for sect in ("product", "price", "place", "promotion"):
                d = fp.get(sect) or {}
                own = {int(c["id"]): str(c.get("source") or "")
                       for c in (d.get("citations") or []) if c.get("id") is not None}
                for marker in _section_markers(d):
                    if marker not in own:
                        continue          # dangling — the verifier's job, not the renderer's
                    checked += 1
                    key = (sect, marker)
                    self.assertIn(key, rendered,
                                  f"{path} {sect}: marker {marker} has no footnote rendered "
                                  f"in its own section's group")
                    want = own[marker]
                    if want:              # the fabricated empty-source citation has no text
                        self.assertIn(want[:40], rendered[key],
                                      f"{path} {sect}: marker {marker} renders "
                                      f"{rendered[key]!r:.80} but the section's citation "
                                      f"{marker} is {want!r:.60}")
        self.assertGreaterEqual(checked, 10,
                                f"only {checked} markers checked — the stored runs should "
                                "provide at least the 12 resolvable ones measured on run7")

    def test_the_run7_misresolution_cases_specifically(self):
        """The two reader-visible examples from the diagnosis, pinned exactly."""
        fp = _load_four_ps("out/live/run7.json")
        if fp is None:
            self.skipTest("run7 not present")
        rendered = _rendered_footnote_map(_render_citations_block(fp))
        # Guard against a vacuous pass: with the old flat rendering the map is EMPTY, and
        # assertNotIn over "" would succeed while proving nothing.
        self.assertIn(("price", 1), rendered,
                      "price has no per-section footnote 1 at all — the flat list is back")
        price_1 = rendered.get(("price", 1), "")
        self.assertNotIn("Max-Diff", price_1,
                         "price's marker 1 still resolves to the product section's Max-Diff "
                         "source — the flat loop.index numbering is back")
        promo_3 = rendered.get(("promotion", 3), "")
        self.assertNotIn("PSM Pricing", promo_3,
                         "promotion's marker 3 still resolves to the price section's PSM "
                         "source")

    def test_a_skipped_id_keeps_its_own_number(self):
        """<ol> auto-numbering breaks the moment an id is missing: citations [1,3] would render
        as visible 1,2 and marker 3 would land on id 3's text via number 2's slot — off by one
        forever after. The value attribute must pin each visible number to the citation's id."""
        fp = {"citations": [
            {"id": 1, "source": "Alpha", "_section": "price"},
            {"id": 3, "source": "Gamma", "_section": "price"},
        ]}
        rendered = _rendered_footnote_map(_render_citations_block(fp))
        self.assertIn(("price", 3), rendered,
                      "citation id 3 is not rendered under visible number 3")
        self.assertIn("Gamma", rendered[("price", 3)])
        self.assertNotIn(("price", 2), rendered,
                         "a phantom footnote 2 exists — ids are being renumbered by position")


class TestTheRendererAndTheDetectorShareOneIdSpace(unittest.TestCase):
    """The structural property that lets this never regress: whatever set of (section, id)
    pairs the DETECTOR considers resolvable must be exactly the set the RENDERER emits anchors
    for. The bug lived in the gap between the two."""

    def test_agreement_on_every_stored_run(self):
        from report.citation import audit_sections
        for path in ("out/live/run5.json", "out/live/run6.json", "out/live/run7.json"):
            fp = _load_four_ps(path)
            if fp is None:
                continue
            sections = {p: fp.get(p) for p in ("product", "price", "place", "promotion")
                        if isinstance(fp.get(p), dict)}
            rendered_keys = set(_rendered_footnote_map(_render_citations_block(fp)))
            detector_keys = set()
            for name, sec in sections.items():
                for c in (sec.get("citations") or []):
                    if c.get("id") is not None:
                        detector_keys.add((name, int(c["id"])))
            self.assertEqual(rendered_keys, detector_keys,
                             f"{path}: the renderer and the detector disagree about which "
                             f"(section, id) footnotes exist — renderer-only: "
                             f"{sorted(rendered_keys - detector_keys)}, detector-only: "
                             f"{sorted(detector_keys - rendered_keys)}")


if __name__ == "__main__":
    unittest.main()
