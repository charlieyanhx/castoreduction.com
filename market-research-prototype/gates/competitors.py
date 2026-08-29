"""gates/competitors.py — the roster: who is on it, how many, and whether every surface counts the same set.

12 of the 61 deterministic detectors. Each takes (result, html) and
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


def d07_geo_competitors(r: dict, html: Optional[str]) -> Finding:
    """A hyperlocal venture found its competitors on the map, not on the web.

    If a venture classified hyperlocal has no geo-sourced roster, one of the two upstream
    decisions is wrong: either it is not really hyperlocal, or the location never resolved.
    Either way the trade-area arithmetic below it cannot be trusted.
    """
    # Hyperlocal ONLY: a hyperlocal-classified venture MUST have geo-sourced competitors —
    # if it can't (no location / unmapped category), either the promotion or the scale
    # classification is wrong (the audit's agency-misrouted-to-hyperlocal critical). Regional
    # chains and national_physical marketplaces have no single trade-area point → N/A.
    ms = r.get("market_scale") or {}
    if ms.get("scale") != "hyperlocal":
        return not_applicable(f"scale={ms.get('scale')} (not hyperlocal)")
    return Finding(bool((r.get("discover") or {}).get("geo_sourced")),
                   f"geo_sourced={(r.get('discover') or {}).get('geo_sourced')}")


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
    from scrape.search import is_shared_platform_host
    bad = []
    shared = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        # R1 (88b416f6): a shared platform host can never be a competitor's identity.
        # Dify.ai rostered with github.com had five report surfaces describe GitHub.
        dom = str(op.get("domain") or "")
        if dom and is_shared_platform_host(dom):
            shared.append(f"{op.get('brand')} -> {dom}")
        if (op.get("domain_source") == "pattern_probe"
                and op.get("domain_confidence") == "medium"):
            label = dom.split(".")[0]
            if label and not brand_names_match(str(op.get("brand") or ""), label):
                bad.append(f"{op.get('brand')} -> {dom}")
    if shared:
        return Finding(False, "roster domain is a shared platform host, not the "
                              "brand's identity: " + "; ".join(shared[:3]))
    if bad:
        return Finding(False, "pattern-probed domain fails the brand-identity match: "
                              + "; ".join(bad[:3]))
    scored = [op for op in ops if isinstance(op, dict)
              and isinstance(op.get("relevance_score"), (int, float))]
    if len(scored) >= 50 and not any(op.get("off_category") for op in scored):
        return Finding(False, f"relevance gate mis-calibrated: 0 of {len(scored)} "
                              "scored records flagged off-category")
    return Finding(True, "domain identities verified; relevance gate live")


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


def d51_momentum_count_measured_on_the_shown_roster(r: dict, html: str | None) -> Finding:
    """The active-momentum count must be measured on the roster the report DISPLAYS.

    Both geo-swap paths replace ranked_opportunities with real OSM rivals and resync
    competitor_density, but `active_signal_density` kept the value computed over the
    discarded web-discovery pool. Measured across the shipped corpus: 6 of 6 geo-sourced
    reports published an active count over a 26-30 venue roster carrying no signal data at
    all, and the claim was cited and load-bearing -- "Focus initial promotional efforts on
    the 7 competitors with active web-momentum signals" named rivals from the set that had
    been thrown away, so a reader following the advice was pointed at companies the report
    never lists.

    Fails when a published count cannot be backed by the displayed roster: either the roster
    carries no signal data at all (nothing was measured, so no count is defensible), or the
    count exceeds what the roster can support. N/A when no count was published, or when
    there is no roster to check it against."""
    disc = r.get("discover") or {}
    active = disc.get("active_signal_density")
    roster = ((disc.get("synthesis") or {}).get("ranked_opportunities")
              or disc.get("ranked_opportunities") or [])
    if active is None or not roster:
        return Finding(None, "no published momentum count, or no roster to check it against")

    observed = [o for o in roster if ("signals" in o or "_score" in o or "active_signal" in o)]
    if not observed:
        return Finding(False, f"claims {active} of {len(roster)} rivals show active "
                              "web-momentum, but not one entry in the displayed roster "
                              "carries any signal data -- the count describes a different set")
    backed = sum(1 for o in observed
                 if (o.get("signals") or {}) or o.get("active_signal")
                 or (o.get("_score") or 0) > 20)
    if active > backed:
        return Finding(False, f"claims {active} active rivals; the displayed roster supports "
                              f"at most {backed} of {len(observed)} measured entries")
    return Finding(True, f"{active} active of {len(observed)} measured entries in a "
                         f"{len(roster)}-rival roster")
