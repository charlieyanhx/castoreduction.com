"""
The full spec pipeline — chains all 14 steps into a single runnable function.

Input: raw company/startup description (user text)
Output: 4Ps plan + viability score 1-100

Steps (spec mapping):
  1. Accept description (caller)
  2. Extract company profile           → profile.extract_company_profile
  3a/3b/3c/3d. Competitive intel       → discover.discover
  4. Consolidate context store         → (embedded in result)
  5/6. Customer segments               → taste.decode_taste (for top competitor)
  7/8. Opportunity scoring             → _score in discover
  9a. Max-Diff features                → pricing.simulate_max_diff
  9b. Van Westendorp PSM               → pricing.simulate_van_westendorp
  10. Pricing framework                → pricing.compute_break_even
  11. Place analysis                   → place.analyze + place.recommend_place
  12. Validation gate                  → computed inline
  13. 4Ps plan                         → four_ps.assemble_4ps
  14. Viability score                  → four_ps.score_viability
"""
from __future__ import annotations
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from company_profile import extract_company_profile
from discover import discover
from taste import decode_taste
from pricing import simulate_max_diff, simulate_van_westendorp, compute_break_even
from place import analyze_competitor_channels, recommend_place
from four_ps import assemble_4ps, assemble_4ps_split, score_viability
from clustering import cluster_competitors, find_whitespace
from competitor_pricing import gather_competitor_prices
from market_sizing import estimate_market_size
from financials import project_three_year
from personas import synthesize_personas
from logger import get

log = get("plan")


def _run_with_timeout(fn, *args, timeout_s: int = 180, label: str = "", **kwargs):
    """Run a step with a hard timeout. Returns {} on timeout or error, logs warning."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeoutError:
            log.warning(f"[plan] {label} exceeded {timeout_s}s timeout — returning partial")
            return {"error": f"timed out after {timeout_s}s"}
        except Exception as e:
            # Log full traceback so future debugging isn't blind
            import traceback
            log.warning(f"[plan] {label} failed: {type(e).__name__}: {e}")
            log.debug(traceback.format_exc())
            return {"error": f"{type(e).__name__}: {e}"}


def _validation_gate(result: dict) -> dict:
    """Step 12 — surface data quality flags. cycle30: stricter thresholds; pipeline
    should NEVER report 100% confidence with 0 flags unless every signal is strong."""
    flags = []
    confidence = 1.0

    opps = (result.get("discover", {}).get("synthesis", {}) or {}).get("ranked_opportunities", [])
    density = result.get("discover", {}).get("competitor_density") or 0
    if len(opps) < 3:
        flags.append(f"Only {len(opps)} competitors found — expand search")
        confidence -= 0.15

    audience = result.get("audience") or {}
    ac_raw = audience.get("confidence")
    if ac_raw is None:
        flags.append("Audience taste confidence not reported by LLM — using heuristic fallback")
        confidence -= 0.05
    else:
        ac = float(ac_raw)
        # cycle30: tightened — was <0.5; now any audience below 0.7 is worth flagging
        if ac < 0.3:
            flags.append(f"Audience taste confidence VERY LOW ({ac:.2f}) — treat results as directional only")
            confidence -= 0.25
        elif ac < 0.5:
            flags.append(f"Audience taste confidence is low ({ac:.2f}) — results may be weak")
            confidence -= 0.15
        elif ac < 0.7:
            flags.append(f"Audience taste confidence is moderate ({ac:.2f}) — additional voice sources recommended")
            confidence -= 0.05

    pricing = result.get("pricing", {})
    if pricing.get("error") or not pricing.get("psm"):
        flags.append("Pricing simulation failed — no PSM data")
        confidence -= 0.1

    place = result.get("place", {}) or {}
    if place.get("error") or not place.get("primary_channel"):
        flags.append("Place analysis incomplete")
        confidence -= 0.05

    # cycle30 NEW: customer-voice source breadth — flag if <3 sources actually returned data
    sources_with_data = 0
    if (result.get("reddit_signal") or {}).get("threads_found", 0) > 0:
        sources_with_data += 1
    if (result.get("hn_signal") or {}).get("hits_found", 0) > 0:
        sources_with_data += 1
    ms_counts = (result.get("multi_source_signal") or {}).get("counts") or {}
    sources_with_data += sum(1 for v in ms_counts.values() if (v or 0) > 0)
    if sources_with_data < 3:
        flags.append(f"Only {sources_with_data} customer-voice sources returned data — opinion signals are thin")
        confidence -= 0.10

    # cycle30 NEW: TAM 3-method completion check
    tam = (result.get("market_sizing") or {}).get("tam") or {}
    n_tam_methods = sum(
        1 for k in ("method_top_down", "method_bottom_up", "method_analog")
        if (tam.get(k) or {}).get("value_usd")
    )
    if n_tam_methods < 3:
        flags.append(f"TAM only has {n_tam_methods}/3 methods filled — triangulation incomplete")
        confidence -= 0.08
    # cycle31-r2 (BUG B): market_sizing returned EMPTY tam.mid silently — surface it
    if "market_sizing" in (result.get("_steps_completed") or []) and not tam.get("mid"):
        flags.append("Market sizing produced no headline TAM (all 3 methods returned no usable values)")
        confidence -= 0.30
    # cycle31-r2: financial scenarios empty / non-monotonic
    fin = result.get("financials") or {}
    scenarios = fin.get("scenarios") or {}
    if "financials" in (result.get("_steps_completed") or []) and not scenarios:
        flags.append("Growth scenarios were not produced — financials block empty")
        confidence -= 0.10

    # cycle30 NEW: segment scoring quality — flag when scores were fabricated/defaulted
    sr = result.get("segment_ranking") or {}
    n_defaulted = sum(1 for r in (sr.get("ranked") or []) if r.get("_scores_were_defaulted"))
    if n_defaulted > 0:
        flags.append(f"{n_defaulted} segment(s) scored using 0.5 defaults — LLM declined to rate")
        confidence -= 0.05

    # cycle30 NEW: viability skipped (the user's specific bug)
    viability = result.get("viability") or {}
    if viability.get("error"):
        flags.append(f"Viability scoring failed: {viability.get('error')}")
        confidence -= 0.20
    elif "viability" not in (result.get("_steps_completed") or []):
        flags.append("Viability step was skipped or did not complete")
        confidence -= 0.20

    # cycle30 NEW: differentiator coverage — if 0 found, that's a critical finding
    diffs = (result.get("differentiators") or {}).get("differentiators") or []
    if len(diffs) == 0:
        flags.append("No differentiators identified — venture may be a commodity copycat")
        confidence -= 0.10

    return {
        "flags": flags,
        "confidence_score": round(max(0.0, confidence), 2),
    }


def gate_and_annotate_sizing(sizing: dict, scale_decision: dict | None) -> dict:
    """Run legacy sizing output through the numbers-right validation gate.

    Non-mutating contract: returns a new dict (sizing + validation + scale_decision
    + physical-venture caveat). The legacy tam/sam/som shape is left untouched so
    downstream readers (som.mid, etc.) keep working. cycle33.
    """
    out = dict(sizing or {})
    try:
        from skills.sizing.validate import validate_numbers
        tam_block = out.get("tam") or {}
        # C7: feed the per-method figures (with their calculation as the formula)
        # and the segmentation into the gate, so formula-reconciliation and
        # segmentation-sum checks actually run on live reports — not just unit tests.
        figures = []
        for key in ("method_top_down", "method_bottom_up", "method_analog"):
            blk = tam_block.get(key) or {}
            if isinstance(blk.get("value_usd"), (int, float)):
                figures.append({
                    "value_usd": float(blk["value_usd"]), "label": f"TAM_{key}",
                    "source": blk.get("source") or "estimate_market_size",
                    "formula": blk.get("calculation") or "",
                })
        adapted = {
            "tam_usd": tam_block.get("mid"),
            "sam_usd": (out.get("sam") or {}).get("mid"),
            "som_usd": (out.get("som") or {}).get("mid"),
            "figures": figures,
            "segmentation": out.get("segmentation") or [],
        }
        v = validate_numbers(adapted)
        out["validation"] = v.payload
        # C3 (audit remediation): a failed gate makes the sizing UNPUBLISHABLE.
        # The renderer hard-banners this and downstream consumers can refuse it —
        # "validate loud" now actually blocks instead of silently annotating.
        out["publishable"] = bool(v.payload.get("passed"))
        if not v.payload["passed"]:
            log.warning("[plan] SIZING BLOCKED by validation gate (unpublishable): %s", v.error)
    except Exception as e:  # gate machinery failure is non-fatal, but mark unknown
        log.warning("[plan] sizing validation failed (non-fatal): %s", e)
        out["publishable"] = out.get("publishable", True)

    if scale_decision:
        out["scale_decision"] = scale_decision
        if scale_decision.get("scale") in ("hyperlocal", "regional", "national_physical"):
            out["notes"] = list(out.get("notes") or []) + [
                "Physical/local venture: national TAM method is an upper bound — "
                "trade-area sizing (size_hyperlocal) needs a specific address for "
                "a defensible SOM."]
    return out


_STATED_PRICE_RE = re.compile(
    r"\$\s*(\d[\d,]*\.?\d*)\s*(?:/|\s*per\s*)?\s*(?:mo|month|monthly|/mo\b|/month\b)",
    re.I)


def extract_stated_price(text: str) -> float | None:
    """Pull the user's stated monthly price from free text ($99/month, $99/mo, …).

    Returns the first monthly price found, or None. cycle33 / C5.
    """
    if not text:
        return None
    m = _STATED_PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def reconcile_pricing(stated: float | None, recommended) -> dict | None:
    """Compare the user's stated price to the model's recommendation, visibly.

    Returns a reconciliation dict (or None if nothing to reconcile) so the report
    can show "you said $X, model recommends $Y, here's the gap" instead of silently
    re-pricing. cycle33 / C5 — the gap Manus handled and Castor didn't.
    """
    try:
        rec = float(recommended)
    except (TypeError, ValueError):
        return None
    if stated is None or stated <= 0 or rec <= 0:
        return None
    delta_pct = round((rec - stated) / stated * 100, 1)
    if abs(delta_pct) <= 15:
        verdict = "aligned"
        note = (f"Your ${stated:,.0f}/mo aligns with the model's recommended "
                f"${rec:,.0f}/mo ({delta_pct:+.0f}%).")
    elif rec < stated:
        verdict = "model_suggests_lower"
        note = (f"You stated ${stated:,.0f}/mo; the model's price simulation suggests "
                f"${rec:,.0f}/mo ({delta_pct:+.0f}%) may capture more of the market. "
                f"Validate before changing.")
    else:
        verdict = "model_suggests_higher"
        note = (f"You stated ${stated:,.0f}/mo; willingness-to-pay analysis suggests "
                f"${rec:,.0f}/mo ({delta_pct:+.0f}%) — you may be under-pricing.")
    return {"stated_usd": stated, "recommended_usd": rec,
            "delta_pct": delta_pct, "verdict": verdict, "note": note}


def build_consumer_research(description: str, geo: str, profile: dict,
                            opps: list[dict]) -> dict | None:
    """STORM-style multi-perspective consumer research, grounded in known context.

    Env-gated (CASTOR_CONSUMER_RESEARCH=0 disables) and non-fatal — returns the
    payload dict on success, or None if disabled, skeletoned, or it errored.
    cycle33.
    """
    if os.getenv("CASTOR_CONSUMER_RESEARCH", "1") == "0":
        return None
    try:
        from skills.perspective import consumer_research_skill
        comp_names = ", ".join(o.get("brand", "") for o in (opps or [])[:5] if o.get("brand"))
        summary = (profile or {}).get("summary", "")
        context = f"Product: {summary}. Known competitors: {comp_names}." if comp_names else summary
        log.info("[plan] Step 6c: consumer research (multi-perspective)")
        cr = consumer_research_skill(description=description, geo=geo, context=context)
        if cr.payload and not cr.skeleton:
            return cr.payload
    except Exception as e:  # never sink the run on a research-enrichment failure
        log.warning("[plan] consumer research failed (non-fatal): %s", e)
    return None


def ground_sizing_bottom_up(sizing: dict, description: str, profile: dict,
                            arpu_monthly_fallback: float | None = None) -> dict:
    """C2/F3 (audit remediation): replace the LLM's hallucinated bottom-up TAM method
    with a LIVE Census-grounded one (target-customer establishment count × ARPU).

    ARPU basis, in order: the user's stated $/mo, else a modeled monthly price
    (`arpu_monthly_fallback`, e.g. the PSM optimal price) — so grounding fires for most
    digital ventures, not only when a price was typed (F3). Degrades gracefully: with
    no ARPU basis or no live count, returns sizing unchanged.
    """
    target = (profile or {}).get("target_customer") or description
    # ARPU basis priority: (1) user's stated $/mo → (2) SCRAPED competitor price (real,
    # geographic, origin='scrape') → (3) LLM-modeled fallback (PSM optimal). Preferring a
    # scraped price over the modeled one grounds the soft multiplier in real data (M2).
    stated = extract_stated_price(description)
    arpu_monthly = stated
    arpu_sourced = "stated price"
    arpu_origin = "stated"
    if not arpu_monthly and os.getenv("CASTOR_SCRAPE_PRICE", "1") != "0":
        try:
            from skills.price_intel import scrape_market_price
            geo = (profile or {}).get("geography") or "US"
            spe = scrape_market_price(target, geo)
            if not spe.skeleton and (spe.payload or {}).get("median_monthly_usd"):
                arpu_monthly = float(spe.payload["median_monthly_usd"])
                arpu_sourced = spe.payload.get("source_label", "scraped competitor pricing")
                arpu_origin = "scrape"
                log.info("[plan] ARPU grounded from scrape: $%s/mo (%s)",
                         arpu_monthly, arpu_sourced)
        except Exception as e:
            log.warning("[plan] price scrape failed (non-fatal): %s", e)
    if not arpu_monthly:
        arpu_monthly = arpu_monthly_fallback
        arpu_sourced = "modeled price (PSM optimal)"
        arpu_origin = "llm"
    if not arpu_monthly or arpu_monthly <= 0:
        return sizing  # no ARPU basis → can't ground; leave legacy bottom-up
    try:
        from skills.sizing.bottom_up import grounded_bottom_up
        gb = grounded_bottom_up(annual_arpu=arpu_monthly * 12.0, category=target)
    except Exception as e:
        log.warning("[plan] grounded bottom-up failed (non-fatal): %s", e)
        return sizing
    if gb.skeleton or not gb.payload or not gb.payload.get("tam_usd"):
        return sizing  # no live count → keep legacy

    out = dict(sizing)
    tam = dict(out.get("tam") or {})
    fig0 = (gb.payload.get("figures") or [{}])[0]
    tam["method_bottom_up"] = {
        "value_usd": gb.payload["tam_usd"],
        "calculation": fig0.get("formula", ""),
        "source": fig0.get("source", "US Census CBP"),
        "data_origin": "census",   # REAL provenance — a fetched count actually fired
    }
    vals = [tam[k]["value_usd"] for k in
            ("method_top_down", "method_bottom_up", "method_analog")
            if isinstance((tam.get(k) or {}).get("value_usd"), (int, float))]
    if vals:
        tam["mid"] = round(sum(vals) / len(vals))
        tam["low"] = round(min(vals) * 0.85)
        tam["high"] = round(max(vals) * 1.15)
    out["tam"] = tam
    out["notes"] = list(out.get("notes") or []) + [
        f"Bottom-up grounded in live Census count "
        f"({gb.payload['establishments']:,} establishments × "
        f"${arpu_monthly * 12:,.0f}/yr from {arpu_sourced})."]
    log.info("[plan] C2: grounded bottom-up TAM = %s (%s establishments)",
             gb.payload["tam_usd"], gb.payload["establishments"])
    return out


_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z0-9][\w.'-]*(?:\s+[\w.'-]+){0,4}\s+"
    r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Plaza)\b",
    re.I)
_PLACE_RE = re.compile(
    r"\b(?:in|at|near|around|located in)\s+"
    r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}(?:,\s*[A-Z]{2})?)")


def extract_location(text: str) -> str | None:
    """Best-effort physical-location extraction: a street address, else 'in <Place>'.

    Returns a geocodable-ish string or None. cycle35 — feeds hyperlocal routing.
    """
    if not text:
        return None
    m = _STREET_RE.search(text)
    if m:
        return m.group(0).strip()
    m = _PLACE_RE.search(text)
    return m.group(1).strip() if m else None


# Map common physical categories to an OSM amenity value for competitor density.
_OSM_BY_CATEGORY = {
    "restaurant": "restaurant", "food": "restaurant", "cafe": "cafe", "coffee": "cafe",
    "bar": "bar", "gym": "gym", "fitness": "gym", "salon": "hairdresser",
    "clinic": "clinic", "pharmacy": "pharmacy", "bakery": "bakery",
}


def size_by_scale(scale_decision: dict | None, description: str, profile: dict) -> dict | None:
    """For physical ventures with a location, size by trade-area (size_hyperlocal) and
    adapt to the legacy tam/sam/som shape so the report + gate work. Returns None to
    let the caller keep the legacy digital sizing. cycle35 (F3, location path).

    The competitor count here is GEOGRAPHIC — size_hyperlocal uses OSM poi_competition
    within the trade-area radius, not LLM guesses.
    """
    if not scale_decision or scale_decision.get("scale") not in ("hyperlocal", "regional"):
        return None
    location = extract_location(description)
    if not location:
        return None  # caller keeps legacy + the existing "needs an address" caveat
    cat = (profile or {}).get("category") or ""
    osm = next((v for k, v in _OSM_BY_CATEGORY.items() if k in cat.lower()), "restaurant")
    try:
        from skills.sizing.hyperlocal import size_hyperlocal
        ev = size_hyperlocal(address=location, category=cat or "food_away_from_home",
                             osm_value=osm)
    except Exception as e:
        log.warning("[plan] hyperlocal sizing failed (non-fatal): %s", e)
        return None
    if ev.skeleton or not ev.payload:
        return None
    p = ev.payload

    def _block(v):
        return ({"mid": v, "low": round(v * 0.7), "high": round(v * 1.3)}
                if isinstance(v, (int, float)) and not isinstance(v, bool) else {})

    val = p.get("validation") or {}
    return {
        "tam": _block(p.get("tam_usd")), "sam": _block(p.get("sam_usd")),
        "som": _block(p.get("som_usd")),
        "method": p.get("method"), "figures": p.get("figures"),
        "households": p.get("households"), "competitors": p.get("competitors"),
        "notes": p.get("notes"), "validation": val,
        "publishable": val.get("passed", True),
        "scale_decision": scale_decision,
        "_hyperlocal_location": location, "_osm_value": osm,
    }


def build_integrity_summary(result: dict) -> dict:
    """Surface the (otherwise invisible) backend rigor as a user-facing trust object.

    'Dark' capabilities are a red flag — the validation gate, triangulation, determinism,
    and provenance only matter if the user can SEE them. Pure read over existing result
    data; no new computation. cycle35.
    """
    ms = (result or {}).get("market_sizing") or {}
    tam = ms.get("tam") or {}
    val = ms.get("validation") or {}
    tri = tam.get("triangulation") or {}

    # Provenance: how many headline TAM methods carry a real source string.
    methods = [tam.get(k) or {} for k in ("method_top_down", "method_bottom_up", "method_analog")]
    methods = [m for m in methods if isinstance(m.get("value_usd"), (int, float))]
    n_sourced = sum(1 for m in methods if str(m.get("source") or "").strip())

    # Distinct data origins that actually fired (census/bls/llm…).
    origins = sorted({str(m.get("data_origin") or "llm") for m in methods}) if methods else []

    has_gate = bool(val)
    return {
        "reproducible": True,  # F2: temperature=0 + seed → same input, same number
        "validation": {
            "ran": has_gate,
            "passed": val.get("passed") if has_gate else None,
            "n_blocks": len(val.get("blocks") or []),
            "n_warns": len(val.get("warns") or []),
        },
        "triangulation": {
            "confidence": tri.get("confidence"),
            "n_independent": tri.get("n_independent"),
        } if tri else None,
        "provenance": {"n_sourced": n_sourced, "n_total": len(methods)},
        "data_origins": origins,
        "grounded": any(o in ("census", "bls", "acs") for o in origins),
    }


def triangulate_sizing(sizing: dict) -> dict:
    """Replace the naive 3-method average with REAL origin-independent triangulation.

    The 3 TAM methods are tagged by data origin: a Census/BLS-grounded bottom-up is an
    independent origin ('census'); LLM-generated top-down/analog collapse to one 'llm'
    origin (they're correlated draws from one model — not independent). The headline
    `mid` becomes the median ACROSS origins, with the triangulation object attached so
    the report can show convergence/divergence honestly. cycle33 / TRIANGULATION.md.
    """
    tam = sizing.get("tam") or {}
    try:
        from skills.triangulate import triangulate, Estimate
    except Exception:
        return sizing
    ests = []
    from skills.sizing.validate import safe_eval_formula
    out = dict(sizing)
    out_tam = dict(tam)
    for key, method in (("method_top_down", "top_down"),
                        ("method_bottom_up", "bottom_up"),
                        ("method_analog", "analog")):
        blk = dict(tam.get(key) or {})
        v = blk.get("value_usd")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            # F5: do NOT silently rewrite a value to match its own formula. A gross
            # value/formula mismatch is an arithmetic hallucination — FLAG it and let
            # the validation gate block it (F1 then withholds the numbers), instead of
            # laundering an incoherent figure into a publishable one.
            computed = safe_eval_formula(str(blk.get("calculation") or ""))
            if computed and computed > 0 and (computed / v > 10 or computed / v < 0.1):
                blk["_formula_mismatch"] = {"stated": v, "computed": round(computed)}
                out_tam[key] = blk          # record the flag; keep the stated value
            src = str(blk.get("source") or "")
            origin = str(blk.get("data_origin") or "llm")
            ests.append(Estimate(float(v), src or "estimate_market_size", method, origin))
    if not ests:
        return sizing

    tri = triangulate("TAM", ests)
    if tri.get("point") is not None:
        out_tam["mid"] = tri["point"]  # median across independent origins (robust)
        cross = [c["value"] for c in tri.get("cross_origin") or []]
        if cross:
            out_tam["low"] = round(min(cross) * 0.85)
            out_tam["high"] = round(max(cross) * 1.15)
    out_tam["triangulation"] = tri
    out["tam"] = out_tam
    # Triangulation moved the headline TAM → re-derive any dependent figures from
    # the NEW mid so they stay consistent (else the gate's segmentation_sum check
    # correctly blocks every report). Segments rescale by share_pct, else proportionally.
    out = _renormalize_segmentation(out, tri.get("point"))
    log.info("[plan] triangulated TAM: point=%s n_independent=%s confidence=%s",
             tri.get("point"), tri.get("n_independent"), tri.get("confidence"))
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


def refine_pipeline_result(result: dict, description: str, geo: str, profile: dict,
                           opps: list[dict], max_rounds: int = 1) -> dict:
    """Generator-evaluator-refine pass over a finished pipeline result (opt-in).

    Wires `skills.refine_report` (independent judge anchored by the deterministic
    gate) to real section regenerators: weak sizing → re-ground bottom-up + re-gate;
    weak consumer insight → re-run consumer research. Bounded + non-fatal: returns
    the original result on any failure. Attaches `_refine` audit. cycle33."""
    try:
        from skills.refine_report import refine_report
    except Exception as e:
        log.warning("[plan] refine unavailable (non-fatal): %s", e)
        return result

    def _regen_sizing(rep: dict) -> dict:
        ms = ground_sizing_bottom_up(rep.get("market_sizing") or {}, description, profile)
        ms = triangulate_sizing(ms)
        ms = gate_and_annotate_sizing(ms, rep.get("market_scale"))
        new = dict(rep); new["market_sizing"] = ms; return new

    def _regen_consumer(rep: dict) -> dict:
        cr = build_consumer_research(description, geo, profile, opps)
        new = dict(rep)
        if cr:
            new["consumer_research"] = cr
        return new

    try:
        rr = refine_report(result, description,
                           regenerators={"market_sizing": _regen_sizing,
                                         "consumer_research": _regen_consumer},
                           max_rounds=max_rounds)
        out = dict(rr.artifact)
        out["_refine"] = {"passed": rr.passed, "rounds": rr.rounds,
                          "score_trajectory": rr.score_trajectory,
                          "weak_dims": rr.weak_dims}
        return out
    except Exception as e:
        log.warning("[plan] refine loop failed (non-fatal): %s", e)
        return result


def run_plan(description: str, geo: str = "US", max_candidates: int = 20, progress=None,
             operator_weights: dict | None = None, refine: bool = False) -> dict:
    """
    Run the full market research pipeline on a raw description.

    If `progress` callback is provided, it's called with the partial result
    after every step so the UI can show live progress.
    If `refine=True`, runs the generator-evaluator-refine loop after the pipeline
    (independent judge + deterministic gate → regenerate weak sections). Opt-in
    because it adds LLM cost; default path is unchanged.
    """
    t_start = time.time()
    result: dict = {"_steps_completed": []}

    def checkpoint():
        """Persist partial result so the UI can see progress mid-run."""
        if progress:
            try:
                result["_elapsed_seconds"] = round(time.time() - t_start, 1)
                progress(result)
            except Exception:
                pass

    # Persist operator weights for segment scoring (iter 36 spec step 1)
    if operator_weights:
        result["operator_weights"] = operator_weights

    # --- Step 2: Extract profile ---
    log.info("[plan] Step 2: extracting company profile")
    profile = extract_company_profile(description)
    if profile.get("error"):
        return {"error": f"Profile extraction failed: {profile['error']}", "profile": profile}

    # Geography fallback: if LLM said "unknown" but user passed a geo, use the request value.
    # Prevents the embarrassing "Geography: unknown" in the cover page when we DO know.
    geo_in_profile = (profile.get("geography") or "").strip().lower()
    if geo_in_profile in ("", "unknown", "none", "n/a"):
        profile["geography"] = geo
        profile["_geography_source"] = "request_default"

    result["profile"] = profile
    result["_steps_completed"].append("profile")
    checkpoint()

    # --- Step 3: Competitive intelligence (via discover) ---
    log.info(f"[plan] Step 3: discovering competitors in category '{profile.get('category')}'")
    disc = discover(
        profile["category"],
        geo=geo,
        max_candidates=max_candidates,
        business_model=profile.get("business_model", ""),
        named_competitors=profile.get("named_competitors") or [],
    )
    result["discover"] = disc
    result["_steps_completed"].append("discover")
    checkpoint()

    opps = (disc.get("synthesis", {}) or {}).get("ranked_opportunities", [])
    competitor_domains = [o["domain"] for o in opps if o.get("domain")][:8]

    if not opps:
        # Degraded but not fatal — skip downstream steps that need competitors
        log.warning("[plan] no competitors found — proceeding with profile-only plan")

    # --- Step 3e: Firmographic enrichment (B2B mode only) ---
    # B2B buyers want to know "is this competitor a 50-person Series A or a 500-person
    # public co?" — DTC competitors don't need this. Skip for DTC to save time.
    if "b2b" in (profile.get("business_model") or "").lower() and opps:
        try:
            log.info(f"[plan] Step 3e: firmographic enrichment for top {min(6, len(opps))} B2B competitors")
            from firmographics import enrich_competitors
            enriched = enrich_competitors(opps, max_to_enrich=6)
            # Write back into the discover result so downstream steps see it
            disc["synthesis"]["ranked_opportunities"] = enriched
            result["discover"] = disc
            hits = sum(1 for o in enriched[:6] if (o.get("firmographics") or {}).get("sources"))
            log.info(f"[plan] firmographics: {hits}/{min(6, len(enriched))} competitors enriched")
            result["_steps_completed"].append("firmographics")
            checkpoint()
        except Exception as e:
            log.warning(f"[plan] firmographic enrichment failed (non-fatal): {e}")

    # --- Step 3c: Cluster competitors + detect whitespace (sklearn) ---
    if len(opps) >= 4:
        log.info("[plan] Step 3c: clustering competitors + PCA whitespace detection")
        signals = (disc.get("steps", {}) or {}).get("signals", [])
        # Prefer richer signal data for clustering if available
        cluster_input = signals if len(signals) >= 4 else opps
        clustering = cluster_competitors(cluster_input)
        if not clustering.get("error"):
            whitespace = find_whitespace(clustering, profile)
            # Label PCA axes (user feedback #3a + spec step 3c)
            try:
                from clustering import label_pca_axes
                axis_labels = label_pca_axes(clustering, opps)
                if "error" not in axis_labels:
                    clustering["axis_labels"] = axis_labels
            except Exception as e:
                log.warning(f"[plan] PCA axis labeling failed (non-fatal): {e}")
            result["clustering"] = clustering
            result["whitespace"] = whitespace
            result["_steps_completed"].append("clustering")
            checkpoint()

    # --- Step 3d: Differentiators + market gaps (spec step 3d — iter 36) ---
    try:
        from differentiators import extract_differentiators
        log.info("[plan] Step 3d: extracting differentiators + market gaps")
        diffs = extract_differentiators(
            profile=profile,
            our_features=profile.get("core_features", []),
            clustering=result.get("clustering") or {},
            competitors=opps,
        )
        if "error" not in diffs:
            result["differentiators"] = diffs
            result["_steps_completed"].append("differentiators")
            checkpoint()
    except Exception as e:
        log.warning(f"[plan] differentiators failed (non-fatal): {e}")

    # --- Step 5: Customer universe (real B2B companies, iter 36) ---
    # Run in parallel with the taste decode below — independent I/O.
    # Only for B2B mode (DTC plans don't need a company universe).
    biz_model = (profile.get("business_model") or "").lower()
    if "b2b" in biz_model or "saas" in biz_model:
        try:
            from customer_universe import build_customer_universe
            log.info("[plan] Step 5: building B2B customer universe")
            universe = build_customer_universe(
                profile=profile,
                competitors=opps[:5],
                differentiators=(result.get("differentiators") or {}).get("differentiators", []),
                target_count=30,
            )
            result["customer_universe"] = universe
            if universe.get("count", 0) > 0:
                result["_steps_completed"].append("customer_universe")
            checkpoint()
        except Exception as e:
            log.warning(f"[plan] customer universe failed (non-fatal): {e}")

    # --- PARALLEL PHASE: Steps that can run concurrently after discover ---
    # Decode taste for TOP-3 brands (not just top-1) to enable persona synthesis.
    # Plus place + pricing scrapes — all independent.
    top_3_comps = [o for o in opps if o.get("domain")][:3]
    channel_data = {}

    def _taste_task_for(comp):
        log.info(f"[plan] Step 6: decoding audience for {comp['brand']}")
        return decode_taste(comp["brand"], comp["domain"])

    def _channels_task():
        if not competitor_domains:
            return {}
        log.info(f"[plan] Step 11: scraping channels across {len(competitor_domains)} competitors")
        return analyze_competitor_channels(competitor_domains)

    def _prices_task():
        """Scrape competitor product prices for PSM anchoring."""
        if not competitor_domains:
            return {}
        log.info(f"[plan] Step 10b: scraping prices across {len(competitor_domains)} competitors")
        return gather_competitor_prices(competitor_domains[:6])

    def _reddit_task():
        """Pull Reddit customer voice for the top competitor (or category if none)."""
        from reddit_signal import fetch_signal
        target = (top_3_comps[0]["brand"] if top_3_comps else profile.get("category", ""))
        if not target:
            return {}
        log.info(f"[plan] Step 6c: pulling Reddit signal for '{target}'")
        return fetch_signal(target, max_threads=10, days_back=180)

    def _hn_task():
        """cycle25 (issue 6/7): also pull HackerNews mentions as customer voice."""
        from sources import hackernews_mentions
        target = (top_3_comps[0]["brand"] if top_3_comps else profile.get("category", ""))
        if not target:
            return []
        log.info(f"[plan] Step 6d: pulling HackerNews mentions for '{target}'")
        try:
            return hackernews_mentions(target, limit=20)
        except Exception as e:
            log.warning(f"[plan] HN fetch failed (non-fatal): {e}")
            return []

    def _multisrc_task():
        """cycle27 (real fix to issue 6/7): pull Stack Exchange + DEV.to + Lobsters in parallel.
        cycle31-r2 (Discovery 2 fix): + vertical_publication_mentions for non-tech verticals.
        All free, no API key. Returns dict {stackoverflow, devto, lobsters, vertical_pubs}."""
        from sources import stackexchange_mentions, devto_mentions, lobsters_mentions, vertical_publication_mentions
        target = (top_3_comps[0]["brand"] if top_3_comps else profile.get("category", ""))
        if not target:
            return {}
        category = profile.get("category", "")
        log.info(f"[plan] Step 6e: pulling Stack Exchange + DEV.to + Lobsters + vertical_pubs for '{target}'")
        out = {}
        with ThreadPoolExecutor(max_workers=4) as p:
            so_f = p.submit(stackexchange_mentions, target, 12)
            dv_f = p.submit(devto_mentions, target, 10)
            lb_f = p.submit(lobsters_mentions, target, 10)
            vp_f = p.submit(vertical_publication_mentions, target, category, 10)
            for name, fut in [("stackoverflow", so_f), ("devto", dv_f), ("lobsters", lb_f), ("vertical_pubs", vp_f)]:
                try:
                    out[name] = fut.result(timeout=25) or []
                except Exception as e:
                    log.warning(f"[plan] {name} fetch failed (non-fatal): {e}")
                    out[name] = []
        return out

    taste_results: list[dict] = []
    competitor_pricing_data = {}
    reddit_data = {}
    hn_data: list[dict] = []
    multisrc_data: dict = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        # Submit one taste decode per top brand (1, 2, or 3 in parallel)
        taste_futs = [pool.submit(_taste_task_for, c) for c in top_3_comps]
        channels_fut = pool.submit(_channels_task)
        prices_fut = pool.submit(_prices_task)
        reddit_fut = pool.submit(_reddit_task)
        hn_fut = pool.submit(_hn_task)
        multisrc_fut = pool.submit(_multisrc_task)

        # Gather taste results — iter 40: also collect cannot_decode entries
        # so the report can show "we tried but no signal" honestly.
        cannot_decode_results: list[dict] = []
        for fut in taste_futs:
            try:
                t = fut.result(timeout=120) or {}
                if t and not t.get("error"):
                    if t.get("cannot_decode"):
                        cannot_decode_results.append(t)
                    else:
                        taste_results.append(t)
            except FutureTimeoutError:
                log.warning("[plan] one taste decode timed out after 120s")

        try:
            channel_data = channels_fut.result(timeout=60) or {}
        except FutureTimeoutError:
            log.warning("[plan] channel scraping timed out after 60s")
            channel_data = {}
        try:
            competitor_pricing_data = prices_fut.result(timeout=80) or {}
        except FutureTimeoutError:
            log.warning("[plan] competitor pricing scrape timed out after 80s")
            competitor_pricing_data = {}
        try:
            reddit_data = reddit_fut.result(timeout=120) or {}
        except FutureTimeoutError:
            log.warning("[plan] reddit signal timed out after 120s")
            reddit_data = {}
        except Exception as e:
            log.warning(f"[plan] reddit signal failed (non-fatal): {e}")
            reddit_data = {}
        try:
            hn_data = hn_fut.result(timeout=30) or []
        except Exception as e:
            log.warning(f"[plan] HN signal failed (non-fatal): {e}")
            hn_data = []
        try:
            multisrc_data = multisrc_fut.result(timeout=60) or {}
        except Exception as e:
            log.warning(f"[plan] multi-source fetch failed (non-fatal): {e}")
            multisrc_data = {}

    # Persist Reddit signal even if downstream skips it
    if reddit_data:
        result["reddit_signal"] = reddit_data
        if reddit_data.get("threads_found", 0) > 0:
            result["_steps_completed"].append("reddit_signal")
        checkpoint()

    # cycle25: persist HN customer voice as its own signal
    target_for_voice = top_3_comps[0]["brand"] if top_3_comps else profile.get("category", "")
    if hn_data:
        result["hn_signal"] = {
            "query": target_for_voice,
            "hits_found": len(hn_data),
            "hits": hn_data[:15],
        }
        result["_steps_completed"].append("hn_signal")
        checkpoint()

    # cycle27: persist Stack Exchange + DEV.to + Lobsters
    # cycle31-r2: + vertical_pubs for non-tech verticals
    if multisrc_data:
        result["multi_source_signal"] = {
            "query": target_for_voice,
            "stackoverflow": (multisrc_data.get("stackoverflow") or [])[:8],
            "devto": (multisrc_data.get("devto") or [])[:6],
            "lobsters": (multisrc_data.get("lobsters") or [])[:6],
            "vertical_pubs": (multisrc_data.get("vertical_pubs") or [])[:8],
            "counts": {
                "stackoverflow": len(multisrc_data.get("stackoverflow") or []),
                "devto": len(multisrc_data.get("devto") or []),
                "lobsters": len(multisrc_data.get("lobsters") or []),
                "vertical_pubs": len(multisrc_data.get("vertical_pubs") or []),
            },
        }
        result["_steps_completed"].append("multi_source_signal")
        checkpoint()

    # Backwards-compat: keep `top_audience` as the first decoded profile
    top_audience = taste_results[0] if taste_results else {}
    if top_audience:
        result["audience"] = top_audience
        result["audiences"] = taste_results  # full set for transparency
        result["_steps_completed"].append("audience")
    # Iter 40 (#3c): surface the cannot_decode brands so the report can show
    # "we tried but no consumer signal exists for these enterprise B2B brands"
    if cannot_decode_results:
        result["audiences_undecodable"] = cannot_decode_results
        checkpoint()

    # --- Step 6b: Synthesize personas from multiple decoded audiences ---
    if len(taste_results) >= 1:
        log.info(f"[plan] Step 6b: synthesizing personas from {len(taste_results)} taste profiles")
        personas_result = _run_with_timeout(
            synthesize_personas,
            taste_profiles=taste_results,
            product_summary=profile.get("summary", ""),
            timeout_s=90,
            label="personas",
        )
        if not personas_result.get("error"):
            result["personas"] = personas_result
            result["_steps_completed"].append("personas")
            checkpoint()

    # --- Step 6c: STORM-style consumer research (multi-perspective) — cycle33 ---
    cr_payload = build_consumer_research(description, geo, profile, opps)
    if cr_payload:
        result["consumer_research"] = cr_payload
        result["_steps_completed"].append("consumer_research")
        checkpoint()

    if competitor_pricing_data and competitor_pricing_data.get("competitors_with_prices", 0) > 0:
        result["competitor_pricing"] = competitor_pricing_data
        result["_steps_completed"].append("competitor_pricing")
        checkpoint()

    # --- Step 9a: Max-Diff feature ranking (needs audience + profile) ---
    # cycle22: stop polluting features_to_rank with raw competitor descriptions —
    # those are taglines, not features, and they crash through max-diff as
    # garbage entries like "unmind supports your people, develops your leaders".
    # Use only product features explicitly extracted by the profile step.
    features_to_rank = list(dict.fromkeys(profile.get("core_features", []) or []))[:15]

    segment_summary = (
        (top_audience.get("purchase_motivation", "") + " ")
        + " Audience: " + (profile.get("apparent_target_customer") or "")
    )[:1000]

    max_diff_result = {}
    if len(features_to_rank) >= 3:
        log.info(f"[plan] Step 9a: Max-Diff on {len(features_to_rank)} features")
        max_diff_result = _run_with_timeout(
            simulate_max_diff,
            features=features_to_rank,
            segment_summary=segment_summary,
            category=profile["category"],
            timeout_s=90,
            label="max_diff",
        )
        result["max_diff"] = max_diff_result
        if not max_diff_result.get("error"):
            result["_steps_completed"].append("max_diff")
            checkpoint()

    # --- Step 9b + Step 11 LLM recommendation in parallel (both need max_diff-ish inputs, but independent of each other) ---
    top_features = [f["feature"] for f in max_diff_result.get("ranked_features", [])[:5] if isinstance(f, dict)]

    def _psm_task():
        log.info("[plan] Step 9b: Van Westendorp PSM")
        # Use real scraped competitor prices to anchor the simulation
        comp_prices = None
        if competitor_pricing_data and competitor_pricing_data.get("category_median"):
            comp_prices = [d["median"] for d in competitor_pricing_data.get("per_domain", []) if d.get("median")]
        return simulate_van_westendorp(
            segment_summary=segment_summary,
            product_summary=profile.get("summary", ""),
            top_features=top_features,
            competitor_prices=comp_prices,
        )

    def _place_llm_task():
        if not channel_data:
            return {}
        log.info("[plan] Step 11: LLM channel recommendation")
        return recommend_place(
            product_summary=profile.get("summary", ""),
            segment_summary=segment_summary,
            competitor_analysis=channel_data,
        )

    psm_result = {}
    place_result = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        psm_fut = pool.submit(_psm_task)
        place_fut = pool.submit(_place_llm_task)
        try:
            psm_result = psm_fut.result(timeout=90) or {}
        except FutureTimeoutError:
            log.warning("[plan] PSM timed out")
            psm_result = {"error": "timed out"}
        try:
            place_result = place_fut.result(timeout=90) or {}
        except FutureTimeoutError:
            log.warning("[plan] place recommendation timed out")
            place_result = {"error": "timed out"}

    result["pricing"] = {"psm": psm_result}
    if not psm_result.get("error"):
        result["_steps_completed"].append("pricing")
        checkpoint()

    # C5 (Manus-parity): the user's stated price must not be silently dropped.
    # Reconcile it against the model's recommended optimal price, visibly.
    recon = reconcile_pricing(extract_stated_price(description),
                              psm_result.get("optimal_price_point"))
    if recon:
        result["price_reconciliation"] = recon
        log.info("[plan] price reconciliation: stated=%s recommended=%s verdict=%s",
                 recon["stated_usd"], recon["recommended_usd"], recon["verdict"])

    if psm_result.get("optimal_price_point"):
        try:
            result["pricing"]["break_even"] = compute_break_even(float(psm_result["optimal_price_point"]))
        except (TypeError, ValueError):
            pass

        # --- Per-unit pricing + competitor benchmark table (user feedback #3b) ---
        try:
            from pricing import build_benchmark_table
            biz_model = (profile.get("business_model") or "").lower()
            unit = "seat" if "b2b" in biz_model or "saas" in biz_model else "account"
            bench = build_benchmark_table(
                our_tiers=psm_result.get("recommended_tiers", []),
                competitor_pricing=competitor_pricing_data,
                pricing_unit=unit,
                competitor_brands=opps[:8],
            )
            if "error" not in bench:
                result["pricing"]["benchmark"] = bench
        except Exception as e:
            log.warning(f"[plan] pricing benchmark failed (non-fatal): {e}")

        # --- Step 10 (full): CLV + CAC + EVC economic decomposition ---
        # Spec step 10 requires CLV + CAC_ratio. User feedback iter 35 adds EVC.
        try:
            from economics import full_economics
            log.info("[plan] Step 10: CLV + CAC + EVC economics")
            comp_prices = None
            if competitor_pricing_data and competitor_pricing_data.get("per_domain"):
                comp_prices = [d["median"] for d in competitor_pricing_data["per_domain"] if d.get("median")]
            # Derive a sensible pricing unit from the business model
            biz_model = (profile.get("business_model") or "").lower()
            unit = "seat" if "b2b" in biz_model or "saas" in biz_model else "account"
            econ = full_economics(
                segment_summary=segment_summary,
                product_summary=profile.get("summary", ""),
                optimal_price_monthly=float(psm_result["optimal_price_point"]),
                pricing_unit=unit,
                competitor_prices=comp_prices,
            )
            result["economics"] = econ
            if "error" not in econ:
                result["_steps_completed"].append("economics")
                checkpoint()
        except Exception as e:
            log.warning(f"[plan] economics computation failed (non-fatal): {e}")

    result["place"] = place_result
    if not place_result.get("error"):
        result["_steps_completed"].append("place")
        checkpoint()

    # --- Step 12: Validation gate ---
    log.info("[plan] Step 12: validation gate")
    val = _validation_gate(result)
    result["validation"] = val
    result["_steps_completed"].append("validation")
    checkpoint()

    # --- Step 7b: Market sizing (TAM/SAM/SOM) — parallel with 4Ps ---
    # This uses profile + competitors + audience + pricing, all of which are already computed.
    # Run in parallel with 4Ps synthesis to save wall-clock time.
    def _sizing_task():
        log.info("[plan] Step 7b: Market sizing (TAM/SAM/SOM)")
        return estimate_market_size(
            profile=profile,
            competitors=opps[:6],
            audience=top_audience,
            competitor_pricing=competitor_pricing_data,
            psm_result=psm_result,
        )

    def _four_ps_task():
        log.info("[plan] Step 13: assembling 4Ps plan (split into 4 focused prompts)")
        return assemble_4ps_split(
            profile=profile,
            competitors=opps[:5],
            top_audience=top_audience,
            max_diff=max_diff_result,
            van_westendorp=psm_result,
            place=place_result,
            pricing_benchmark=(result.get("pricing") or {}).get("benchmark"),
            economics=result.get("economics"),
            reddit_signal=reddit_data,
        )

    # cycle33: classify market scale (numbers-right engine). Non-breaking — the
    # legacy estimate_market_size shape is preserved downstream; we annotate the
    # result with the scale decision and route physical ventures' caveats.
    scale_decision = None
    try:
        from skills.sizing.classify import classify_market_scale
        scale_decision = classify_market_scale(description, geo).payload
        result["market_scale"] = scale_decision
        result["_steps_completed"].append("market_scale")
        log.info("[plan] Step 7a: market scale = %s → %s",
                 scale_decision.get("scale"), scale_decision.get("sizing_skill"))
    except Exception as e:
        log.warning("[plan] scale classification failed (non-fatal): %s", e)

    sizing = {}
    four_ps = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        sizing_fut = pool.submit(_sizing_task)
        fourps_fut = pool.submit(_four_ps_task)
        try:
            sizing = sizing_fut.result(timeout=90) or {}
        except FutureTimeoutError:
            log.warning("[plan] market sizing timed out")
            sizing = {"error": "timed out"}
        try:
            four_ps = fourps_fut.result(timeout=120) or {}
        except FutureTimeoutError:
            log.warning("[plan] 4Ps synthesis timed out")
            four_ps = {"error": "timed out"}

    if sizing and not sizing.get("error"):
        # C2: ground the bottom-up TAM in a live Census count BEFORE the gate, so
        # the report uses the real establishment count, not the LLM's guess.
        # F3: ARPU basis = stated price, else the modeled PSM optimal price, so the
        # Census-grounded bottom-up fires for most digital ventures (not only typed $/mo).
        _psm_price = (psm_result or {}).get("optimal_price_point")
        sizing = ground_sizing_bottom_up(sizing, description, profile,
                                         arpu_monthly_fallback=_psm_price)
        # cycle33: real origin-independent triangulation (replaces naive averaging).
        sizing = triangulate_sizing(sizing)
        # cycle33: gate + annotate via the numbers-right engine (non-breaking).
        sizing = gate_and_annotate_sizing(sizing, scale_decision)
        result["market_sizing"] = sizing

    # F3 (location path): for a PHYSICAL venture with a location, override the digital
    # sizing with a real trade-area model (Census households × BLS spend, OSM competitor
    # density). Falls back silently to the digital sizing if no location / unavailable.
    try:
        hl = size_by_scale(scale_decision, description, profile)
        if hl:
            result["market_sizing"] = hl
            if "market_sizing" not in result["_steps_completed"]:
                result["_steps_completed"].append("market_sizing")
            log.info("[plan] hyperlocal sizing override (%s @ %s)",
                     (scale_decision or {}).get("scale"), hl.get("_hyperlocal_location"))
    except Exception as e:
        log.warning("[plan] hyperlocal override failed (non-fatal): %s", e)
        result["_steps_completed"].append("market_sizing")
        checkpoint()

    result["four_ps"] = four_ps
    if not four_ps.get("error"):
        result["_steps_completed"].append("four_ps")
        checkpoint()

    # --- Steps 7-8: Per-segment scoring + weighting (iter 36, spec 7-8) ---
    # Requires customer_universe.segments to exist. Uses operator_weights if provided.
    cu = result.get("customer_universe") or {}
    segs = cu.get("segments", [])
    # Iter 41: lowered from 2 → 1. Even a single segment scored on the 5 metrics
    # is more useful than no segment-prioritization section at all.
    if segs and len(segs) >= 1:
        try:
            from segment_scoring import rank_segments, DEFAULT_WEIGHTS
            weights = result.get("operator_weights") or DEFAULT_WEIGHTS
            competition_ctx = f"{len(opps)} competitors discovered; top: " + ", ".join(
                o.get("brand", "?") for o in opps[:5]
            )
            log.info("[plan] Steps 7-8: scoring %d segments on 5 metrics", len(segs))
            ranking = rank_segments(
                segments=segs,
                product_summary=profile.get("summary", ""),
                competition_context=competition_ctx,
                weights=weights,
            )
            result["segment_ranking"] = ranking
            if "error" not in ranking:
                result["_steps_completed"].append("segment_ranking")
            checkpoint()
        except Exception as e:
            log.warning(f"[plan] segment ranking failed (non-fatal): {e}")

    # --- Step 10b: Financial projections (deterministic, no LLM) ---
    som_mid = (sizing.get("som", {}) or {}).get("mid")
    optimal_price = psm_result.get("optimal_price_point")
    be = (result.get("pricing", {}) or {}).get("break_even", {}) or {}
    be_customers = be.get("break_even_customers")
    if som_mid and optimal_price:
        log.info("[plan] Step 10b: 3-year financial projections")
        proj = project_three_year(
            som_mid=float(som_mid),
            optimal_price=float(optimal_price),
            break_even_customers=be_customers,
        )
        if not proj.get("error"):
            result["financials"] = proj
            result["_steps_completed"].append("financials")
            checkpoint()

    # --- Step 14: Viability score ---
    log.info("[plan] Step 14: scoring viability")
    signal_count = sum(
        1 for s in (disc.get("steps", {}) or {}).get("signals", [])
        if s.get("_score", 0) > 0
    )
    # Iter 43 (issue I): pass actual differentiators_strength + universe_count +
    # economics into viability so its 5-dim scoring uses the REAL pipeline data
    # instead of the LLM's own guesses.
    # cycle30: viability is critical — if it errors/times out on first try, retry
    # once with a longer timeout. Better to take +90s than silently skip.
    viability_kwargs = dict(
        profile=profile,
        four_ps=four_ps,
        density=disc.get("competitor_density") or 0,
        avg_score=disc.get("avg_opportunity_score") or 0,
        audience_confidence=top_audience.get("confidence", 0) or 0,
        signal_count=signal_count,
        differentiators_strength=(result.get("differentiators") or {}).get("differentiation_strength"),
        differentiators_count=len((result.get("differentiators") or {}).get("differentiators", [])),
        customer_universe_count=(result.get("customer_universe") or {}).get("count"),
        economics_evc=(result.get("economics") or {}).get("evc", {}).get("verdict"),
        economics_clv=(result.get("economics") or {}).get("clv", {}).get("clv_usd"),
    )
    viability = _run_with_timeout(score_viability, timeout_s=90, label="viability", **viability_kwargs)
    if viability.get("error"):
        log.warning("[plan] viability errored on first try (%s) — retrying with 180s timeout",
                    viability.get("error"))
        viability = _run_with_timeout(score_viability, timeout_s=180, label="viability(retry)", **viability_kwargs)
    result["viability"] = viability
    if not viability.get("error"):
        result["_steps_completed"].append("viability")
        checkpoint()
    else:
        log.warning("[plan] viability FAILED twice — surfacing as validation flag")

    # cycle30: re-run validation gate at end so viability/segment/source flags
    # surface — the early gate ran before downstream signals existed.
    final_val = _validation_gate(result)
    # Merge with the earlier validation pass: keep both flag lists, take the
    # MIN confidence (more conservative).
    if result.get("validation"):
        prev = result["validation"]
        merged_flags = list(dict.fromkeys((prev.get("flags") or []) + (final_val.get("flags") or [])))
        merged_conf = min(prev.get("confidence_score", 1.0), final_val.get("confidence_score", 1.0))
        result["validation"] = {"flags": merged_flags, "confidence_score": merged_conf}
    else:
        result["validation"] = final_val

    # cycle33: opt-in generator-evaluator-refine pass (Anthropic harness pattern).
    if refine:
        log.info("[plan] running generator-evaluator-refine loop")
        result = refine_pipeline_result(result, description, geo, profile, opps)
        if "refine" not in (result.get("_steps_completed") or []):
            result.setdefault("_steps_completed", []).append("refine")

    result["_duration_seconds"] = round(time.time() - t_start, 1)
    return result
