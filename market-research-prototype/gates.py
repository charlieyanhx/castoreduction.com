"""
gates.py — deterministic milestone gate runner.

Every detector here is a MACHINE-CHECKABLE invariant distilled from a confirmed audit finding
(docs/AUDIT_RESULTS.md M1-M15) or a milestone gate (docs/TESTING_MILESTONES.md). No LLM, no
network, no randomness: same corpus in → same verdict out, so milestones are claimable by a
program, not an opinion. The LLM audit panel (ring R4) remains only for what cannot be
deterministic (prose quality); everything below is ring R3.

Usage:
  python gates.py --corpus /tmp/audit/run1              # dir of <slug>.json (+ <slug>.html)
  python gates.py --db .jobs.sqlite --latest 16         # newest complete plan jobs (no HTML checks)
  python gates.py --corpus DIR --gate core --out docs/baselines/M3.json

Exit code: 0 if the selected gate passes, 1 otherwise (CI-able).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

PER_UNIT_KINDS = {"transactional", "ecommerce", "services", "hybrid"}
NON_US_MARKERS = (
    "portugal", "lisbon", "canada", "mexico", "brazil", "united kingdom", " uk", "london",
    "germany", "berlin", "france", "paris", "spain", "madrid", "italy", "japan", "tokyo",
    "china", "india", "australia", "singapore", "netherlands", "europe",
)
MONTHLY_UNITS = {"mo", "month", "monthly", "account", "seat"}


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


@dataclass
class Finding:
    ok: Optional[bool]  # True pass / False fail / None not-applicable
    detail: str = ""


@dataclass
class Invariant:
    id: str
    name: str
    audit_class: str          # which historical failure mode this detects
    severity: str             # "fail" blocks a gate; "warn" is reported only
    check: Callable[[dict, Optional[str]], Finding] = field(repr=False, default=None)


# --------------------------------------------------------------------------------------------
# Detectors. Each takes (result_json, rendered_html_or_None) and returns a Finding.
# --------------------------------------------------------------------------------------------

def d01_complete(r: dict, html: Optional[str]) -> Finding:
    steps = len(r.get("_steps_completed") or [])
    return Finding(steps >= 12, f"{steps} steps completed")


def d02_renders(r: dict, html: Optional[str]) -> Finding:
    if html is None:
        return Finding(None, "no HTML in corpus")
    return Finding(len(html) > 1000, f"{len(html)} bytes")


def d03_single_som(r: dict, html: Optional[str]) -> Finding:
    som = _num(((r.get("market_sizing") or {}).get("som") or {}).get("mid"))
    used = _num(((r.get("financials") or {}).get("assumptions") or {}).get("som_mid_used"))
    if som is None or used is None:
        return Finding(None, "SOM or financials absent")
    return Finding(abs(som - used) < 0.5, f"sizing SOM={som:,.0f} vs financials={used:,.0f}")


def d04_funnel_order(r: dict, html: Optional[str]) -> Finding:
    ms = r.get("market_sizing") or {}
    tam, sam, som = (_num((ms.get(k) or {}).get("mid")) for k in ("tam", "sam", "som"))
    if tam is None or sam is None or som is None:
        return Finding(None, "funnel incomplete")
    return Finding(som <= sam <= tam, f"SOM {som:,.0f} <= SAM {sam:,.0f} <= TAM {tam:,.0f}")


def d05_unit_no_monthly(r: dict, html: Optional[str]) -> Finding:
    if r.get("business_model_kind") not in PER_UNIT_KINDS:
        return Finding(None, "not a per-unit model")
    units = {
        "economics": str((r.get("economics") or {}).get("unit") or ""),
        "financials": str(((r.get("financials") or {}).get("assumptions") or {}).get("unit") or ""),
        "wtp": str((((r.get("consumer_research") or {}).get("synthesis") or {})
                    .get("willingness_to_pay") or {}).get("unit") or "").lstrip("/"),
    }
    bad = {k: u for k, u in units.items() if u.lower() in MONTHLY_UNITS}
    return Finding(not bad, f"units={units}" + (f" MONTHLY BLEED: {bad}" if bad else ""))


def d06_html_no_saas_bleed(r: dict, html: Optional[str]) -> Finding:
    # C3/D06-extend: marketplace ventures (take-rate per transaction) are also
    # never-recurring — same subscription-phrase leak class as per-unit models. Real
    # R4 catch: a marketplace's per-booking price rendered "$350/mo per account".
    # Kept separate from PER_UNIT_KINDS (shared with D05, where marketplace genuinely
    # isn't "per-unit" in the unit-noun sense) rather than widening that set.
    kind = r.get("business_model_kind")
    if kind not in PER_UNIT_KINDS and kind != "marketplace":
        return Finding(None, "not a per-unit or marketplace model")
    if html is None:
        return Finding(None, "no HTML in corpus")
    hits = [p for p in ("/month per ", "B2B SaaS benchmark", "per account") if p in html]
    return Finding(not hits, f"SaaS phrases in rendered report: {hits}" if hits else "clean")


def d07_geo_competitors(r: dict, html: Optional[str]) -> Finding:
    # Hyperlocal ONLY: a hyperlocal-classified venture MUST have geo-sourced competitors —
    # if it can't (no location / unmapped category), either the promotion or the scale
    # classification is wrong (the audit's agency-misrouted-to-hyperlocal critical). Regional
    # chains and national_physical marketplaces have no single trade-area point → N/A.
    ms = r.get("market_scale") or {}
    if ms.get("scale") != "hyperlocal":
        return Finding(None, f"scale={ms.get('scale')} (not hyperlocal)")
    return Finding(bool((r.get("discover") or {}).get("geo_sourced")),
                   f"geo_sourced={(r.get('discover') or {}).get('geo_sourced')}")


def d08_profit_coherent(r: dict, html: Optional[str]) -> Finding:
    econ = r.get("economics") or {}
    asv = econ.get("at_som_volume") or {}
    if asv.get("profitable_at_som") is not True:
        return Finding(None, "no profitable-at-SOM claim")
    scen = (r.get("financials") or {}).get("scenarios") or {}
    agg = scen.get("aggressive") or {}
    y3 = _num((agg.get("year_3") or {}).get("monthly_operating_profit_usd"))
    be = agg.get("break_even_year")
    if y3 is None and be is None:
        return Finding(None, "no scenario table")
    ok = (be is not None) or (y3 is not None and y3 > 0)
    return Finding(ok, f"profitable_at_som=True but aggressive Y3 profit={y3} break_even={be}")


def d09_publishable_gated(r: dict, html: Optional[str]) -> Finding:
    ms = r.get("market_sizing") or {}
    val = ms.get("validation") or {}
    if val.get("passed") is not False:
        return Finding(None, "validation passed or absent")
    if ms.get("publishable") is not False:
        return Finding(False, "validation failed but publishable flag not False")
    if html is not None and "failed validation" not in html and "do not rely" not in html.lower():
        return Finding(False, "validation failed but no withhold banner rendered")
    return Finding(True, "gated correctly")


def d10_wtp_band_sane(r: dict, html: Optional[str]) -> Finding:
    wtp = (((r.get("consumer_research") or {}).get("synthesis") or {})
           .get("willingness_to_pay") or {})
    lo, md, hi = _num(wtp.get("low")), _num(wtp.get("median")), _num(wtp.get("high"))
    if lo is None or md is None or hi is None:
        return Finding(None, "no band (single point or absent)")
    if not (lo <= md <= hi):
        return Finding(False, f"band unordered: {lo}/{md}/{hi}")
    if lo == hi and not wtp.get("single_point") and (wtp.get("n_would_pay") or 0) >= 2:
        return Finding(False, f"degenerate band {lo}=={hi} presented as a range")
    return Finding(True, f"band {lo}/{md}/{hi}")


def d11_currency_sources(r: dict, html: Optional[str]) -> Finding:
    geo = str((r.get("profile") or {}).get("geography") or "").lower()
    desc = str((r.get("profile") or {}).get("summary") or "").lower()
    blob = f"{geo} {desc}"
    if not any(m in blob for m in NON_US_MARKERS):
        return Finding(None, "US venture")
    srcs = " ".join((r.get("market_sizing") or {}).get("sources_to_validate") or [])
    bad = [s for s in ("US Census", "BLS") if s in srcs]
    return Finding(not bad, f"non-US venture recommends US sources: {bad}" if bad else "clean")


def d12_provenance(r: dict, html: Optional[str]) -> Finding:
    tr = r.get("_trace")
    n = len(tr) if isinstance(tr, (list, dict)) else 0
    return Finding(n > 0, f"{n} trace records" if n else "no _trace on result")


def d13_benchmark_not_fabricated(r: dict, html: Optional[str]) -> Finding:
    if not (r.get("discover") or {}).get("geo_sourced"):
        return Finding(None, "web-sourced competitors (benchmark legitimate)")
    rows = ((r.get("pricing") or {}).get("benchmark") or {}).get("rows") or []
    return Finding(not rows,
                   f"geo-sourced venture has {len(rows)} scraped benchmark rows" if rows else "no scraped benchmark (correct)")


def d14_no_failed_sections(r: dict, html: Optional[str]) -> Finding:
    fp = r.get("four_ps") or {}
    bad = [s for s in ("product", "price", "place", "promotion")
           if "generation failed" in str((fp.get(s) or {}).get("narrative") or "")]
    return Finding(not bad, f"failed sections: {bad}" if bad else "all sections generated")


_TAM_FIG_RE = re.compile(r"TAM[^$]{0,12}\$?\s*([\d.]+)\s*([BMK])\b", re.I)


def _tam_figures(text: str) -> list[float]:
    """Dollar magnitudes explicitly labeled 'TAM $X' in a free-text string."""
    out = []
    for m in _TAM_FIG_RE.finditer(text or ""):
        try:
            out.append(float(m.group(1)) * {"B": 1e9, "M": 1e6, "K": 1e3}[m.group(2).upper()])
        except (ValueError, KeyError):
            pass
    return out


def d15_tam_coherent_across_sections(r: dict, html: Optional[str]) -> Finding:
    """C1 single-value coherence (the audit's cross-cutting invariant, ported to a
    deterministic detector): every 'TAM $X' figure cited in the SAM derivation must
    match the headline tam.mid. The R4 audit's dominant CRITICAL cluster (9/16 reports)
    was triangulation rewriting tam.mid to the median while sam.calculation /
    serviceability_waterfall kept citing the pre-triangulation TAM — two different TAMs
    in one section. N/A when the SAM derivation is a bottom-up build with no TAM anchor."""
    ms = r.get("market_sizing") or {}
    tam_mid = _num((ms.get("tam") or {}).get("mid"))
    if not tam_mid:
        return Finding(None, "no headline TAM")
    sam = ms.get("sam") or {}
    figs = _tam_figures(sam.get("calculation") or "") + _tam_figures(sam.get("serviceability_waterfall") or "")
    if not figs:
        return Finding(None, "SAM derivation cites no TAM figure (bottom-up)")
    off = [f for f in figs if abs(f - tam_mid) / tam_mid > 0.10]
    return Finding(not off,
                   f"SAM cites TAM {[round(f/1e6) for f in off]}M but headline is {round(tam_mid/1e6)}M"
                   if off else f"SAM-derivation TAM matches headline ({round(tam_mid/1e6)}M)")


def d16_density_matches_ranked(r: dict, html: Optional[str]) -> Finding:
    """B1: competitor_density must be a plausible count of the ACTUAL ranked
    competitor set, not a filtered web-momentum count. The R4 critical shape:
    a hyperlocal cafe with 30 real OSM-sourced venues scored competitor_density=1
    (only 1 had web-momentum signal), and the viability prompt then faithfully
    argued '1 meaningful competitor' against a 30-venue market. FAIL when density
    is under half the ranked-list length. N/A when no ranked list is present."""
    disc = r.get("discover") or {}
    density = disc.get("competitor_density")
    if density is None:
        return Finding(None, "no competitor_density recorded")
    ops = disc.get("ranked_opportunities") or (disc.get("synthesis") or {}).get("ranked_opportunities") or []
    if not ops:
        return Finding(None, "no ranked competitor list")
    ok = density >= len(ops) / 2
    return Finding(ok, f"density={density} vs {len(ops)} ranked competitors"
                   if not ok else f"density={density} plausible for {len(ops)} ranked")


def d17_per_unit_not_on_subscription_fallback(r: dict, html: Optional[str]) -> Finding:
    """B2 + C3: a venture whose business model is NOT a true subscription
    (transactional/ecommerce/services/hybrid, OR marketplace) must NOT have
    financials on the subscription shape (a 'customers' key / 'annual_price_per_
    customer' assumption in the scenario table — that's churn-annualized revenue,
    wrong for both a one-time-sale business AND a take-rate marketplace).

    Two real R4 chains this catches: (1) 8add1fa2 (hybrid) — a device price was
    mis-extracted as its $5/mo app fee, margin went negative, economics errored, and
    financials silently fell back to subscription math. (2) 174ae091 (marketplace) —
    the average booking value was treated as a monthly seat fee ("$5400/yr, 5%
    monthly churn") for a venture whose own differentiator claims "zero subscription
    fees". N/A when the venture is a true subscription, or financials are absent."""
    kind = r.get("business_model_kind")
    year3 = (((r.get("financials") or {}).get("scenarios") or {}).get("base") or {}).get("year_3") or {}
    if kind in PER_UNIT_KINDS:
        econ = r.get("economics") or {}
        if econ.get("model") != "transactional":
            return Finding(None, "economics not on the transactional model")
        if not year3:
            return Finding(None, "no financials scenario table")
        bad = "customers" in year3
        return Finding(not bad, "financials year_3 carries 'customers' (subscription shape) "
                       "on a transactional venture" if bad else "financials use the unit shape")
    if kind == "marketplace":
        if not year3:
            return Finding(None, "no financials scenario table")
        assumptions = (r.get("financials") or {}).get("assumptions") or {}
        bad = "customers" in year3 or "annual_price_per_customer" in assumptions
        return Finding(not bad, "financials carry a subscription shape ('customers' or "
                       "annual_price_per_customer) on a marketplace venture" if bad
                       else "financials use the revenue-only shape")
    return Finding(None, "not a per-unit or marketplace model")


def d18_wtp_price_reconciled(r: dict, html: Optional[str]) -> Finding:
    """B3: a large gap between the consumer-research WTP synthesis and the PSM-
    recommended price must be disclosed (plan.reconcile_wtp_with_price), never
    rendered side by side with no comment. Real R4 shape: WTP $150-1,500/unit vs a
    $125,000/unit recommendation, 83-100x apart, unflagged (800c261b, e55db08e,
    4a755faa). FAIL when the ratio is outside 0.1x-10x and no wtp_price_mismatch
    flag is present. N/A when either number is missing or they already agree."""
    syn = ((r.get("consumer_research") or {}).get("synthesis") or {})
    wtp = syn.get("willingness_to_pay") or {}
    wtp_point = wtp.get("median") if wtp.get("median") is not None else wtp.get("point")
    recommended = (r.get("pricing") or {}).get("psm", {}).get("optimal_price_point")
    if not wtp_point or not recommended:
        return Finding(None, "WTP or recommended price missing")
    ratio = _num(recommended) / _num(wtp_point) if _num(wtp_point) else None
    if ratio is None or 0.1 <= ratio <= 10:
        return Finding(None, "WTP and recommended price agree (no mismatch)")
    flagged = "wtp_price_mismatch" in syn
    return Finding(flagged, f"WTP {wtp_point} vs recommended {recommended} "
                   f"({ratio:.0f}x) — unflagged" if not flagged else
                   f"mismatch disclosed ({ratio:.0f}x)")


def d19_no_off_category_direct_competitor(r: dict, html: Optional[str]) -> Finding:
    """B4: no off-category domain (content relevance below the W2-5 threshold) may
    present as a "direct" competitor in the top 3 ranked opportunities. Real R4
    critical (e55db08e): a 183-day-old crypto-SaaS domain ("Theon Technology") ranked
    #1 direct rival for a superconducting-tape venture purely on domain age, with no
    relevance signal checked. N/A when the ranking carries no off_category/relevance
    fields at all (older corpora, or a discovery run with no LLM-validated domains)."""
    disc = r.get("discover") or {}
    ops = disc.get("ranked_opportunities") or (disc.get("synthesis") or {}).get("ranked_opportunities") or []
    top3 = ops[:3]
    if not any("off_category" in o for o in top3):
        return Finding(None, "no relevance verdict on the top-3 ranking")
    bad = [o.get("brand") for o in top3 if o.get("off_category") and o.get("relevance") == "direct"]
    return Finding(not bad, f"off-category 'direct' competitor(s) in top 3: {bad}"
                   if bad else "no off-category domain ranked as direct")


_SAM_FIG_RE = re.compile(r"SAM[^$]{0,12}\$?\s*([\d.]+)\s*([BMK])\b", re.I)


def _sam_figures(text: str) -> list[float]:
    """Dollar magnitudes explicitly labeled 'SAM $X' in a free-text string."""
    out = []
    for m in _SAM_FIG_RE.finditer(text or ""):
        try:
            out.append(float(m.group(1)) * {"B": 1e9, "M": 1e6, "K": 1e3}[m.group(2).upper()])
        except (ValueError, KeyError):
            pass
    return out


def d20_sam_self_consistent(r: dict, html: Optional[str]) -> Finding:
    """C1: every 'SAM $X' figure cited in sam.calculation / sam.serviceability_waterfall
    must match the headline sam.mid — a SEPARATE defect from D15 (which checks the TAM
    figure in those same strings). Root cause: sam.mid can be moved by triangulation OR
    by the funnel-ordering clamp (_enforce_sizing_ordering), and the narrative strings
    used to only get re-synced once, early, before either of those could run. Real R4
    critical (174ae091): mid=$195.8M vs strings say $202.5M. N/A when the SAM narrative
    cites no SAM figure at all (a bottom-up-only calculation).

    Tolerance note: comparing raw sam_mid to a figure parsed back out of a 1-decimal-
    place display string (e.g. "$1.4B") is NOT apples-to-apples — format_currency's own
    rounding can introduce a ~3-4% artifact (1.35B displays as "1.4B", parses back as
    1.40B) that is the SAME magnitude as the real bug this detects. So sam_mid is passed
    through the identical formatter before comparing — both sides see the same rounding,
    and only a genuinely different underlying number trips the (now tight) threshold."""
    ms = r.get("market_sizing") or {}
    sam = ms.get("sam") or {}
    sam_mid = _num(sam.get("mid"))
    if not sam_mid:
        return Finding(None, "no headline SAM")
    figs = _sam_figures(sam.get("calculation") or "") + _sam_figures(sam.get("serviceability_waterfall") or "")
    if not figs:
        return Finding(None, "SAM narrative cites no SAM figure")
    from market_sizing import format_currency
    canon = _sam_figures(f"SAM {format_currency(sam_mid)}")
    baseline = canon[0] if canon else sam_mid
    off = [f for f in figs if abs(f - baseline) / baseline > 0.005]
    return Finding(not off,
                   f"SAM narrative cites {[round(f/1e6) for f in off]}M but headline is {round(sam_mid/1e6)}M"
                   if off else f"SAM narrative matches headline ({round(sam_mid/1e6)}M)")


_AVG_ORDER_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*average\s+(?:order|job|booking|transaction)\b", re.I)


def _avg_order_figures(text: str) -> list[float]:
    """Dollar magnitudes labeled '$X average order/job/booking/transaction' in prose."""
    out = []
    for m in _AVG_ORDER_RE.finditer(text or ""):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def _canonical_arpu(r: dict) -> Optional[float]:
    """Mirrors four_ps.price_anchor_directive's resolution: per-unit models use the
    REAL unit price (economics.price_per_unit); others use the PSM optimal point."""
    kind = str(r.get("business_model_kind") or "").lower()
    econ = r.get("economics") or {}
    if kind in ("transactional", "ecommerce", "services", "hybrid"):
        return _num(econ.get("price_per_unit"))
    return _num((r.get("pricing") or {}).get("psm", {}).get("optimal_price_point"))


def d21_arpu_coherent_across_sections(r: dict, html: Optional[str]) -> Finding:
    """C2: Place/Product/Promotion get NO pricing context in their own prompts (only
    Price does), so an "average order/job/booking" dollar figure appearing in their
    prose is genuinely invented, not miscomputed. Real R4 critical (a marketplace):
    Price used $450 (correct), Place said $200 "average job size", Product said $100
    "average order" — three numbers for one concept. FAILs when any section's figure
    disagrees with the canonical ARPU (economics.price_per_unit for per-unit models,
    else the PSM optimal point). N/A when no section names an average-order figure at
    all, or no canonical ARPU is available to check against."""
    canon = _canonical_arpu(r)
    fp = r.get("four_ps") or {}
    all_figs: dict[str, list[float]] = {}
    for section in ("product", "price", "place", "promotion"):
        nar = str((fp.get(section) or {}).get("narrative") or "")
        figs = _avg_order_figures(nar)
        if figs:
            all_figs[section] = figs
    if not all_figs:
        return Finding(None, "no section names an average order/job/booking figure")
    if not canon:
        return Finding(None, "no canonical ARPU available to check against")
    bad = {s: fs for s, fs in all_figs.items()
          if any(abs(f - canon) / canon > 0.02 for f in fs)}
    return Finding(not bad, f"sections disagree with canonical ${canon:,.0f}: {bad}"
                   if bad else f"all sections agree with canonical ${canon:,.0f}")


INVARIANTS: list[Invariant] = [
    Invariant("D01", "pipeline completes (>=12 steps)", "M2/M11 blank-or-degraded run", "fail", d01_complete),
    Invariant("D02", "report renders (>1KB HTML)", "M2 0-byte deliverable", "fail", d02_renders),
    Invariant("D03", "single canonical SOM", "M3 dual SOM", "fail", d03_single_som),
    Invariant("D04", "funnel ordered SOM<=SAM<=TAM", "sizing incoherence", "fail", d04_funnel_order),
    Invariant("D05", "per-unit model has per-unit units", "M4 subscription bleed (data)", "fail", d05_unit_no_monthly),
    Invariant("D06", "rendered report free of SaaS phrasing", "M5 subscription bleed (render)", "fail", d06_html_no_saas_bleed),
    Invariant("D07", "geo competitors for physical-local", "M1 wrong competitor set", "fail", d07_geo_competitors),
    Invariant("D08", "profitability claims coherent", "profitable-at-SOM contradiction", "fail", d08_profit_coherent),
    Invariant("D09", "failed validation withholds numbers", "unpublishable TAM reused", "fail", d09_publishable_gated),
    Invariant("D10", "WTP band sane", "degenerate/fabricated band", "fail", d10_wtp_band_sane),
    Invariant("D11", "non-US venture avoids US-only sources", "M6 currency/provenance mismatch", "warn", d11_currency_sources),
    Invariant("D12", "provenance trace present", "debuggability invariant", "warn", d12_provenance),
    Invariant("D13", "no scraped benchmark on geo-sourced set", "fabricated price benchmark", "fail", d13_benchmark_not_fabricated),
    Invariant("D14", "no failed 4Ps sections", "silent section failure", "warn", d14_no_failed_sections),
    Invariant("D15", "TAM coherent across sections", "same number, two values (audit C1)", "fail", d15_tam_coherent_across_sections),
    Invariant("D16", "competitor_density matches ranked set", "wrong density input to viability", "fail", d16_density_matches_ranked),
    Invariant("D17", "per-unit venture never on subscription fallback", "hybrid device price mis-extracted", "fail", d17_per_unit_not_on_subscription_fallback),
    Invariant("D18", "WTP reconciled with recommended price", "83x gap rendered uncommented", "fail", d18_wtp_price_reconciled),
    Invariant("D19", "no off-category 'direct' competitor in top 3", "wrong-industry rival ranked #1", "fail", d19_no_off_category_direct_competitor),
    Invariant("D20", "SAM narrative coherent with headline", "same SAM, two values", "fail", d20_sam_self_consistent),
    Invariant("D21", "ARPU coherent across 4Ps sections", "invented order/job/booking value", "fail", d21_arpu_coherent_across_sections),
]

# Named gates: which invariants must be 100% pass (severity 'fail' ones) for the claim.
GATES: dict[str, list[str]] = {
    "core": [i.id for i in INVARIANTS if i.severity == "fail"],
    "all": [i.id for i in INVARIANTS],
    "M1-fixes": ["D01", "D02", "D03", "D07"],       # the first root-fix wave
    "M4-models": ["D05", "D06", "D08", "D10", "D13"],  # business-model correctness wave
}


def load_corpus(corpus: Optional[str], db: Optional[str], latest: int) -> dict[str, tuple[dict, Optional[str]]]:
    """Return {name: (result_json, html_or_None)}."""
    out: dict[str, tuple[dict, Optional[str]]] = {}
    if corpus:
        for f in sorted(os.listdir(corpus)):
            if not f.endswith(".json"):
                continue
            path = os.path.join(corpus, f)
            try:
                d = json.load(open(path))
            except (OSError, json.JSONDecodeError):
                continue
            r = d.get("result") or d
            hpath = path[:-5] + ".html"
            html = open(hpath, encoding="utf-8", errors="replace").read() if os.path.exists(hpath) else None
            out[f[:-5]] = (r, html)
    elif db:
        con = sqlite3.connect(db)
        rows = con.execute(
            "select id, result_json from jobs where state='complete' and kind='plan' "
            "order by created_at desc limit ?", (latest,)).fetchall()
        for jid, rj in rows:
            try:
                out[jid[:8]] = (json.loads(rj), None)
            except (TypeError, json.JSONDecodeError):
                continue
    return out


def run_gate(reports: dict[str, tuple[dict, Optional[str]]], gate: str) -> dict:
    ids = set(GATES.get(gate) or GATES["core"])
    invs = [i for i in INVARIANTS if i.id in ids]
    per_report: dict[str, dict] = {}
    failures = 0
    for name, (r, html) in reports.items():
        cells = {}
        for inv in invs:
            f = inv.check(r, html)
            cells[inv.id] = {"ok": f.ok, "detail": f.detail, "severity": inv.severity}
            if f.ok is False and inv.severity == "fail":
                failures += 1
        per_report[name] = cells
    applicable = sum(1 for c in per_report.values() for v in c.values() if v["ok"] is not None)
    passed = sum(1 for c in per_report.values() for v in c.values() if v["ok"] is True)
    return {
        "gate": gate, "n_reports": len(reports),
        "invariants": [i.id for i in invs],
        "cells_applicable": applicable, "cells_passed": passed,
        "pct_pass": round(100 * passed / applicable, 1) if applicable else None,
        "blocking_failures": failures,
        "verdict": "PASS" if failures == 0 and applicable > 0 else "FAIL",
        "per_report": per_report,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic milestone gates for Castor reports")
    ap.add_argument("--corpus", help="dir of <slug>.json (+ optional <slug>.html)")
    ap.add_argument("--db", help=".jobs.sqlite path (no HTML checks)")
    ap.add_argument("--latest", type=int, default=16, help="with --db: newest N complete jobs")
    ap.add_argument("--gate", default="core", choices=sorted(GATES))
    ap.add_argument("--out", help="write scorecard JSON here")
    args = ap.parse_args()
    if not args.corpus and not args.db:
        ap.error("need --corpus or --db")

    reports = load_corpus(args.corpus, args.db, args.latest)
    if not reports:
        print("no reports found", file=sys.stderr)
        return 1
    card = run_gate(reports, args.gate)

    inv_ids = card["invariants"]
    print(f"gate={card['gate']}  reports={card['n_reports']}  "
          f"pass={card['cells_passed']}/{card['cells_applicable']} ({card['pct_pass']}%)  "
          f"blocking failures={card['blocking_failures']}  ->  {card['verdict']}")
    print(f"{'report':22s} " + " ".join(f"{i:>4s}" for i in inv_ids))
    sym = {True: "  ok", False: "FAIL", None: "   -"}
    for name, cells in card["per_report"].items():
        print(f"{name[:22]:22s} " + " ".join(sym[cells[i]["ok"]] for i in inv_ids))
    for name, cells in card["per_report"].items():
        for i in inv_ids:
            if cells[i]["ok"] is False:
                print(f"  !! {name} {i}: {cells[i]['detail']}")

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        json.dump(card, open(args.out, "w"), indent=2)
        print(f"scorecard -> {args.out}")
    return 0 if card["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
