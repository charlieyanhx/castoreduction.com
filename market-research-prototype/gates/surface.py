"""gates/surface.py — what actually reached the page: completeness, disclosure, and dead ends.

10 of the 61 deterministic detectors. Each takes (result, html) and
returns a Finding; none of them share state, which is why they split cleanly.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional
from gates.common import Finding, not_applicable, _num


def d01_complete(r: dict, html: Optional[str]) -> Finding:
    steps = len(r.get("_steps_completed") or [])
    return Finding(steps >= 12, f"{steps} steps completed")


def d02_renders(r: dict, html: Optional[str]) -> Finding:
    if html is None:
        return Finding(None, "no HTML in corpus")
    return Finding(len(html) > 1000, f"{len(html)} bytes")


def _fig_patterns(value: float) -> list[str]:
    """Regexes matching how a dollar magnitude is RENDERED in prose ($1.22B, $180M)."""
    pats = []
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        v = value / div
        if not (0.1 <= v < 1000):
            continue
        for s in (f"{v:.2f}", f"{v:.1f}", f"{v:.0f}"):
            pats.append(rf"\${re.escape(s)}\s*{unit}\b")
    return pats


def _withheld_figures_asserted_in_prose(r: dict) -> list[str]:
    """Withheld headline figures that a NARRATIVE field restates as fact.

    Narrative only — viability reasoning, the 4Ps sections, the executive summary.
    The sizing table may still SHOW the figure beside its warning; that is the
    disclosure. What is illegal is asserting it somewhere the reader takes as a
    finding, which is what drove a 65/100 market-opportunity score off a number the
    same report said not to rely on.
    """
    ms = r.get("market_sizing") or {}
    fp = r.get("four_ps") or {}
    prose_parts = [json.dumps(r.get("viability") or {}),
                   str(fp.get("executive_summary") or "")]
    for sect in ("product", "price", "place", "promotion"):
        prose_parts.append(str((fp.get(sect) or {}).get("narrative") or ""))
    prose = " ".join(prose_parts)

    hits = []
    for key in ("tam", "sam", "som"):
        mid = _num((ms.get(key) or {}).get("mid"))
        if not mid:
            continue
        for pat in _fig_patterns(mid):
            m = re.search(pat, prose)
            if m:
                hits.append(f"{key.upper()} {m.group()}")
                break
    return hits


def d29_withhold_propagates(r: dict, html: Optional[str]) -> Finding:
    """A withheld sizing binds everything DERIVED from it (R4 rank 5, 4/4 blocked).

    The withhold used to end at one Jinja block: 3219f4db rendered "do not rely on
    these figures" and, immediately below it, an unflagged $96K/$420K/$1.2M revenue
    table computed from the same withheld funnel. Two checks when validation failed:
      1. financials must carry the derived_from_withheld_sizing stamp — the
         DATA-layer decision JSON consumers (PDF, API) read;
      2. the html's scenarios REGION (heading to next h2) must carry the withhold
         language. A missing scenarios section passes — absence cannot mislead."""
    ms = r.get("market_sizing") or {}
    if ms.get("publishable") is not False:
        return Finding(None, "sizing publishable or absent")
    fin = r.get("financials") or {}
    if fin.get("scenarios") and not fin.get("derived_from_withheld_sizing"):
        return Finding(False, "sizing withheld but financials carries no "
                              "derived_from_withheld_sizing stamp")
    if html is not None:
        m = re.search(r"3-Year Revenue Scenarios", html)
        if m:
            nxt = re.search(r"<h2[\s>]", html[m.end():])
            region = html[m.end(): m.end() + (nxt.start() if nxt else len(html))]
            low = region.lower()
            if "integrity gate" not in low and "do not rely" not in low:
                return Finding(False, "withheld sizing but the revenue-scenarios "
                                      "section renders with no withhold language")
    return Finding(True, "withhold propagates to derived surfaces")


def d09_publishable_gated(r: dict, html: Optional[str]) -> Finding:
    """Failed validation must WITHHELD the numbers — not merely disclaim them.

    The original check verified two things: publishable is False, and a withhold
    banner exists in the html. Both can be true while the report restates the
    withheld figure as a finding elsewhere — which is exactly what the R4 panel
    caught on 174ae091 ("Failed validation - figures withheld" and "a massive $1.22B
    TAM" in one document, with the score built on it), and what this gate returned
    "gated correctly" for. The gate verified that a disclaimer was printed, not that
    the report honoured it. All 4 corpus ventures that fail validation did this.
    """
    ms = r.get("market_sizing") or {}
    val = ms.get("validation") or {}
    if val.get("passed") is not False:
        return Finding(None, "validation passed or absent")
    if ms.get("publishable") is not False:
        return Finding(False, "validation failed but publishable flag not False")
    # Case-insensitive on BOTH clauses: the first was case-sensitive while the second
    # lowercased, so a banner reading "Failed validation" satisfied only one of them.
    if html is not None:
        low = html.lower()
        if "failed validation" not in low and "do not rely" not in low:
            return Finding(False, "validation failed but no withhold banner rendered")
    asserted = _withheld_figures_asserted_in_prose(r)
    if asserted:
        return Finding(False, "withheld figures restated as fact in narrative prose: "
                              + ", ".join(asserted))
    return Finding(True, "gated correctly")


def d14_no_failed_sections(r: dict, html: Optional[str]) -> Finding:
    """No 4Ps section shipped with its generation-failed placeholder still in it."""
    fp = r.get("four_ps") or {}
    bad = [s for s in ("product", "price", "place", "promotion")
           if "generation failed" in str((fp.get(s) or {}).get("narrative") or "")]
    return Finding(not bad, f"failed sections: {bad}" if bad else "all sections generated")


def d36_validation_warns_surfaced(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 13: advisory validation.warns ('estimates diverge 11x — at least one is
    wrong') were computed and stored but rendered nowhere, while a green 'Validated —
    passed the integrity gate' chip sat over them. When warns exist, the report must
    render each warn's text AND must not show the plain-green Validated chip."""
    val = ((r.get("market_sizing") or {}).get("validation") or {})
    warns = val.get("warns") or []
    if not warns:
        return Finding(None, "no validation warns")
    if html is None:
        return Finding(None, "no html to check")
    import html as _html_mod
    text = _html_mod.unescape(html)
    missing = [w.get("msg") for w in warns if w.get("msg") and w["msg"] not in text]
    if missing:
        return Finding(False, f"{len(missing)} of {len(warns)} validation warn(s) not "
                              "rendered in the report")
    if "passed the integrity gate" in text:
        return Finding(False, "warns present but a plain-green 'Validated' chip "
                              "('passed the integrity gate') is shown over them")
    return Finding(True, f"{len(warns)} warn(s) surfaced; chip is not plain-green")


def d43_no_dead_in_page_anchors(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 24: the 'Jump to' nav linked to sections that render conditionally, so
    16/16 reports carried dead in-page anchors (#sensitivity, #audiences, #customer-
    universe, …) that scroll nowhere. FAIL when an href='#X' has no matching id='X'."""
    if html is None:
        return Finding(None, "no html to check")
    import re
    anchors = set(re.findall(r'href="#([a-z0-9][a-z0-9-]*)"', html))
    ids = set(re.findall(r'id="([a-z0-9][a-z0-9-]*)"', html))
    dead = sorted(anchors - ids)
    if dead:
        return Finding(False, f"dead in-page anchors (nav link, no target section): "
                              f"{', '.join('#' + a for a in dead[:6])}")
    return Finding(True, f"{len(anchors)} in-page anchors all resolve to a section")


def d45_cannot_decode_notice_not_self_refuting(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 24: the 'insufficient customer voice' notice reported finding N signals
    and, in the same sentence, claimed 'no consumer review surface' / 'no scrapable
    presence' (10/16 reports: 'total 21 signals … no consumer review surface'). FAIL
    when a notice that reports total N>0 signals also claims the surface is absent."""
    if html is None:
        return Finding(None, "no html to check")
    import re
    notices = re.findall(r"total (\d+) signals[^.]*\.([^<]{0,160})", html)
    if not notices:
        return Finding(None, "no cannot-decode notices")
    for total, tail in notices:
        if int(total) > 0 and ("no consumer review surface" in tail
                                or "no scrapable presence" in tail):
            return Finding(False, f"notice reports {total} signals yet claims the "
                                  "surface is absent (self-refuting)")
    return Finding(True, f"{len(notices)} cannot-decode notice(s), none self-refuting")


# Keys the ledger names that legitimately land under a DIFFERENT result path. Verified by
# searching run2's result for each: counting these as lost would make the gate cry wolf on
# two healthy outputs, and I nearly reported five losses instead of three by skipping this.
_LEDGER_KEY_ALIASES = {
    "competitor_landscape": ("discover.synthesis.ranked_opportunities", "discover"),
    "pricing_benchmark": ("pricing.benchmark",),
    "market_scale": ("market_scale", "market_sizing.scale_decision"),
    "market_sizing": ("market_sizing",),
}


def _path_present(r: dict, dotted: str) -> bool:
    cur: object = r
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return bool(cur)


def d54_produced_output_reaches_the_report(r: dict, html: str | None) -> Finding:
    """Work the pipeline paid for must reach the report, or the run must say why it did not.

    MEASURED on run2: the ledger recorded 9 outputs as produced, with module, qualname, file
    and line. Three appear NOWHERE in the result -- clustering (cluster_competitors,
    clustering.py:142, ok=true), consumer_research, price_intel. `clustering` was in run1's
    report and vanished from run2's while still being recorded as produced, because the caller
    does `if not clustering.get("error")` and on error simply moves on.

    A section that disappears without a trace is indistinguishable from one that was never
    meant to exist, so the trace ends up MORE complete than the report it describes.

    A drop is acceptable -- real data is sometimes too sparse -- but it must be RECORDED.
    `_dropped_outputs[key] = reason` satisfies this gate; silence does not.

    N/A when the run carries no ledger (`_trace`), since there is nothing to reconcile."""
    if not (r.get("_trace") or []):
        return Finding(None, "no run ledger to reconcile against")
    try:
        from report.trace import recorded_producers
        produced = recorded_producers(r) or {}
    except Exception as e:                     # pragma: no cover - defensive
        return Finding(None, f"cannot read the ledger: {type(e).__name__}: {e}")
    if not produced:
        return Finding(None, "ledger records no produced outputs")

    dropped = r.get("_dropped_outputs") or {}
    lost = []
    for key, meta in produced.items():
        if key in dropped:
            continue                            # accounted for, with a reason
        paths = _LEDGER_KEY_ALIASES.get(key, (key,))
        if any(_path_present(r, p) for p in paths):
            continue
        by = (meta or {}).get("produced_by") or "?"
        where = ""
        if (meta or {}).get("file"):
            where = f" ({meta['file']}:{meta.get('line')})"
        lost.append(f"{key} produced by {by}{where}")
    if lost:
        return Finding(False,
                       f"{len(lost)} output(s) the ledger records as produced are absent from "
                       "the report with no reason recorded: " + "; ".join(lost[:4])
                       + (f" (+{len(lost)-4} more)" if len(lost) > 4 else ""))
    return Finding(True, f"all {len(produced)} recorded outputs are present or explained "
                         f"({len(dropped)} explained drop(s))")


# Measured across 17 stored corpus reports plus three live runs: answerable-gate coverage runs
# 63-80% on every real report and 44-46% on the two thin ones. A floor of 55% sits in a
# 17-point gap, so it discriminates with margin at both ends rather than being a round number
# somebody liked.
_MIN_COVERAGE_PCT = 55


def d55_report_is_complete_enough_to_have_been_checked(r: dict, html: str | None) -> Finding:
    """A report must not pass by being too empty to judge.

    THE PROBLEM THIS EXISTS FOR. Every other gate answers True, False, or not-applicable, and
    not-applicable is correct when a section is absent. But the SCORECARD then rewards absence:
    measured, out/live/run2 scored 23 pass / 0 fail where the fuller-but-partly-fabricated run1
    scored 35 pass / 1 fail. The emptier report looked better. A regression that guts a section
    reads as an improvement, which is exactly backwards.

    So this gate asks the one question none of the others can: how much of the rulebook could
    ACTUALLY ANSWER on this report? Below the floor, the verdict "nothing wrong" means "almost
    nothing was checked", and those must not look the same.

    Deliberately NOT a section checklist. Sections are legitimately conditional --
    customer_universe is B2B-only by design, and a hyperlocal cafe rightly skips it -- so
    counting sections would fail honest reports for being the shape they should be. Coverage
    measures what was CHECKABLE, which is the property that actually matters.

    Recursion note: this gate excludes itself from the count, or a report could pass it by
    virtue of it being answerable.

    AND IT MUST NOT COUNT THE VENTURE'S SHAPE AS THE REPORT'S FAULT. The paragraph above
    rejects a section checklist because "sections are legitimately conditional" — and the
    first version of this gate then counted GATES with the identical defect. Thirteen
    invariants are hyperlocal-only or per-unit-only by construction, so a national_digital
    subscription forfeited a third of the denominator before the run started. Measured, that
    put the whole family at 55-60% against a 55% floor: a user's report (job d62bc04f) was
    withheld at 52% as "too incomplete to have been meaningfully verified" while answering 31
    of its 47 APPLICABLE invariants, 25 pass / 1 fail — the 1 fail being this gate.

    So a detector that cannot apply to this KIND of venture says so via `not_applicable`, and
    those leave the denominator. What stays in is every abstention that means "this would
    apply and the data is not here", INCLUDING the guards that abstain because the report made
    no such claim — a hollow report makes no claims at all, so they all abstain together, and
    that is precisely the signal this gate reads. Measured across every artifact on disk, the
    split holds: out/live/run2 (the case in the paragraph above) 43% -> 49%, run3 45% -> 51%,
    run4 45% -> 50%, all still withheld; c98_subscription 55% -> 70%, becc8783 58% -> 74%,
    3219f4db 60% -> 77%, all delivering. A hyperlocal report barely moves (c98_nonus 73% ->
    75%) because almost nothing is out of scope for it, which is the point."""
    # Taken at call time: runner imports this module to BUILD the table, so importing
    # it back at module scope would be a cycle. This detector is the one that asks how
    # much of the table could answer, so it needs the table itself.
    from gates.runner import INVARIANTS
    answered = na = out_of_scope = 0
    excluded: list[str] = []
    for inv in INVARIANTS:
        if inv.id == "D55":
            continue
        try:
            f = inv.check(r, html)
        except Exception:                        # a crashing detector checked nothing
            na += 1
            continue
        if f.ok is not None:
            answered += 1
        elif f.out_of_scope:
            out_of_scope += 1
            excluded.append(inv.id)
        else:
            na += 1
    total = answered + na                        # applicable invariants only
    if not total:
        return Finding(None, "no applicable invariants to measure coverage against")
    pct = round(100 * answered / total)
    shape = (f"; {out_of_scope} more do not apply to this venture's shape "
             f"({', '.join(excluded)})" if out_of_scope else "")
    if pct < _MIN_COVERAGE_PCT:
        return Finding(False,
                       f"only {answered}/{total} applicable invariants ({pct}%) could answer "
                       f"on this report, below the {_MIN_COVERAGE_PCT}% floor — it is too "
                       "incomplete to have been meaningfully verified, so a clean scorecard "
                       f"here means 'barely checked', not 'nothing wrong'{shape}")
    return Finding(True, f"{answered}/{total} applicable invariants ({pct}%) could answer{shape}")
