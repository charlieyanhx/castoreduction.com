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
import statistics
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
    # R4 rank 17: check every EDGE (low/mid/high), not just the mid — the mid clamp
    # scaled high independently and let SAM.high exceed TAM.high on ordered mids (3/16).
    ms = r.get("market_sizing") or {}
    blocks = {k: {e: _num((ms.get(k) or {}).get(e)) for e in ("low", "mid", "high")}
              for k in ("tam", "sam", "som")}
    tam, sam, som = blocks["tam"], blocks["sam"], blocks["som"]
    if tam["mid"] is None or sam["mid"] is None or som["mid"] is None:
        return Finding(None, "funnel incomplete")
    for edge in ("low", "mid", "high"):
        t, s, o = tam[edge], sam[edge], som[edge]
        if s is not None and t is not None and s > t:
            return Finding(False, f"SAM.{edge} {s:,.0f} > TAM.{edge} {t:,.0f}")
        if o is not None and s is not None and o > s:
            return Finding(False, f"SOM.{edge} {o:,.0f} > SAM.{edge} {s:,.0f}")
    return Finding(True, f"funnel ordered on all edges (mids {som['mid']:,.0f} <= "
                         f"{sam['mid']:,.0f} <= {tam['mid']:,.0f})")


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
    # "/mo per " matters as much as "/month per ": the tier cards and optimal-price
    # line render the ABBREVIATED form, so a list carrying only "/month per " reported
    # "clean" on a marketplace that printed "$185.0/mo per booking" four times (caught
    # by the Wave-4-entry R4 panel, R5). A gate's phrase list has to match what the
    # renderer actually writes, not a near-miss of it.
    hits = [p for p in ("/month per ", "/mo per ", "B2B SaaS benchmark", "per account")
            if p in html]
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


def d23_at_som_matches_its_label(r: dict, html: Optional[str]) -> Finding:
    """`at_som_volume` must be the volume its own label claims.

    The dominant R12 integrity defect: Unit Economics computed profitability at
    som.HIGH while labelling it `som_capture_pct: 100.0` and titling the box "at the
    obtainable SOM volume" — the same volume the scenario table on the facing page
    calls "130% of SOM (aggressive)". A buyer read it as "profitable at our obtainable
    market". 12/16 corpus ventures. FAILS when the implied capture disagrees with the
    stated one; N/A when there is no at-SOM claim or no SOM to check against."""
    asv = ((r.get("economics") or {}).get("at_som_volume") or {})
    monthly = _num(asv.get("monthly_revenue_usd"))
    stated = _num(asv.get("som_capture_pct"))
    som_mid = _num(((r.get("market_sizing") or {}).get("som") or {}).get("mid"))
    if monthly is None or stated is None or not som_mid:
        return Finding(None, "no at-SOM claim or no SOM to check against")
    implied = (monthly * 12) / som_mid * 100
    ok = abs(implied - stated) <= max(2.0, stated * 0.02)
    return Finding(ok, f"at-SOM revenue implies {implied:.0f}% of SOM but is labelled "
                       f"{stated:.0f}%" if not ok else
                       f"at-SOM volume matches its {stated:.0f}% label")


def d24_withheld_profit_not_fabricated(r: dict, html: Optional[str]) -> Finding:
    """A WITHHELD profit must not be rendered as a number.

    business_model.py omits monthly_operating_profit_usd on purpose when SOM spans
    several sites but the cost stack is one site, recording why in
    profit_withheld_reason. The template formatted the absent value through
    SafeUndefined and printed "$0/mo operating profit" — a figure nobody computed,
    beside $1.5M/mo revenue, while the scenario table showed $999K/mo at the identical
    volume, and the reason appeared nowhere. Same class as D09: the code withholds,
    the renderer publishes anyway."""
    asv = ((r.get("economics") or {}).get("at_som_volume") or {})
    reason = str(asv.get("profit_withheld_reason") or "").strip()
    if not reason:
        return Finding(None, "nothing withheld")
    if html is None:
        return Finding(None, "no HTML in corpus")
    if "$0/mo operating profit" in html:
        return Finding(False, "profit was withheld but the report renders "
                              "'$0/mo operating profit' — a fabricated figure")
    # The reason must actually reach the reader; silence just looks incomplete.
    if reason[:30] not in html:
        return Finding(False, "profit withheld but the reason is rendered nowhere")
    return Finding(True, "withheld profit disclosed with its reason")


def d25_provenance_chip_not_fabricated(r: dict, html: Optional[str]) -> Finding:
    """The report may not claim SOURCING it does not have (R4 rank 1, 16/16).

    The integrity chip rendered green "Sourced: 3/3 — headline methods with a cited
    source" from the LLM-authored `source` strings, on reports whose every
    triangulation path was origin='llm' — model-recalled citations sold as fetched
    data, 7 of them naming Census/BLS for numbers no fetch produced. FAILS when no
    method is genuinely grounded (data_origin) but the html carries the fetched-data
    claim without the model-asserted disclosure."""
    ms = r.get("market_sizing") or {}
    tam = ms.get("tam") or {}
    methods = [tam.get(k) or {} for k in ("method_top_down", "method_bottom_up", "method_analog")]
    methods = [m for m in methods if isinstance(m.get("value_usd"), (int, float))]
    if not methods or html is None:
        return Finding(None, "no headline methods or no HTML")
    n_grounded = sum(1 for m in methods
                     if str(m.get("data_origin") or "").strip().lower() not in ("", "llm"))
    if n_grounded > 0:
        return Finding(True, f"{n_grounded}/{len(methods)} methods genuinely grounded")
    # All model-asserted: the old green claim must be gone and the disclosure present.
    if "with a cited source" in html:
        return Finding(False, "all origins are llm but the chip still claims "
                              "'headline methods with a cited source'")
    if "model-asserted" not in html:
        return Finding(False, "all origins are llm and the model-asserted disclosure "
                              "is rendered nowhere")
    return Finding(True, "model-asserted citations disclosed as such")


def d26_pnl_cost_side_honest(r: dict, html: Optional[str]) -> Finding:
    """The P&L may not claim profits its own cost side cannot support (R4 rank 2).

    Three checks over financials + economics:
      1. A withheld profit stays withheld: when assumptions.profit_withheld_reason is
         set, no scenario row may carry monthly_operating_profit_usd.
      2. Break-even feasibility vs the venture's OWN published CAC: a break-even year
         whose acquisition spend (that year's customers x typical_cac_usd) meets or
         exceeds that year's revenue is impossible — 4a755faa claimed break-even
         YEAR 1 beside a $4,500 CAC implying ~$4.3M spend against $160K revenue.
      3. Implied operating margin never exceeds the disclosed contribution margin —
         profit = revenue x margin - fixed can't beat the margin, so a row that does
         was not built from the formula.
    """
    fin = r.get("financials") or {}
    scen = fin.get("scenarios") or {}
    if not scen:
        return Finding(None, "no financials")
    assumptions = fin.get("assumptions") or {}
    problems: list[str] = []

    # The withhold decision may live on EITHER surface — financials' own assumptions,
    # or the economics at-SOM block. The stored de34e328 is exactly the cross-surface
    # case: economics withheld its verdict, and the scenario table on the same page
    # published $827.8K/mo at the identical multi-site volume anyway.
    _econ_reason = ((r.get("economics") or {}).get("at_som_volume") or {}).get("profit_withheld_reason")
    if assumptions.get("profit_withheld_reason") or _econ_reason:
        for label, sc in scen.items():
            if not isinstance(sc, dict):
                continue
            for yk in ("year_1", "year_2", "year_3"):
                if "monthly_operating_profit_usd" in (sc.get(yk) or {}):
                    problems.append(f"{label}.{yk} carries a profit despite the withhold")

    cac = _num(((r.get("economics") or {}).get("unit_economics") or {}).get("typical_cac_usd"))
    if cac and cac > 0:
        for label, sc in scen.items():
            if not isinstance(sc, dict):
                continue
            be = sc.get("break_even_year")
            yr = sc.get(f"year_{be}") if be else None
            n = _num((yr or {}).get("customers"))
            rev = _num((yr or {}).get("revenue_usd"))
            if be and n and rev and n * cac >= rev:
                problems.append(
                    f"{label}: break-even Y{be} but acquisition spend "
                    f"({n:,.0f} x ${cac:,.0f} = ${n * cac:,.0f}) >= Y{be} revenue ${rev:,.0f}")

    margin = _num(assumptions.get("contribution_margin_pct"))
    if margin:
        for label, sc in scen.items():
            if not isinstance(sc, dict):
                continue
            for yk in ("year_1", "year_2", "year_3"):
                y = sc.get(yk) or {}
                p_, rev = _num(y.get("monthly_operating_profit_usd")), _num(y.get("revenue_usd"))
                if p_ and rev and rev > 0 and (p_ * 12) / rev > margin / 100 + 0.001:
                    problems.append(f"{label}.{yk}: implied op margin exceeds the "
                                    f"disclosed {margin}% contribution margin")

    if problems:
        return Finding(False, "; ".join(problems[:3]))
    return Finding(True, "cost side consistent with its own claims")


_SOM_SHARE_RE = re.compile(r"(\d{2,4}(?:\.\d+)?)\s*% of SOM")


def d27_som_share_claims_possible(r: dict, html: Optional[str]) -> Finding:
    """No rendered share of SOM may exceed 100%, and the scenario construction must
    reach the reader (R4 rank 3, 16/16).

    The old label divided each scenario's Y3 ceiling by som_mid and printed the
    ratio as "% of SOM by Y3" — but the ceilings ARE the SOM band, so aggressive
    printed 120-200% of SOM: more than the obtainable market, by definition
    impossible. And `assumptions.scenario_basis`, the one sentence explaining the
    construction, was emitted in JSON and rendered nowhere."""
    fin = r.get("financials") or {}
    if not fin.get("scenarios") or html is None:
        return Finding(None, "no financials or no HTML")
    impossible = sorted({m for m in _SOM_SHARE_RE.findall(html) if float(m) > 100})
    if impossible:
        return Finding(False, "impossible share of SOM rendered: "
                              + ", ".join(f"{v}%" for v in impossible[:4]))
    basis = str((fin.get("assumptions") or {}).get("scenario_basis") or "")
    if basis:
        # Compare against UNESCAPED html: Jinja autoescape turns the basis text's
        # apostrophe into &#39;, and a raw substring check reported a rendered
        # sentence as missing (caught live on the first re-render).
        import html as _html_mod
        first_clause = basis.split(":")[0].strip()
        if first_clause and first_clause not in _html_mod.unescape(html):
            return Finding(False, "scenario_basis is in the JSON but its first "
                                  "clause is rendered nowhere")
    return Finding(True, "share claims possible; scenario basis rendered")


def d28_domain_identity_verified(r: dict, html: Optional[str]) -> Finding:
    """Competitor domains must be IDENTITIES, not lookalikes; the relevance gate must
    actually fire (R4 rank 4, 15/16).

    Two checks:
      1. Any ranked record whose domain came from pattern_probe at "medium" must have
         a registrable label that plausibly IS the brand name — purpleair.shop's
         squatter prices became the category anchor through exactly this hole.
      2. Calibration canary: off_category firing on ZERO of >=50 relevance-scored
         records is the audit's 9-of-263 shape — a gate that never fires is
         decoration, and decoration reads as verification.
    """
    ops = (((r.get("discover") or {}).get("synthesis") or {})
           .get("ranked_opportunities") or [])
    if not ops:
        return Finding(None, "no ranked competitors")
    from sources import brand_names_match
    bad = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        if (op.get("domain_source") == "pattern_probe"
                and op.get("domain_confidence") == "medium"):
            label = str(op.get("domain") or "").split(".")[0]
            if label and not brand_names_match(str(op.get("brand") or ""), label):
                bad.append(f"{op.get('brand')} -> {op.get('domain')}")
    if bad:
        return Finding(False, "pattern-probed domain fails the brand-identity match: "
                              + "; ".join(bad[:3]))
    scored = [op for op in ops if isinstance(op, dict)
              and isinstance(op.get("relevance_score"), (int, float))]
    if len(scored) >= 50 and not any(op.get("off_category") for op in scored):
        return Finding(False, f"relevance gate mis-calibrated: 0 of {len(scored)} "
                              "scored records flagged off-category")
    return Finding(True, "domain identities verified; relevance gate live")


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


_DIFF_PRICE_LANG = re.compile(
    r"\$\s?\d|\d+(?:\.\d+)?%\s*(?:cheaper|below|above|less|lower|premium)", re.I)


def d30_differentiators_evidence_backed(r: dict, html: Optional[str]) -> Finding:
    """Differentiators must stand on evidence, not on a prompt mandate (R4 rank 6).

    Step 3d ran before any evidence existed, under "you MUST return at least 1 ...
    never zero", and strength was a pure function of the count that structure pinned
    at 8-10 — "high" on 16/16 ventures, anchoring the viability score. Reports
    asserted specific competitor pricing as unhedged fact for products that do not
    exist yet. Three checks:
      1. price-comparison language with NO competitor_pricing evidence in the run;
      2. near-duplicate entries (token-Jaccard >= 0.5) — one idea restated to
         inflate the count;
      3. strength "high" while no entry cites any evidence_ref at all."""
    diffs_blk = r.get("differentiators") or {}
    entries = [e for e in (diffs_blk.get("differentiators") or []) if isinstance(e, dict)]
    if not entries:
        return Finding(None, "no differentiators")
    problems: list[str] = []

    has_pricing = bool(((r.get("competitor_pricing") or {}).get("per_domain"))
                       or ((r.get("competitor_pricing") or {}).get("competitors")))
    if not has_pricing:
        for e in entries:
            text = f"{e.get('feature') or ''} {e.get('why_unique') or ''}"
            if _DIFF_PRICE_LANG.search(text):
                problems.append("price-comparison claim with no competitor_pricing "
                                f"evidence in the run: {str(e.get('feature'))[:60]!r}")
                break

    toks = []
    for e in entries:
        t = set(re.findall(r"[a-z0-9]+", str(e.get("feature") or "").lower()))
        for prev in toks:
            if t and prev and len(t & prev) / len(t | prev) >= 0.5:
                problems.append("near-duplicate entries inflate the count "
                                f"({str(e.get('feature'))[:50]!r})")
                break
        else:
            toks.append(t)
            continue
        break

    if (diffs_blk.get("differentiation_strength") == "high"
            and not any(str(e.get("evidence_ref") or "").strip() for e in entries)):
        problems.append('strength "high" while no entry cites any evidence')

    if problems:
        return Finding(False, "; ".join(problems[:3]))
    return Finding(True, "differentiators evidence-backed, distinct, honestly rated")


def d31_benchmark_prices_coherent(r: dict, html: Optional[str]) -> Finding:
    """A benchmark price must be a comparable per-unit price, not a mixed-SKU median
    (R4 rank 7, 7/16).

    scrape_brand_prices pooled every dollar amount off a page and medianed it —
    purpleair.shop's [9.99..349] became "$75.50", spread 31.7x. Two checks:
      1. any per-domain median whose prices_found span > 3x is incoherent;
      2. a category_median backed by fewer than 3 priced domains is not a category."""
    cp = r.get("competitor_pricing") or {}
    per_domain = cp.get("per_domain") or []
    if not per_domain and cp.get("category_median") is None:
        return Finding(None, "no competitor pricing")
    problems = []
    for d in per_domain:
        med = d.get("median")
        prices = [p for p in (d.get("prices_found") or [])
                  if isinstance(p, (int, float)) and p > 0]
        if med and prices:
            spread = max(prices) / min(prices) if min(prices) > 0 else 999
            if spread > 3.0:
                problems.append(f"{d.get('domain')}: median ${med} from a "
                                f"{spread:.0f}x price spread (mixed SKUs)")
    n_priced = sum(1 for d in per_domain if d.get("median"))
    if cp.get("category_median") is not None and n_priced < 3:
        problems.append(f"category median from only {n_priced} priced domain(s) "
                        "— not a defensible category")
    if problems:
        return Finding(False, "; ".join(problems[:3]))
    return Finding(True, "benchmark prices coherent and adequately sourced")


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


def d32_wtp_aggregation_honest(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 8: the WTP band must be honest arithmetic over the interviews.

    Three defects the corpus carried: the 'median' was the UPPER-middle order
    statistic (overstated for even n); a $0 'would not buy' counted as a payer,
    inflating n_would_pay; and a low/median/high band was minted from as few as 2
    answers. FAIL when the reported median disagrees with statistics.median of the
    strictly-positive interview WTPs, when n_would_pay counts a non-positive answer,
    or when a median band rests on fewer than 3 named prices."""
    cr = r.get("consumer_research") or {}
    syn = cr.get("synthesis") or {}
    wtp = syn.get("willingness_to_pay") or {}
    if not wtp:
        return Finding(None, "no WTP band")
    interviews = cr.get("interviews") or []
    pos = [float(w) for iv in interviews
           if isinstance((w := iv.get("willingness_to_pay_usd")), (int, float))
           and not isinstance(w, bool) and w > 0]

    n = wtp.get("n_would_pay")
    if interviews and n is not None and n != len(pos):
        return Finding(False, f"n_would_pay={n} but {len(pos)} strictly-positive WTPs "
                              "(a $0/non-buyer counted as a payer)")

    md = wtp.get("median")
    if md is not None and not wtp.get("single_point"):
        if (wtp.get("n_would_pay") or 0) < 3:
            return Finding(False, f"median band from only {wtp.get('n_would_pay')} "
                                  "named prices — a median needs >= 3")
        if pos:
            true_med = statistics.median(pos)
            if abs(float(md) - true_med) > 0.01:
                return Finding(False, f"reported median {md} != statistics.median "
                                      f"{true_med} of the interview WTPs")
    return Finding(True, "WTP aggregation honest")


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


def d33_competitor_counts_reconcile(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 9: ONE canonical competitor roster. The displayed roster
    (ranked_opportunities) is the count a report stands behind; competitor_density and
    the clustering map must both be counts of THAT set. Four surfaces disagreed on the
    corpus (15/16): density counted the discovered pool (~20) while the report listed a
    curated 7-9, and clustering ran on a third `signals` set or silently dropped
    thin-text venues. FAIL when density != roster length, when clustering did not
    receive the roster, or when clustering lost competitors without disclosing them."""
    disc = r.get("discover") or {}
    roster = ((disc.get("synthesis") or {}).get("ranked_opportunities")
              or disc.get("ranked_opportunities") or [])
    if not roster:
        return Finding(None, "no competitor roster")
    n_roster = len(roster)

    density = disc.get("competitor_density")
    if density is not None and density != n_roster:
        return Finding(False, f"competitor_density {density} != {n_roster} displayed "
                              "competitors (density counts a set the report doesn't show)")

    clust = r.get("clustering") or {}
    if clust and not clust.get("error"):
        n_input = clust.get("n_input")
        if n_input is not None:
            if n_input != n_roster:
                return Finding(False, f"clustering saw {n_input} competitors but the "
                                      f"roster has {n_roster} — the map is a different set")
            n_mapped, n_drop = clust.get("n_competitors"), clust.get("n_dropped")
            if (n_mapped is not None and n_drop is not None
                    and n_mapped + n_drop != n_input):
                return Finding(False, f"clustering lost competitors silently: "
                                      f"{n_mapped} mapped + {n_drop} dropped != {n_input}")
    return Finding(True, f"{n_roster} competitors coherent across density/roster/map")


def d34_roster_excludes_references(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 10: the competitor roster (ranked_opportunities) must contain only real
    competitors — reference/off-category/non-competitor entries belong in
    reference_cases, not counted as competitors. B4/D19 relabeled them 'reference' but
    left them in the roster, inflating density and taking map dots. FAIL when a
    ranked_opportunities entry is off_category, flagged is_competitor==false, or labelled
    relevance=='reference'."""
    disc = r.get("discover") or {}
    roster = ((disc.get("synthesis") or {}).get("ranked_opportunities")
              or disc.get("ranked_opportunities") or [])
    if not roster:
        return Finding(None, "no competitor roster")
    junk = [o.get("brand") for o in roster
            if o.get("off_category") or o.get("is_competitor") is False
            or (o.get("relevance") or "").strip().lower() == "reference"]
    if junk:
        return Finding(False, f"{len(junk)} non-competitor entries counted in the "
                              f"roster: {', '.join(str(b) for b in junk[:4])}")
    return Finding(True, f"{len(roster)} entries, all real competitors")


def d35_tam_method_divergence_disclosed(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 12: when the three TAM methods (top-down/bottom-up/analog) diverge by
    more than 3x, that divergence must be disclosed — not collapsed to a single point
    with spread 0.0. Every method shares the 'llm' origin, so triangulate reduces them
    to one median and reports a cross-origin spread of 0.0 that reads as "converged"
    above tables actually spanning 8-28x (800c261b 27.8x). FAIL when methods diverge
    >3x but the triangulation reports converged/spread 0 or carries no raw_spread."""
    tam = ((r.get("market_sizing") or {}).get("tam") or {})
    vals = [float(tam[k]["value_usd"]) for k in
            ("method_top_down", "method_bottom_up", "method_analog")
            if isinstance(tam.get(k), dict) and _num(tam[k].get("value_usd"))]
    if len(vals) < 2 or min(vals) <= 0:
        return Finding(None, "fewer than 2 numeric TAM methods")
    span = max(vals) / min(vals)
    if span <= 3.0:
        return Finding(True, f"TAM methods span {span:.1f}x — coherent")
    tri = tam.get("triangulation") or {}
    spread, raw = tri.get("spread"), tri.get("raw_spread")
    if tri.get("converged") is True or (spread is not None and spread == 0):
        return Finding(False, f"TAM methods span {span:.1f}x but triangulation reports "
                              f"spread={spread}/converged={tri.get('converged')} — "
                              "the divergence is hidden behind one median")
    if raw is None:
        return Finding(False, f"TAM methods span {span:.1f}x but no raw_spread is "
                              "disclosed — the divergence is invisible")
    return Finding(True, f"TAM methods span {span:.1f}x, disclosed (raw_spread={raw})")


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


def d37_viability_anchored_to_real_margin(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 14: a per-unit venture with a computed contribution margin must anchor
    viability to THAT margin, not invent one. The surfacing was gated on
    model=='transactional', so hybrid/services/ecommerce got nothing (28d0ec61 computed
    65.5% but viability called it 'thin on unit-level contribution margins', score 40).
    FAIL when economics is a per-unit kind with a contribution_margin_pct but viability
    carries no unit_economics_anchor, or the anchor disagrees with the computed margin."""
    from business_model import is_per_unit
    econ = r.get("economics") or {}
    cm = econ.get("contribution_margin_pct")
    if not is_per_unit(econ.get("model")) or not _num(cm):
        return Finding(None, "not a per-unit venture with a computed margin")
    anchor = (r.get("viability") or {}).get("unit_economics_anchor")
    if not anchor:
        return Finding(False, f"economics computed a {cm}% per-unit margin but viability "
                              "records no unit_economics_anchor — margin not surfaced")
    a = _num(anchor.get("contribution_margin_pct"))
    if a is None or abs(a - float(cm)) > 0.01:
        return Finding(False, f"viability anchor margin {a} != computed {cm}")
    return Finding(True, f"viability anchored to the computed {cm}% margin")


def d38_sam_slice_authoritative(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 15: the serviceable slice a report stands behind is sam.mid / tam.mid.
    The LLM's key_assumption prose stated a different % (174ae091: SAM is 90% of TAM
    but the assumption said '15%') and was rendered nowhere. FAIL when a SAM lacks the
    computed `serviceable_slice_pct` or that figure disagrees with sam.mid/tam.mid."""
    ms = r.get("market_sizing") or {}
    tam_mid = _num((ms.get("tam") or {}).get("mid"))
    sam = ms.get("sam") or {}
    sam_mid = _num(sam.get("mid"))
    if not tam_mid or not sam_mid:
        return Finding(None, "no SAM/TAM mids")
    computed = sam_mid / tam_mid * 100.0
    slice_pct = _num(sam.get("serviceable_slice_pct"))
    if slice_pct is None:
        return Finding(False, f"SAM has no serviceable_slice_pct (computed slice is "
                              f"{computed:.1f}% of TAM — the authoritative figure)")
    if abs(slice_pct - computed) > 0.5:
        return Finding(False, f"serviceable_slice_pct {slice_pct}% != computed "
                              f"{computed:.1f}% (sam.mid/tam.mid)")
    return Finding(True, f"serviceable slice {slice_pct}% matches sam.mid/tam.mid")


def d39_price_reconcile_unit_honest(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 16: a per-unit venture's price reconciliation must render in the
    venture's OWN unit, not a hardcoded '/mo' — an $18,500-per-project consultancy
    read '$18,500/mo'. FAIL when a per-unit venture's price_reconciliation note carries
    '/mo'. N/A for subscriptions (where /mo is correct) or no reconciliation."""
    from business_model import is_per_unit
    recon = r.get("price_reconciliation") or {}
    note = recon.get("note")
    if not note:
        return Finding(None, "no price reconciliation")
    econ = r.get("economics") or {}
    kind = econ.get("model") or r.get("business_model_kind")
    if not is_per_unit(kind):
        return Finding(None, "not a per-unit venture (/mo is correct)")
    if "/mo" in note:
        return Finding(False, "per-unit venture's price reconciliation is priced '/mo' "
                              "instead of the venture's unit")
    return Finding(True, "price reconciliation uses the venture's unit")


def d40_hyperlocal_som_basis_honest(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 18: a hyperlocal SOM that rests on an UNSOURCED single-unit revenue
    estimate must not be described as 'capacity-based' — only a real seats × turns
    model is capacity-based. 4/6 hyperlocal reports claimed capacity while the SOM was
    an LLM guess (no seat data). FAIL when the notes say the SOM is 'capacity-based'
    without any seat/capacity evidence."""
    ms = r.get("market_sizing") or {}
    scale = (ms.get("scale_decision") or {}).get("scale")
    if scale != "hyperlocal":
        return Finding(None, "not a hyperlocal venture")
    notes = " ".join(ms.get("notes") or [])
    if not notes:
        return Finding(None, "no sizing notes")
    if "capacity-based" in notes and "seat" not in notes.lower():
        return Finding(False, "SOM described as 'capacity-based' with no seat/capacity "
                              "model — it is an unsourced single-unit revenue estimate")
    return Finding(True, "hyperlocal SOM basis described honestly")


def d41_no_empty_price_per_customer(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 20: a non-priced model (ad-supported / marketplace) or
    a $0 PSM price fell through the subscription assumptions block and rendered an empty
    "Annual price per customer: $ (%/mo churn assumed)". FAIL when the HTML shows a
    price-per-customer line with no number."""
    if html is None:
        return Finding(None, "no html to check")
    import re
    if re.search(r"price per customer:\s*\$\s*\(", html):
        return Finding(False, "empty 'Annual price per customer: $ (%/mo churn)' — a "
                              "non-priced model fell through the subscription block")
    return Finding(True, "no empty per-customer price rendered")


def d42_no_near_dupe_competitors(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 22: the RapidFuzz near-dupe collapse ran on the web competitor set but
    not the geo set, so same-name / corporate-family venues ('Brooklyn Barber' twice)
    could be plotted as rival camps. FAIL when two roster entries are >=92 fuzzy-similar
    by brand name."""
    disc = r.get("discover") or {}
    roster = ((disc.get("synthesis") or {}).get("ranked_opportunities")
              or disc.get("ranked_opportunities") or [])
    names = [str(o.get("brand") or o.get("name") or "").strip() for o in roster]
    names = [n for n in names if n]
    if len(names) < 2:
        return Finding(None, "fewer than 2 named competitors")
    try:
        from rapidfuzz import fuzz
        from sources import _brand_key  # same normalization collapse_near_dupes uses
    except Exception:
        return Finding(None, "rapidfuzz/sources unavailable")
    keys = [_brand_key(n) for n in names]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if not (keys[i] and keys[j]):
                continue
            if keys[i] == keys[j] or max(fuzz.ratio(keys[i], keys[j]),
                                         fuzz.token_sort_ratio(keys[i], keys[j])) >= 92:
                return Finding(False, f"near-duplicate competitors: "
                                      f"'{names[i]}' vs '{names[j]}'")
    return Finding(True, f"{len(names)} distinct competitors, no near-dupes")


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


def d44_vertical_anchors_match_tags(r: dict, html: Optional[str]) -> Finding:
    """R4 rank 24: the 'b2b' substring pulled the 'saas' tag, so a b2b HARDWARE venture
    got 'B2B SaaS' NRR / CAC-payback / magic-number anchors it has no basis for
    (800c261b, a superconductor firm). FAIL when a stored vertical anchor is not
    selectable by the venture's own model/category tags."""
    ms = r.get("market_sizing") or {}
    stored = ((ms.get("macro_anchors") or {}).get("vertical_anchors") or {})
    if not stored:
        return Finding(None, "no vertical anchors")
    prof = r.get("profile") or {}
    try:
        from macro_anchors import fetch_vertical_anchors
        valid = set(fetch_vertical_anchors(prof.get("business_model", ""),
                                           prof.get("category", "")).keys())
    except Exception:
        return Finding(None, "macro_anchors unavailable")
    extra = sorted(set(stored) - valid)
    if extra:
        return Finding(False, "vertical anchors not justified by the venture's tags: "
                              + ", ".join(extra[:4]))
    return Finding(True, f"{len(stored)} vertical anchors all match the venture's tags")


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


def d46_ranked_score_is_pythons(r: dict, html: Optional[str]) -> Finding:
    """Audit critical #1: a ranked competitor's displayed opportunity_score must be the
    Python composite (`_signal_score` -> `_score`) of the record enrichment gathered for
    it, not the synthesis LLM's re-scoring of the same data.

    Both values are in the report: `discover.steps.signals[*]._score` is Python's, and
    `discover.synthesis.ranked_opportunities[*].opportunity_score` is what prints. On the
    pre-fix corpus 73 of 77 matchable records (94%) disagreed, the model inflating by a
    mean of +14.3 points — and the disclosed `avg_opportunity_score`, computed from the
    Python pool, sat beside displayed scores averaging +25.6 higher.

    A record with no enriched counterpart (geo-sourced neighbours) must carry no score;
    a score without a counterpart is a number nothing computed. N/A when discovery has
    no enriched pool or no ranked records to compare."""
    d = r.get("discover") or {}
    enriched = ((d.get("steps") or {}).get("signals")) or []
    ops = ((d.get("synthesis") or {}).get("ranked_opportunities")
           or d.get("ranked_opportunities") or [])
    if not enriched or not ops:
        return Finding(None, "no enriched pool or no ranked records")
    by_domain = {e.get("domain"): e for e in enriched if e.get("domain")}
    by_brand = {e.get("brand"): e for e in enriched if e.get("brand")}
    bad, unbacked, checked = [], 0, 0
    for op in ops:
        src = by_domain.get(op.get("domain")) or by_brand.get(op.get("brand"))
        shown = op.get("opportunity_score")
        if src is None:
            if shown is not None:
                unbacked += 1
            continue
        py = src.get("_score")
        if py is None or shown is None:
            continue
        checked += 1
        if abs(_num(py) - _num(shown)) > 0.5:
            bad.append(f"{op.get('brand') or op.get('domain')} {py}->{shown}")
    if unbacked:
        return Finding(False, f"{unbacked} ranked record(s) print a score with no "
                              "Python-computed counterpart")
    if bad:
        return Finding(False, f"{len(bad)}/{checked} displayed scores are the model's, "
                              f"not Python's: {', '.join(bad[:4])}")
    if not checked:
        return Finding(None, "no record pairs both sides scored")
    return Finding(True, f"{checked} displayed score(s) all equal the Python composite")


def d47_trace_belongs_to_one_run(r: dict, html: Optional[str]) -> Finding:
    """Audit criticals #2/#3: a report's `_trace` must be exactly ONE run's history.

    `step_done` appends to `_steps_completed` and records the ledger step event in the
    same call, idempotent per result dict, and every `_step_done` site in plan.py passes
    the same `result`. So within one run no step name can repeat and no step event can
    exist that `_steps_completed` never declared. Either means a concurrent run's events
    landed in this report — which is how the buyer-facing "Data Provenance" panel came to
    publish another run's work: on the pre-fix corpus 8/16 reports carry duplicate or
    foreign step events, and the contaminated ones report 84-107 LLM calls against a clean
    median of 37 (~2.8x). `_cogs` is derived from the same event list, so the disclosed
    cost of the report inherits the same inflation.

    Post-fix every event carries `run_id`, so more than one run id in a trace is direct
    proof; that clause is inert on pre-fix reports, which is why the structural checks
    carry the baseline. Steps DECLARED but absent from the trace are not failed here — a
    resume seed and plan.py's direct "refine" append both legitimately declare a step
    with no ledger event in this run."""
    tr = r.get("_trace")
    if not isinstance(tr, list) or not tr:
        return Finding(None, "no _trace (D12 covers presence)")
    declared = r.get("_steps_completed") or []
    comp = [e.get("name") for e in tr if isinstance(e, dict)
            and e.get("layer") == "step" and e.get("status") == "complete"]
    if not comp:
        return Finding(None, "no step-complete events in the trace")
    run_ids = {e.get("run_id") for e in tr if isinstance(e, dict) and e.get("run_id")}
    dupes = sorted({n for n in comp if comp.count(n) > 1})
    foreign = sorted(n for n in set(comp) - set(declared) if n)
    problems = []
    if len(run_ids) > 1:
        problems.append(f"{len(run_ids)} distinct run_ids in one trace")
    if dupes:
        problems.append(f"step(s) recorded complete more than once: {dupes[:4]}")
    if foreign:
        problems.append(f"step event(s) this run never declared: {foreign[:4]}")
    if problems:
        return Finding(False, "; ".join(problems))
    return Finding(True, f"{len(comp)} step event(s), one run's history")


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
    center = wtp.get("median") if wtp.get("median") is not None else wtp.get("point")
    ceiling = wtp.get("high") if wtp.get("high") is not None else center
    recommended = (r.get("pricing") or {}).get("psm", {}).get("optimal_price_point")
    ceiling_n, rec_n = _num(ceiling), _num(recommended)
    if not ceiling_n or not rec_n:
        return Finding(None, "WTP ceiling or recommended price missing")
    # R4 rank 11: the mismatch that misleads a buyer is a price ABOVE the top of the
    # WTP range — not a ratio-to-median inside a wide deadband. A price at/below the
    # ceiling is fine (someone would pay it).
    if rec_n <= ceiling_n:
        return Finding(None, f"recommended {rec_n} within WTP range (ceiling {ceiling_n})")
    flagged = "wtp_price_mismatch" in syn
    return Finding(flagged, f"recommended {rec_n} above WTP ceiling {ceiling_n} — "
                   + ("disclosed" if flagged else "UNFLAGGED"))


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


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_NUM_TOKEN = r"(?:\d[\d,]*|" + "|".join(_NUMBER_WORDS) + r")"
_DENSITY_CLAIM_RE = re.compile(
    r"\b(?:only|just)\s+(" + _NUM_TOKEN + r")\s+(?:meaningful\s+|direct\s+|real\s+|"
    r"identified\s+|active\s+)*competitors?\b"
    r"|\b(" + _NUM_TOKEN + r")\s+(?:meaningful\s+|direct\s+|real\s+|identified\s+|"
    r"active\s+)*competitors?\s+(?:identified|found|in\s+(?:the|this)\s+(?:market|category))\b",
    re.I,
)


def _competitor_count_claims(text: str) -> list[int]:
    """Numeric 'only/just N competitors' or 'N competitors identified/found/in the
    market' claims — the exact phrasing shape of the real R4 critical (viability
    reasoning said "only one meaningful competitor" while the Competitors section
    listed 248). Handles both digit and spelled-out one-ten (LLM prose spells out
    small numbers). Deliberately narrow (unlike a bare '\\d+ competitors?') so it
    does not fire on subset references ("the top 3 competitors by revenue") or an
    adjacent price figure ("a $250 competitor price point")."""
    out = []
    for m in _DENSITY_CLAIM_RE.finditer(text or ""):
        raw = (m.group(1) or m.group(2)).lower()
        if raw in _NUMBER_WORDS:
            out.append(_NUMBER_WORDS[raw])
        else:
            try:
                out.append(int(raw.replace(",", "")))
            except ValueError:
                pass
    return out


def d22_viability_reasoning_density_coherent(r: dict, html: Optional[str]) -> Finding:
    """D22 item 3: competitive_density_directive (item 1, four_ps.py) and the
    business-model-aware real_metrics (item 2) reduce, but do not eliminate, the
    chance that Viability's OWN written prose invents a competitor count that
    disagrees with the real, FINAL discover.competitor_density — especially the
    documented KNOWN LIMITATION case (see competitive_density_directive's docstring)
    where a hyperlocal venture's real competitor set is only surfaced LATE, after
    4Ps/Viability prompts were already dispatched with the pre-override density.
    This gate is the safety net, checked against the finished report. Mines
    'only/just N competitors' and 'N competitors identified/found/in the market'
    claims from viability's per-dimension reasoning, summary, strengths, and risks;
    a claim is coherent if it matches EITHER competitor_density or
    active_signal_density (item 1's own two canonical numbers). N/A when viability
    names no such claim, or no density has been computed to check against."""
    disc = r.get("discover") or {}
    density = disc.get("competitor_density")
    active = disc.get("active_signal_density")
    valid = {n for n in (density, active) if n is not None}

    v = r.get("viability") or {}
    texts: dict[str, str] = {}
    for dim, block in (v.get("scores") or {}).items():
        texts[f"scores.{dim}.reasoning"] = (block or {}).get("reasoning") or ""
    texts["summary"] = v.get("summary") or ""
    for i, s in enumerate(v.get("strengths") or []):
        texts[f"strengths[{i}]"] = s or ""
    for i, risk in enumerate(v.get("risks") or []):
        if isinstance(risk, dict):
            texts[f"risks[{i}]"] = risk.get("risk") or ""

    claims: dict[str, list[int]] = {}
    for loc, t in texts.items():
        cs = _competitor_count_claims(t)
        if cs:
            claims[loc] = cs
    if not claims:
        return Finding(None, "viability names no explicit competitor-count claim")
    if not valid:
        return Finding(None, "no competitor_density computed to check against")

    bad = {loc: cs for loc, cs in claims.items() if any(c not in valid for c in cs)}
    return Finding(not bad,
                   f"viability claims disagree with real density {sorted(valid)}: {bad}"
                   if bad else f"viability's competitor claims match real density {sorted(valid)}")


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
    Invariant("D22", "viability reasoning coherent with real competitor density", "invented competitor-count claim (audit item 3)", "fail", d22_viability_reasoning_density_coherent),
    Invariant("D23", "at-SOM claim matches its own label", "R12: aggressive ceiling sold as the obtainable volume", "fail", d23_at_som_matches_its_label),
    Invariant("D24", "withheld profit never rendered as a number", "R12: a suppressed verdict published as a fabricated $0", "fail", d24_withheld_profit_not_fabricated),
    Invariant("D25", "provenance chip never claims sourcing it lacks", "R4 rank 1: model-asserted citations sold as fetched data", "fail", d25_provenance_chip_not_fabricated),
    Invariant("D26", "P&L cost side honest (withhold holds; CAC-feasible break-even; margin bound)", "R4 rank 2: single-site scalar + ignored CAC", "fail", d26_pnl_cost_side_honest),
    Invariant("D27", "no impossible share-of-SOM claim; scenario basis rendered", "R4 rank 3: ceilings ARE the band, ratio sold as capture", "fail", d27_som_share_claims_possible),
    Invariant("D28", "competitor domains are identities, not lookalikes", "R4 rank 4: pattern-probed squatter poisoned prices", "fail", d28_domain_identity_verified),
    Invariant("D29", "withheld sizing binds derived surfaces (scenarios, viability)", "R4 rank 5: unflagged revenue table below a do-not-rely banner", "fail", d29_withhold_propagates),
    Invariant("D30", "differentiators evidence-backed, distinct, honestly rated", "R4 rank 6: fabricated before evidence, strength pinned high", "fail", d30_differentiators_evidence_backed),
    Invariant("D31", "benchmark prices coherent (same-unit, >=3 domains)", "R4 rank 7: mixed-SKU median fabricated as a category price", "fail", d31_benchmark_prices_coherent),
    Invariant("D32", "WTP aggregation honest (real median, no $0 payer, n>=3 band)", "R4 rank 8: upper-middle order statistic, $0 payer, 2-answer median", "fail", d32_wtp_aggregation_honest),
    Invariant("D33", "competitor counts reconcile (density==roster==map input)", "R4 rank 9: 4 competitor counts on 4 surfaces, none canonical", "fail", d33_competitor_counts_reconcile),
    Invariant("D34", "roster is only real competitors (references partitioned out)", "R4 rank 10: self-flagged junk relabeled not excluded", "fail", d34_roster_excludes_references),
    Invariant("D35", "TAM method divergence disclosed (no fake 0% spread)", "R4 rank 12: single-origin collapse hides 8-28x method spread", "fail", d35_tam_method_divergence_disclosed),
    Invariant("D36", "validation warns surfaced (not under a green chip)", "R4 rank 13: advisory warns computed, rendered nowhere", "fail", d36_validation_warns_surfaced),
    Invariant("D37", "viability anchored to the real per-unit margin", "R4 rank 14: unit-econ anchor gated on transactional only", "fail", d37_viability_anchored_to_real_margin),
    Invariant("D38", "SAM serviceable slice is authoritative (sam/tam), rendered", "R4 rank 15: slice back-formed, key_assumption contradicts it", "fail", d38_sam_slice_authoritative),
    Invariant("D39", "price reconciliation priced in the venture's unit", "R4 rank 16: hardcoded /mo on a per-unit venture", "fail", d39_price_reconcile_unit_honest),
    Invariant("D40", "hyperlocal SOM basis honest (capacity vs unsourced estimate)", "R4 rank 18: 'capacity-based' claimed over an LLM guess", "fail", d40_hyperlocal_som_basis_honest),
    Invariant("D41", "no empty per-customer price (non-priced fall-through)", "R4 rank 20: ad_supported hits the subscription else-branch", "fail", d41_no_empty_price_per_customer),
    Invariant("D42", "no near-duplicate competitors (geo set collapsed too)", "R4 rank 22: near-dupe collapse skipped the geo set", "fail", d42_no_near_dupe_competitors),
    Invariant("D43", "no dead in-page nav anchors", "R4 rank 24: nav linked to conditionally-rendered sections", "fail", d43_no_dead_in_page_anchors),
    Invariant("D44", "vertical macro anchors match the venture's tags", "R4 rank 24: b2b substring pulled saas anchors onto b2b hardware", "fail", d44_vertical_anchors_match_tags),
    Invariant("D45", "cannot-decode notice not self-refuting", "R4 rank 24: 'N signals found ... no review surface'", "fail", d45_cannot_decode_notice_not_self_refuting),
    Invariant("D46", "ranked score is Python's, not the model's", "audit critical #1: opportunity_score == enriched _score", "fail", d46_ranked_score_is_pythons),
    Invariant("D47", "trace belongs to one run", "audit criticals #2/#3: no duplicate/foreign step events, one run_id", "fail", d47_trace_belongs_to_one_run),
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
