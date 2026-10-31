"""skills/sizing/triangulation.py — one canonical TAM, and every sibling figure resynced to it.

Moved verbatim out of plan.py (#87). The bodies are byte-identical to what shipped; only
their address changed. plan.py re-exports them, so every existing caller and every test that
patches `plan.triangulate_sizing` keeps working.

WHY IT MOVES. plan.py was 2,211 lines and 37% of it was the sizing family — a market-sizing
library living inside the orchestrator that calls it. This cluster is the self-contained part:
its only dependency outside itself is the logger, which is why it goes first. #85 dismantled
run_plan in 13 waves for the same reason, and the same rule applies here — one coherent
cluster per commit, suite green between each.

WHAT IT DOES. triangulate_sizing picks ONE canonical TAM (median across independent data
origins, not an average of correlated estimates) and then makes every other figure agree with
it: SAM is re-derived, the calc strings are rewritten, and segmentation is renormalised. The
helpers exist because a headline TAM that changes while a SAM string still quotes the old one
is the self-contradiction this whole module prevents.
"""
from __future__ import annotations

import re

from logger import get

log = get("plan.sizing.triangulation")


def triangulate_sizing(sizing: dict) -> dict:
    """Replace the naive 3-method average with REAL origin-independent triangulation.

    The 3 TAM methods are tagged by data origin: a Census/BLS-grounded bottom-up is an
    independent origin ('census'); LLM-generated top-down/analog collapse to one 'llm'
    origin (they're correlated draws from one model — not independent). The headline
    `mid` becomes the median ACROSS origins, and the convergence view is attached so the
    report can show convergence/divergence honestly. cycle33 / TRIANGULATION.md.

    ONE ENGINE (audit high #9). This used to run two: report.forecast.triangulate produced
    the headline (unit-aware, EXCLUDING minority-unit methods) while skills.triangulate
    produced the `triangulation` object the report renders (unit-blind, INCLUDING them). The
    convergence badge could therefore annotate a headline it did not equal. Latent on the
    corpus only because every stored report has one unit and one origin, which makes the two
    engines arithmetically identical. report.forecast now derives both, so `point == mid` by
    construction.
    """
    tam = sizing.get("tam") or {}
    from skills.sizing.validate import safe_eval_formula
    out = dict(sizing)
    out_tam = dict(tam)
    from report.forecast import Method as _FMethod, triangulate as _ftri
    _methods = []
    for key, method in (("method_top_down", "top_down"),
                        ("method_bottom_up", "bottom_up"),
                        ("method_analog", "analog")):
        blk = dict(tam.get(key) or {})
        v = blk.get("value_usd")
        if not (isinstance(v, (int, float)) and not isinstance(v, bool)):
            continue
        # F5: do NOT silently rewrite a value to match its own formula. A gross
        # value/formula mismatch is an arithmetic hallucination — FLAG it and let
        # the validation gate block it (F1 then withholds the numbers), instead of
        # laundering an incoherent figure into a publishable one.
        computed = safe_eval_formula(str(blk.get("calculation") or ""))
        if computed and computed > 0 and (computed / v > 10 or computed / v < 0.1):
            blk["_formula_mismatch"] = {"stated": v, "computed": round(computed)}
            out_tam[key] = blk          # record the flag; keep the stated value
        _methods.append(_FMethod(
            name=method, value_usd=float(v),
            unit=(blk.get("unit") or "revenue").lower(),
            origin=(blk.get("data_origin") or "llm").lower(),
            formula=blk.get("calculation") or "", source=blk.get("source") or ""))
    if not _methods:
        return sizing

    # W4-1: the FINAL owner of the headline — and the site that used to switch the
    # derivation to a median while leaving market_sizing's "unweighted average"
    # sentence lying, and derive low/high from cross[] (one entry PER ORIGIN, so
    # all-llm collapsed the band to mid±15% under a caption claiming it spans the
    # methods). Numbers and prose now regenerate together through the one model.
    s = _ftri(_methods)
    out_tam["mid"], out_tam["low"], out_tam["high"] = s.mid, s.low, s.high
    out_tam["reconciliation"] = s.derivation
    out_tam["range_basis"] = s.range_basis
    out_tam["n_independent_origins"] = s.n_independent
    for m in s.unit_conflict:
        blk = out_tam.get(f"method_{m.name}")
        if blk:
            blk["excluded_from_headline"] = True
    # The convergence view the report renders, from the SAME engine that set `mid`.
    out_tam["triangulation"] = {
        "label": "TAM", "point": s.point, "spread": s.spread,
        # D35: the spread across EVERY printed method, so a wide table can never be
        # described only by the subset the headline was taken from.
        "raw_spread": s.raw_spread,
        # The unbounded companion. raw_spread is (max-min)/mid and saturates
        # near max/mid, so on a 312x table it reads 159% and cannot read more.
        "raw_fold": s.raw_fold,
        "converged": s.converged, "confidence": s.confidence,
        "cross_origin": [dict(c) for c in s.cross_origin],
        "n_independent": s.n_independent, "flag": s.flag,
    }
    out["tam"] = out_tam
    # Triangulation moved the headline TAM → re-derive any dependent figures from
    # the NEW mid so they stay consistent (else the gate's segmentation_sum check
    # correctly blocks every report). Segments rescale by share_pct, else proportionally.
    out = _renormalize_segmentation(out, s.mid)
    # D15 / audit C1: SAM and its calc strings were anchored to the PRE-triangulation
    # TAM — the report then showed two different TAMs in one section (the R4 panel's
    # dominant CRITICAL cluster, 10/16). Re-derive SAM from the canonical TAM so the
    # serviceability fraction is preserved and every 'TAM $X' string cites the headline.
    out = _resync_sam_after_triangulation(out, tam.get("mid"), s.mid)
    log.info("[plan] triangulated TAM: point=%s n_independent=%s confidence=%s",
             s.point, s.n_independent, s.confidence)
    return out
_TAM_TOKEN_RE = re.compile(r"(TAM[^$]{0,12})\$?\s*[\d.]+\s*[BMK]\b", re.I)
def _fmt_tam_short(v: float) -> str:
    """Compact TAM figure matching the sizing strings' style ('$1.4B', '$437.5M')."""
    v = float(v)
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"
def _rewrite_tam_tokens(text: str, canonical: float) -> str:
    """Replace every 'TAM $X<unit>' figure in a free-text string with the canonical
    TAM, preserving the surrounding qualitative wording (serviceability factors etc.)."""
    if not text:
        return text
    return _TAM_TOKEN_RE.sub(lambda m: m.group(1) + _fmt_tam_short(canonical), text)
def _resync_sam_after_triangulation(sizing: dict, old_tam_mid, new_tam_mid) -> dict:
    """D15 / audit C1 fix: after triangulation moves the headline TAM, re-derive SAM
    from it so the funnel and its buyer-facing strings stay coherent.

    SAM = TAM x serviceability, and the serviceability fraction is a market-structure
    property independent of the absolute TAM — so SAM scales by the SAME ratio the TAM
    moved (preserving that fraction), and every 'TAM $X' token in sam.calculation /
    serviceability_waterfall is rewritten to the canonical headline. SOM keeps its own
    anchor (analog/capacity) and is re-clamped to SAM downstream by
    _enforce_sizing_ordering. No-op when TAM didn't move or inputs are unusable."""
    old = old_tam_mid if isinstance(old_tam_mid, (int, float)) and not isinstance(old_tam_mid, bool) else None
    new = new_tam_mid if isinstance(new_tam_mid, (int, float)) and not isinstance(new_tam_mid, bool) else None
    if not old or not new or old <= 0 or new <= 0:
        return sizing
    ratio = new / old
    if abs(ratio - 1.0) < 1e-9:
        return sizing  # triangulation didn't move the headline
    out = dict(sizing)
    sam = dict(out.get("sam") or {})
    if not sam:
        return out
    for k in ("low", "mid", "high"):
        v = sam.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            sam[k] = round(v * ratio)
    sam["calculation"] = _rewrite_tam_tokens(sam.get("calculation") or "", new)
    sam["serviceability_waterfall"] = _rewrite_tam_tokens(sam.get("serviceability_waterfall") or "", new)
    out["sam"] = sam
    return out
def _renormalize_segmentation(sizing: dict, new_tam_mid) -> dict:
    """Rescale segmentation tam_usd to a new TAM headline (after triangulation).
    Uses share_pct when present; otherwise scales by the old segment-sum. Keeps
    segments consistent with the headline so the validation gate doesn't block."""
    if not isinstance(new_tam_mid, (int, float)) or new_tam_mid <= 0:
        return sizing
    segs = sizing.get("segmentation") or []
    if not segs:
        return sizing
    old_sum = sum(float(s.get("tam_usd") or 0) for s in segs if isinstance(s, dict))
    out = dict(sizing)
    new_segs = []
    for s in segs:
        if not isinstance(s, dict):
            new_segs.append(s); continue
        s2 = dict(s)
        share = s.get("share_pct")
        if isinstance(share, (int, float)) and share:
            s2["tam_usd"] = round(float(share) / 100.0 * new_tam_mid)
        elif old_sum > 0 and isinstance(s.get("tam_usd"), (int, float)):
            s2["tam_usd"] = round(float(s["tam_usd"]) / old_sum * new_tam_mid)
        new_segs.append(s2)
    out["segmentation"] = new_segs
    return out
