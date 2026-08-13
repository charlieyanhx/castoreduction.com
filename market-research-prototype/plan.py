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

from capabilities.scheduler import run_labeled

from taste import decode_taste
from pricing import simulate_max_diff, simulate_van_westendorp, compute_break_even
from place import analyze_competitor_channels, recommend_place
from four_ps import assemble_4ps, assemble_4ps_split, score_viability
from clustering import cluster_competitors, find_whitespace
from competitor_pricing import gather_competitor_prices
from market_sizing import estimate_market_size, validation_sources_for
from financials import project_three_year, Y3_CAPTURE
from personas import synthesize_personas
from logger import get

log = get("plan")


# Wave 3 item 5: the step machinery moved to orchestrator/steps/ so an extracted
# step can use it without importing plan (plan imports the steps — the reverse
# would be a cycle). Re-exported under the old private names: the "+shim" the
# wave calls for, so existing callers and tests keep working untouched.
from orchestrator.steps import skip_step as _skip_step, step_done as _step_done
from orchestrator.steps.competitors import run_discover_step
from orchestrator.steps.firmographics import run_firmographics_step
from orchestrator.steps.profile import run_profile_step


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

    # cycle30 NEW: customer-voice source breadth — flag if too few sources returned data.
    # cycle38 follow-up (#70): the denominator counts only sources actually QUERIED. The dev
    # forums are deliberately skipped for non-tech ventures, and their zeros used to sit in
    # `counts` indistinguishable from real empty results — run9 (a cafe) was flagged "Only 1
    # customer-voice sources returned data" and docked 0.10 confidence for having no Stack
    # Overflow presence. The expectation bar scales with how many sources were queried, so a
    # venture is never penalised for a source the pipeline chose not to ask.
    sources_with_data = 0
    sources_queried = 2          # reddit_signal + hn_signal are always attempted
    if (result.get("reddit_signal") or {}).get("threads_found", 0) > 0:
        sources_with_data += 1
    if (result.get("hn_signal") or {}).get("hits_found", 0) > 0:
        sources_with_data += 1
    _mss = result.get("multi_source_signal") or {}
    ms_counts = _mss.get("counts") or {}
    ms_queried = _mss.get("queried") or {k: True for k in ms_counts}   # legacy artifacts
    for name, v in ms_counts.items():
        if not ms_queried.get(name, True):
            continue             # skipped by design — not evidence of thin signal
        sources_queried += 1
        if (v or 0) > 0:
            sources_with_data += 1
    _voice_bar = min(3, max(1, sources_queried - 1))
    if sources_with_data < _voice_bar:
        flags.append(f"Only {sources_with_data} of {sources_queried} queried customer-voice "
                     f"sources returned data — opinion signals are thin")
        confidence -= 0.10

    # cycle30 NEW: TAM 3-method completion check. cycle36: ONLY applies to the national
    # 3-method (top-down/bottom-up/analog) sizing. Hyperlocal trade-area sizing
    # (households × spend) is single-method BY DESIGN — flagging it "0/3 methods" is a
    # false alarm, so skip the check entirely for that path.
    ms_for_flags = result.get("market_sizing") or {}
    tam = ms_for_flags.get("tam") or {}
    is_trade_area = ms_for_flags.get("method") == "trade_area_catchment"
    if not is_trade_area:
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
    # G2 (D04): re-enforce SOM ≤ SAM ≤ TAM *here*, at the END of the sizing chain.
    # estimate_market_size clamps once, but ground_sizing_bottom_up / triangulate_sizing
    # rewrite tam.mid afterwards — if triangulation pulls TAM below SAM, the funnel broke
    # silently (baseline: 3 national-path reports shipped SAM > TAM). The clamp itself
    # discloses via clamp_note + weakest_assumptions.
    try:
        from market_sizing import _enforce_sizing_ordering
        out = _enforce_sizing_ordering(out)
    except Exception as e:
        log.warning("[plan] final ordering enforcement failed (non-fatal): %s", e)
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
                    # Real provenance travels WITH the figure, or the F6 external
                    # cross-check can never see it — validate classifies grounded
                    # strictly by origin now, never by substrings of source prose.
                    "origin": blk.get("data_origin") or "llm",
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
        # Persist the figures we just validated (audit high #5). They used to live and die
        # inside `adapted`, so `market_sizing.figures` never existed on a digital or
        # regional report — and report/verifier.py's layer-2 formula check reads exactly
        # that key, so it silently found nothing to check on 10 of 16 corpus reports: every
        # size_national_digital and size_regional one, i.e. precisely the reports whose
        # arithmetic an LLM wrote rather than Python computed. C7 inside validate_numbers
        # did run on them (30/30 method figures, catching both real contradictions in the
        # corpus); this makes the same figures re-checkable by the second, independent
        # checker instead of leaving it blind.
        #
        # Never CLOBBER: this gate runs on every sizing, and a hyperlocal one has no
        # `method_*` blocks, so `figures` comes back empty for it — while size_hyperlocal
        # has already published its own three (TAM_local/SAM_local/SOM_obtainable). An
        # unconditional assignment would delete exactly the figures the verifier can
        # currently read.
        out["figures"] = figures or out.get("figures") or []
        # C3 (audit remediation): a failed gate makes the sizing UNPUBLISHABLE.
        # The renderer hard-banners this and downstream consumers can refuse it —
        # "validate loud" now actually blocks instead of silently annotating.
        out["publishable"] = bool(v.payload.get("passed"))
        if not v.payload["passed"]:
            log.warning("[plan] SIZING BLOCKED by validation gate (unpublishable): %s", v.error)
    except Exception as e:  # gate machinery failure is non-fatal, but mark unknown
        log.warning("[plan] sizing validation failed (non-fatal): %s", e)
        out["publishable"] = out.get("publishable", True)

    # Item 2: every figure ships WITH an origin key. A missing key is indistinguishable from
    # a fetched number to everything downstream -- the trace, the gates, the reader. Measured:
    # 14 of 15 agency-citing figures across the corpus carried no origin at all. Where the
    # producer recorded nothing, the honest label is "unattributed" -- not "llm", which would
    # assert a provenance we equally cannot prove, and not a silent absence.
    _figs = out.get("figures")
    if isinstance(_figs, list):
        out["figures"] = [
            ({**f, "data_origin": (f.get("data_origin") or f.get("origin") or "unattributed")}
             if isinstance(f, dict) else f)
            for f in _figs
        ]

    if scale_decision:
        out["scale_decision"] = scale_decision
        skill = scale_decision.get("sizing_skill") or ""
        if scale_decision.get("scale") in ("hyperlocal", "regional", "national_physical"):
            # Did the skill the classifier NAMED actually produce these numbers? Its output
            # carries a trade-area footprint; LLM sizing has none. Measured on a real run:
            # the classifier demanded size_hyperlocal, it never ran, and the LLM's
            # $31,050,000 shipped as publishable citing "Census ACS & BLS QCEW" with
            # data_origin=None and zero Census calls. A decision nothing enforces is
            # decoration, and the silence was the whole defect.
            ran = bool(out.get("trade_area_households") is not None or out.get("radius_m")
                       or out.get("catchment_km2") is not None)
            out["scale_skill_ran"] = ran
            if not ran:
                out["publishable"] = False
                out["notes"] = list(out.get("notes") or []) + [
                    f"NOT GROUNDED: this is a {scale_decision.get('scale')} venture, so its "
                    f"sizing must come from {skill or 'the trade-area model'}, which did not "
                    "run — no street address was available (a neighbourhood name geocodes to "
                    "points kilometres apart, so it cannot stand in for one). The figures "
                    "below are model-narrated upper bounds, not a measured trade area. "
                    "Provide a street address for a defensible SOM."]
            else:
                # THREE states, not two. "The skill ran" and "the skill produced numbers" are
                # different facts: on a fresh run size_hyperlocal computed a real 1500m/
                # 7.07km2 catchment and then could not finish, because ACS households need a
                # CENSUS_API_KEY. Claiming "figures are measured from the catchment" beside
                # zero figures and no TAM would be a smaller version of the same overclaim
                # this gate exists to stop.
                _has_numbers = bool((out.get("tam") or {}).get("mid")
                                    or (out.get("figures") or []))
                if _has_numbers:
                    out["notes"] = list(out.get("notes") or []) + [
                        f"Trade-area sizing via {skill or 'size_hyperlocal'}: figures are "
                        "measured from the catchment, not national top-down."]
                else:
                    out["publishable"] = False
                    out["notes"] = list(out.get("notes") or []) + [
                        f"{skill or 'The trade-area model'} ran and measured the catchment "
                        f"({out.get('radius_m')}m radius"
                        + (f", {out.get('catchment_km2')} km2" if out.get("catchment_km2")
                           else "")
                        + "), but could not size it: the household and spend inputs are "
                        "unavailable. No market size is published rather than substituting "
                        "a national estimate for a trade area."]
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


# cycle37: a per-TRANSACTION stated price ("$6 per drink", "$6/cup", "$15 a cut"). The monthly
# _STATED_PRICE_RE misses these, so a transactional venture used to fall back to the PSM monthly
# point ($38) as its "unit price" — a $38 drink. This captures the real per-unit price.
_UNIT_PRICE_RE = re.compile(
    r"\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:/|per|a|an|each)?\s*"
    r"(?:drink|cup|coffee|latte|espresso|beverage|pour[- ]?over|meal|plate|dish|entree|entr[ée]e|"
    r"cover|visit|ticket|session|class|lesson|ride|trip|haircut|cut|treatment|item|slice|pint|"
    r"glass|scoop|cone|order|night|booking|appointment|round|game|head|person|guest)\b",
    re.IGNORECASE)


def extract_unit_price(text: str) -> float | None:
    """Pull a per-transaction stated price ($6 per drink, $15/cut). Returns the value or None."""
    if not text:
        return None
    m = _UNIT_PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


# B2/D17: a hybrid hardware+subscription price ("$199 device plus $5/mo app"). The
# monthly _STATED_PRICE_RE greedily matched the $5/mo phrase and returned it as the
# venture's "unit price" — against real hardware COGS that's a negative margin,
# economics errors, and financials silently fell back to the subscription model
# (churn-annualizing a one-time hardware sale). This looks for a $ figure near a
# device noun that is NOT itself a /mo phrase.
_DEVICE_PRICE_RE = re.compile(
    r"\$\s*(\d[\d,]*\.?\d*)\s*(?:\w+\s+){0,3}?"
    r"(?:device|hardware|unit|kit|sensor|monitor|gadget|appliance)\b",
    re.IGNORECASE)
_MONTHLY_WORD_RE = re.compile(r"/|\bper\b|\bmonth(?:ly)?\b|\bmo\b", re.I)


_NON_RECURRING_KINDS = {"transactional", "ecommerce", "services", "hybrid", "marketplace"}


def _pricing_is_recurring(biz_kind: str) -> bool:
    """C3/D06-extend: which business-model kinds get '/month per <unit>' benchmark
    labels. Real R4 catch (174ae091 R5 CRITICAL): a marketplace's per-booking price
    ("$350/booking") rendered as "$350/mo per account" — the call site derived
    recurring=not is_transactional, and is_transactional is False for marketplace
    too (it isn't in business_model.PER_UNIT_KINDS), so "not per-unit -> must be
    recurring" was wrong for the one other genuinely-non-recurring kind. Only
    'subscription' is a true recurring seat/account fee; ad_supported is free to the
    user (no price to label at all in this path, but never recurring either)."""
    return biz_kind not in _NON_RECURRING_KINDS and biz_kind != "ad_supported"


def extract_device_price(text: str) -> float | None:
    """Pull the one-time hardware/device price from a hybrid venture's description
    ('$199 device plus a $5 per month app' -> 199.0). Returns None when the only
    dollar figure near a device noun is ITSELF a monthly phrase (e.g. '$5 per month
    device fee'), or no device noun is present at all."""
    if not text:
        return None
    for m in _DEVICE_PRICE_RE.finditer(text):
        if _MONTHLY_WORD_RE.search(m.group(0)):
            continue  # the words between $ and the device noun say this IS monthly
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            continue
    return None


def reconcile_wtp_with_price(wtp: dict | None, recommended: float | None,
                             interviews: list | None = None) -> dict | None:
    """B3/D18 + R4 rank 11: flag a recommended price that sits ABOVE the top of the
    simulated willingness-to-pay range — a price no simulated segment said they'd pay.

    The old check compared the recommendation to the WTP MEDIAN inside a 0.1x-10x
    deadband, so a price 3-9x the median, or a price above the band's high, sailed
    through as long as recommended/median stayed under 10x (800c261b, e55db08e,
    4a755faa — 83-156x gaps were caught, but the subtler over-ceiling cases were not).
    Now the comparison is against the CEILING (the band's high, or the single point):
    a price at or below the ceiling is defensible — someone would pay it; a price above
    it is flagged, with how many simulated segments actually named a price that high
    (the honest "0 of N" the report never showed). Returns None when either number is
    missing or the recommendation is within the range."""
    if not wtp or not recommended:
        return None
    center = wtp.get("median") if wtp.get("median") is not None else wtp.get("point")
    ceiling = wtp.get("high") if wtp.get("high") is not None else center
    try:
        recommended = float(recommended)
        center = float(center) if center is not None else None
        ceiling = float(ceiling) if ceiling is not None else center
    except (TypeError, ValueError):
        return None
    if not ceiling or ceiling <= 0 or recommended <= 0:
        return None
    if recommended <= ceiling:
        return None  # within what at least one simulated segment would pay

    # How many simulated segments named a price at or above the recommendation.
    n_named = n_at_or_above = None
    if interviews:
        named = [float(w) for iv in interviews
                 if isinstance((w := iv.get("willingness_to_pay_usd")), (int, float))
                 and not isinstance(w, bool) and w > 0]
        n_named = len(named)
        n_at_or_above = sum(1 for v in named if v >= recommended)

    anchor = center if center is not None else ceiling
    ratio = round(recommended / anchor, 1) if anchor else None
    count_clause = (f" — {n_at_or_above} of {n_named} simulated segments named a price "
                    f"that high" if n_at_or_above is not None else "")
    return {
        "wtp": anchor,
        "wtp_ceiling": ceiling,
        "recommended": recommended,
        "ratio": ratio,
        "n_named": n_named,
        "n_at_or_above": n_at_or_above,
        "note": (
            f"The recommended price (${recommended:,.0f}) is above the top of the "
            f"simulated willingness-to-pay range (${ceiling:,.0f}){count_clause}. These "
            "may reflect different buyer framings; do NOT average the two or silently "
            "pick one — validate willingness-to-pay with real buyer interviews before "
            "pricing."
        ),
    }


def reconcile_pricing(stated: float | None, recommended,
                      unit_label: str = "/mo") -> dict | None:
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
    # R4 rank 16: render prices in the venture's OWN unit, not a hardcoded "/mo" — an
    # $18,500-per-project consultancy read "$18,500/mo", a per-coffee cafe "$6/mo".
    u = unit_label or "/mo"
    delta_pct = round((rec - stated) / stated * 100, 1)
    if abs(delta_pct) <= 15:
        verdict = "aligned"
        note = (f"Your ${stated:,.0f}{u} aligns with the model's recommended "
                f"${rec:,.0f}{u} ({delta_pct:+.0f}%).")
    elif rec < stated:
        verdict = "model_suggests_lower"
        note = (f"You stated ${stated:,.0f}{u}; the model's price simulation suggests "
                f"${rec:,.0f}{u} ({delta_pct:+.0f}%) may capture more of the market. "
                f"Validate before changing.")
    else:
        verdict = "model_suggests_higher"
        note = (f"You stated ${stated:,.0f}{u}; willingness-to-pay analysis suggests "
                f"${rec:,.0f}{u} ({delta_pct:+.0f}%) — you may be under-pricing.")
    return {"stated_usd": stated, "recommended_usd": rec,
            "delta_pct": delta_pct, "verdict": verdict, "note": note}


def price_of_record(unit_price, device_price, stated, opt, unit_noun,
                    is_transactional) -> dict:
    """R4 rank 16: ONE disclosed price of record + its provenance. The price fed to
    economics/financials was a bare fallback chain (unit_price or device_price or stated
    or opt) that could differ from the PSM optimal shown elsewhere, unreconciled. This
    records which source won, the PSM optimal, and whether they materially (>15%) differ
    so a second price of record can never hide."""
    if is_transactional:
        chain = [("stated per-unit price", unit_price),
                 ("one-time device/hardware price", device_price),
                 ("stated price", stated), ("PSM optimal", opt)]
        value, basis = next(((v, b) for b, v in chain if v), (opt, "PSM optimal"))
    else:
        value, basis = opt, "PSM optimal"
    try:
        value = float(value) if value is not None else None
    except (TypeError, ValueError):
        value = None
    try:
        psm = float(opt) if opt is not None else None
    except (TypeError, ValueError):
        psm = None
    differs = bool(value is not None and psm is not None and psm > 0
                   and abs(value - psm) / psm > 0.15)
    return {"value": value, "unit": unit_noun, "basis": basis,
            "psm_optimal": psm, "differs_from_psm": differs}


# A per-transaction price phrase ("$6 per drink", "$15/cut") makes the natural WTP unit
# the purchase unit, NOT a month — labeling a per-visit cafe's WTP "/mo" produces the
# nonsensical "$5/mo < $6/drink". Recurring signals keep the monthly unit.
_PER_UNIT_RE = re.compile(
    r"(?:per|/|each|a)\s+"
    r"(drink|cup|coffee|latte|espresso|beverage|meal|plate|dish|entree|entr[ée]e|cover|"
    r"visit|ticket|session|class|lesson|ride|trip|haircut|cut|treatment|item|bag|slice|"
    r"pint|glass|scoop|cone|order|box|night|booking|appointment|seat|round|game)\b",
    re.I)
_RECURRING_RE = re.compile(
    r"\b(subscription|subscribe|saas|membership|member|per\s+month|/\s*mo\b|monthly|"
    r"recurring|per\s+seat|per\s+user|retainer)\b", re.I)


def infer_wtp_unit(description: str, profile: dict | None = None) -> str:
    """Pick the willingness-to-pay unit for the consumer research. A stated per-purchase
    price ('$6 per drink') wins → '/drink'; else a recurring signal → '/mo'; else '/mo'
    (the safe default for digital/unspecified). cycle36."""
    text = f"{description or ''} {(profile or {}).get('summary', '')}"
    m = _PER_UNIT_RE.search(text)
    if m:
        return f"/{m.group(1).lower()}"
    if _RECURRING_RE.search(text):
        return "/mo"
    return "/mo"


# The ECONOMICS unit noun (distinct from the WTP unit). A per-unit venture must NEVER be sized
# in "/mo" — that recreates subscription bleed in the spine ($45/mo serum, "84 mos/mo" for a gym).
# Broader phrase set than _PER_UNIT_RE, plus category + kind fallbacks.
_UNIT_NOUN_RE = re.compile(
    r"(?:per|/|each|a|an)\s+"
    r"(drink|cup|coffee|latte|espresso|beverage|meal|plate|dish|entree|cover|bowl|burrito|"
    r"taco|sandwich|salad|pizza|slice|scoop|cone|pint|glass|"
    r"visit|ticket|session|class|lesson|drop-?in|ride|trip|haircut|cut|treatment|appointment|"
    r"booking|night|room|"
    r"item|order|box|bag|bottle|jar|unit|device|kit|pair|board|"
    r"project|engagement|sprint|"
    r"meter|sq\s?ft|square\s?foot|hour|day)\b",
    re.I)
_UNIT_DEFAULT_BY_KIND = {"services": "project", "ecommerce": "order",
                         "hybrid": "unit", "transactional": "visit"}
_UNIT_CATEGORY_HINTS = (
    ("coffee", "drink"), ("cafe", "drink"), ("café", "drink"), ("tea", "drink"),
    ("restaurant", "cover"), ("eatery", "cover"), ("diner", "cover"), ("bistro", "cover"),
    ("salad", "bowl"), ("bakery", "item"), ("food truck", "plate"), ("salon", "cut"),
    ("barber", "cut"), ("spa", "treatment"), ("gym", "class"), ("fitness", "class"),
    ("yoga", "class"), ("serum", "bottle"), ("skincare", "bottle"), ("cosmetic", "unit"),
    ("apparel", "item"), ("device", "unit"), ("hardware", "unit"), ("gadget", "unit"),
    ("agency", "project"), ("consult", "engagement"), ("design studio", "project"),
)


_TECH_KW = (
    "saas", "software", "developer", "dev tool", "devtool", " api", "sdk", "devops",
    "cloud", "machine learning", "data platform", "analytics platform", "cybersecurity",
    "infosec", "b2b saas", "open source", "programming", "engineering tool", "ai platform",
    "ml platform", "data pipeline", "observability", "infrastructure software",
)


def _is_tech_venture(profile: dict | None) -> bool:
    """True if dev/tech forums (Stack Exchange / DEV.to / Lobsters) are a relevant customer-voice
    source — i.e. a software/dev/SaaS venture. Deterministic keyword check; a cafe/salon/clinic
    returns False so it isn't searched against (and judged by) tech forums."""
    p = profile or {}
    blob = f"{p.get('business_model','')} {p.get('category','')} {p.get('summary','')}".lower()
    return any(k in blob for k in _TECH_KW)


def unit_for_model(biz_kind: str, description: str, profile: dict | None = None) -> str:
    """The economics/pricing unit noun for a venture — NEVER '/mo' for a per-unit model.

    subscription→seat/account, marketplace→booking, ad_supported→user. For per-unit kinds
    (transactional/ecommerce/services/hybrid): an explicit per-unit phrase wins, else a
    category hint, else a kind default. Deterministic; the root fix for '/mo' bleed in the
    per-unit spine (cycle38)."""
    from business_model import is_per_unit
    prof = profile or {}
    blob = f"{description or ''} {prof.get('summary','')} {prof.get('business_model','')} {prof.get('category','')}".lower()
    if biz_kind == "subscription":
        return "seat" if ("b2b" in blob or "saas" in blob or "per seat" in blob) else "account"
    if biz_kind == "marketplace":
        return "booking"
    if biz_kind == "ad_supported":
        return "user"
    if not is_per_unit(biz_kind):
        return "unit"
    m = _UNIT_NOUN_RE.search(blob)
    if m:
        return m.group(1).lower().replace("dropin", "drop-in")
    for kw, noun in _UNIT_CATEGORY_HINTS:
        if kw in blob:
            return noun
    return _UNIT_DEFAULT_BY_KIND.get(biz_kind, "unit")


def wtp_unit_for(description: str, profile: dict | None = None,
                 market_scale: dict | None = None) -> str:
    """The willingness-to-pay display unit, derived from the BUSINESS MODEL — never a bare
    '/mo' default. Subscription willingness is genuinely monthly; every per-unit model prices
    willingness in its transaction unit (D1/G1: the last '/mo' leak — bottle/bowl/drop-in
    economics rendered 'WTP …/mo', detector D05). Deterministic: classifier + unit resolver."""
    from business_model import classify_business_model
    # Let the classifier see the venture description too — at step 6c the profile can be
    # thin (no business_model field yet) while the description carries the pricing signal
    # ("$13 per bowl").
    prof = dict(profile or {})
    prof["summary"] = f"{prof.get('summary') or ''} {description or ''}".strip()
    kind = classify_business_model(prof, market_scale)
    if kind in ("subscription", "ad_supported"):
        return "/mo"  # recurring (or upsell) willingness is per month
    return "/" + unit_for_model(kind, description, profile)


def _enrich_economics_at_som(econ: dict, som_mid, som_high=None, category: str = "",
                             business_model: str = "", market_scale: str = "",
                             som_low=None) -> dict:
    """cycle37 + G3 (D08): once SOM is known, recompute transactional unit economics with
    the at-SOM-volume profitability — sizing runs after economics, so this can't happen at
    economics time. Pure recompute, no LLM.

    The claim is computed at the AGGRESSIVE scenario ceiling (Y3_CAPTURE, 60% of SOM),
    never at 100% capture: 2/16 baseline reports said "profitable at SOM" while every
    scenario row — including aggressive — lost money (D08 contradiction). Returns econ
    unchanged when not applicable (wrong model, no SOM, already enriched, or bad inputs)."""
    from business_model import is_per_unit as _ipu
    if not _ipu(econ.get("model")) or not som_mid or econ.get("at_som_volume"):
        return econ
    from business_model import retail_unit_economics
    from financials import _y3_ceilings
    _base_ceiling = _y3_ceilings(float(som_mid), som_low, som_high)[0]["base"][0]
    try:
        return retail_unit_economics(
            price_per_unit=econ["price_per_unit"],
            variable_cost_per_unit=econ["variable_cost_per_unit"],
            monthly_fixed_cost=econ["monthly_fixed_cost"],
            unit=econ.get("unit", "unit"),
            # The claim is pinned to the BASE scenario row, read from the SAME
            # function financials uses to build that row (_y3_ceilings) rather than
            # re-derived here. Two Python paths computing one quantity is how they
            # drift, and they did: W4-1 computed this at som.high to be bit-identical
            # with the AGGRESSIVE row. Agreeing was right; agreeing on the OPTIMISTIC
            # row was not. The R4 panel found it on 12/16 ventures — Unit Economics
            # read "profitable at the obtainable SOM volume" off a volume the table
            # called "130% of SOM, aggressive", overstating profit 44%-2.2x, and two
            # ventures claimed profitable when the base case loses money.
            #
            # Reading the shared ceiling also keeps the no-band case coherent: without
            # a usable SOM band financials falls back to the 20% ladder, and a flat
            # som.mid here would contradict it by 5x.
            # Decomposed, not multiplied twice: retail_unit_economics computes
            # obtainable = annual_revenue_usd x som_capture_frac, so the BASE ceiling
            # is expressed as the FRACTION of som.mid, and som_capture_pct then
            # reports the true share of SOM (100% with a band, 20% on the ladder).
            annual_revenue_usd=float(som_mid),
            som_capture_frac=_base_ceiling / float(som_mid),
            cost_source=econ.get("cost_source", ""),
            category=category,
            business_model=business_model,
            kind=econ.get("model", "transactional"),
            market_scale=market_scale,
        )
    except Exception as e:
        log.warning("[plan] at-SOM economics enrich failed (non-fatal): %s", e)
        return econ


def build_consumer_research(description: str, geo: str, profile: dict,
                            opps: list[dict],
                            market_scale: dict | None = None) -> dict | None:
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
        # G1 (D05): unit from the business model — a serum prices WTP per bottle, a salad
        # chain per bowl; only true subscriptions are monthly. infer_wtp_unit's '/mo'
        # default was the last unit-bleed leak.
        wtp_unit = wtp_unit_for(description, profile, market_scale)
        log.info("[plan] Step 6c: consumer research (multi-perspective), WTP unit=%s", wtp_unit)
        cr = consumer_research_skill(description=description, geo=geo, context=context,
                                     wtp_unit=wtp_unit)
        if cr.payload and not cr.skeleton:
            return cr.payload
    except Exception as e:  # never sink the run on a research-enrichment failure
        log.warning("[plan] consumer research failed (non-fatal): %s", e)
    return None


def scrub_failed_psm_citations(four_ps: dict, pricing: dict | None) -> dict:
    """If the Van Westendorp PSM simulation FAILED, relabel any 4Ps citation that credits
    it — so the report never attributes prices to a method that produced no output (audit
    cycle36: false provenance). Citation ids are kept intact (¹ references still resolve);
    only the source text changes. Non-mutating; returns four_ps unchanged when PSM is fine."""
    psm = (pricing or {}).get("psm") or {}
    if not (bool(psm.get("error")) or not psm) or not isinstance(four_ps, dict):
        return four_ps
    honest = "LLM estimate (PSM simulation failed this run — unvalidated)"
    price = four_ps.get("price")
    if not (isinstance(price, dict) and isinstance(price.get("citations"), list)):
        return four_ps
    new_price = dict(price)
    new_price["citations"] = [
        ({**c, "source": honest}
         if isinstance(c, dict)
         and re.search(r"psm|van\s*westendorp", str(c.get("source") or ""), re.I)
         else c)
        for c in price["citations"]
    ]
    return {**four_ps, "price": new_price}


def ground_sizing_bottom_up(sizing: dict, description: str, profile: dict,
                            arpu_monthly_fallback: float | None = None,
                            biz_kind: str | None = None,
                            result: dict | None = None) -> dict:
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
                # PUBLISH the evidence, do not just consume it. The ledger records
                # scrape_market_price as producing `price_intel` and the report showed no
                # such key (D54), because plan.py took the median and dropped the rest --
                # the source hosts, the sample size, the label. An ARPU whose grounding is
                # invisible is barely better than an ungrounded one, so this is a publish
                # rather than a "dropped output" note.
                if result is not None:
                    result["price_intel"] = dict(spe.payload)
                    _step_done(result, "price_intel")
                log.info("[plan] ARPU grounded from scrape: $%s/mo (%s)",
                         arpu_monthly, arpu_sourced)
            elif result is not None:
                record_dropped_output(
                    result, "price_intel",
                    f"scrape_market_price found no usable median: "
                    f"{spe.error or 'no recurring price on any surviving page'}")
        except Exception as e:
            log.warning("[plan] price scrape failed (non-fatal): %s", e)
    if not arpu_monthly:
        # The PSM optimal price carries the VENTURE's unit, not months. Annualizing a
        # per-unit price (x12) fabricates a subscription that does not exist — and the
        # result is then tagged data_origin="census", so a made-up figure inherits real
        # provenance. Corpus shape 800c261b prices superconducting tape at $125,000 per
        # UNIT; x12 would publish a $186M "Census-grounded" bottom-up TAM.
        #
        # The other two bases are monthly by construction: extract_stated_price reads $/mo,
        # and scrape_market_price keeps only recurring $5-$5,000/month values. So the guard
        # belongs here, on the modeled fallback alone. Unknown kind counts as per-unit —
        # the safe reading of a modeled price is "unit unspecified".
        from business_model import is_per_unit
        if arpu_monthly_fallback and (not biz_kind or is_per_unit(biz_kind)):
            out = dict(sizing)
            out["notes"] = list(out.get("notes") or []) + [
                f"Bottom-up TAM not Census-grounded: the only price available is the "
                f"modeled optimal (${arpu_monthly_fallback:,.0f}), which for a "
                f"{biz_kind or 'unclassified'} model is per-unit, not monthly — "
                f"annualizing it would invent recurring revenue this venture does not "
                f"have. Provide a monthly price to ground this method."]
            return out
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
        # The COUNT is fetched; the ARPU multiplying it may not be. A Census count times a
        # modelled price is not a fetched figure, and `data_origin` is what triangulation
        # reads to decide which estimates are INDEPENDENT — stamping 'census' on an
        # LLM-priced product manufactures a second origin out of the same model draw the
        # other methods came from. `arpu_origin` was already computed here and read by
        # nothing at all.
        "data_origin": "census" if arpu_origin in ("stated", "scrape") else "llm",
        "count_origin": "census",   # the establishment count really was fetched
        "arpu_origin": arpu_origin,
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
        f"${arpu_monthly * 12:,.0f}/yr from {arpu_sourced})."
        + ("" if arpu_origin in ("stated", "scrape") else
           " The establishment count is a live Census figure but the price is modeled, so "
           "this method is not independent of the LLM estimates.")]
    log.info("[plan] C2: grounded bottom-up TAM = %s (%s establishments)",
             gb.payload["tam_usd"], gb.payload["establishments"])
    return out


_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z0-9][\w.'-]*(?:\s+[\w.'-]+){0,4}\s+"
    r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Plaza)\b",
    re.I)
_PLACE_RE = re.compile(
    # "in <Neighborhood>[, <City>][, <State>]" — capture the full comma-chain of
    # Capitalized localities so an ambiguous neighborhood keeps its city qualifier
    # (e.g. "Highland Park, Los Angeles" → not Highland Park, Illinois). A lowercase
    # word after a comma (", casual dinner") ends the chain.
    # Measured on a real run: "opening in the Mission District of San Francisco" extracted
    # NOTHING, because the lowercase article ended the match before it began and the city was
    # chained with "of" rather than a comma. The whole hyperlocal path hung on that miss.
    r"\b(?:in|at|near|around|located in)\s+(?:the\s+)?"
    r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}"
    r"(?:(?:,|\s+of)\s*[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,2}){0,2})")


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


# Map common physical categories to a REAL OSM (key, value) tag for competitor lookup.
# Deterministic config (OSM taxonomy), NOT output-hardcoding. Keys are matched as
# substrings of the venture category; the LONGEST matching key wins, so "barbershop"
# beats "bar". Unmatched categories resolve to None (caller skips geo competitors
# rather than fabricating a wrong-category set).
# CRITICAL: OSM does NOT put everything under amenity=. Gyms are leisure=fitness_centre;
# hairdressers/bakeries/bookstores are shop=*. A value-only map (assuming amenity=)
# silently returned 0 competitors for gyms and salons (audit M1 gap).
_OSM_TAG_BY_CATEGORY = {
    "restaurant": ("amenity", "restaurant"), "eatery": ("amenity", "restaurant"),
    "diner": ("amenity", "restaurant"), "bistro": ("amenity", "restaurant"),
    "food truck": ("amenity", "fast_food"), "food cart": ("amenity", "fast_food"),
    "fast food": ("amenity", "fast_food"), "fast-casual": ("amenity", "fast_food"),
    "fast casual": ("amenity", "fast_food"), "salad": ("amenity", "fast_food"),
    "food": ("amenity", "restaurant"),
    "cafe": ("amenity", "cafe"), "café": ("amenity", "cafe"), "coffee": ("amenity", "cafe"),
    "tea house": ("amenity", "cafe"), "teahouse": ("amenity", "cafe"),
    "bakery": ("shop", "bakery"), "patisserie": ("shop", "bakery"),
    "pub": ("amenity", "pub"), "brewery": ("amenity", "bar"), "bar": ("amenity", "bar"),
    "wine bar": ("amenity", "bar"), "cocktail": ("amenity", "bar"),
    "nightclub": ("amenity", "nightclub"), "ice cream": ("amenity", "ice_cream"),
    "gym": ("leisure", "fitness_centre"), "fitness": ("leisure", "fitness_centre"),
    "crossfit": ("leisure", "fitness_centre"), "yoga": ("leisure", "fitness_centre"),
    "pilates": ("leisure", "fitness_centre"),
    "barbershop": ("shop", "hairdresser"), "barber": ("shop", "hairdresser"),
    "hair salon": ("shop", "hairdresser"), "salon": ("shop", "hairdresser"),
    "nail": ("shop", "beauty"), "beauty": ("shop", "beauty"), "spa": ("leisure", "spa"),
    "clinic": ("amenity", "clinic"), "dental": ("amenity", "dentist"),
    "dentist": ("amenity", "dentist"), "veterinary": ("amenity", "veterinary"),
    "pharmacy": ("amenity", "pharmacy"), "bookstore": ("shop", "books"),
    "bookshop": ("shop", "books"), "florist": ("shop", "florist"),
    "library": ("amenity", "library"), "cinema": ("amenity", "cinema"),
}


# Realistic catchment radius (metres) by OSM value — a walk-in cafe draws from ~1.5km, a
# destination restaurant ~3km, a drive-to gym ~5km. A flat 3km over-counted households (and
# competitors) for grab-and-go venues, inflating the trade-area TAM. cycle38.
_RADIUS_BY_OSM_VALUE = {
    "cafe": 1500, "fast_food": 1500, "bakery": 1500, "ice_cream": 1500,
    "bar": 2000, "pub": 2000, "hairdresser": 2500, "beauty": 2500, "spa": 3000,
    "restaurant": 3000, "nightclub": 3000, "pharmacy": 3000,
    "clinic": 4500, "dentist": 4500, "fitness_centre": 5000, "cinema": 6000, "library": 4000,
}


def _radius_for_osm_value(osm_value: str, default: int = 3000) -> int:
    """Deterministic catchment radius (m) for an OSM venue type — walk-in vs destination."""
    return _RADIUS_BY_OSM_VALUE.get((osm_value or "").lower(), default)


def _resolve_osm_tag(category: str) -> tuple[str, str] | None:
    """Deterministic category → (osm_key, osm_value) using real OSM taxonomy. Longest
    substring match wins (so 'barbershop' → shop/hairdresser, not 'bar' → amenity/bar).
    Returns None when no confident match, so callers SKIP geo-competitor fetch instead of
    guessing a wrong category."""
    catl = (category or "").lower()
    best, best_len = None, 0
    for k, v in _OSM_TAG_BY_CATEGORY.items():
        if k in catl and len(k) > best_len:
            best, best_len = v, len(k)
    return best


def _resolve_osm_amenity(category: str) -> str | None:
    """Back-compat shim: the OSM VALUE only (e.g. 'cafe', 'hairdresser'). Prefer
    _resolve_osm_tag, which also returns the correct OSM key (amenity/shop/leisure)."""
    t = _resolve_osm_tag(category)
    return t[1] if t else None


def record_dropped_output(result: dict, key: str, reason: str) -> None:
    """Record that a step produced something the report will not carry, and why.

    Measured on run2: the ledger recorded `clustering` as produced by cluster_competitors
    (clustering.py:142, ok=true) and the section appeared nowhere -- because the caller does
    `if not clustering.get("error")` and, on error, simply moves on. Same for
    consumer_research and price_intel. Three sections' worth of work, paid for and discarded
    without a trace, which is indistinguishable from a section that was never meant to exist.

    Deliberately does NOT create result[key]: a placeholder would be a fabricated section.
    The reason lives in `_dropped_outputs` so both the reader and gate D54 can see it."""
    if not key or not reason:
        return
    drops = result.setdefault("_dropped_outputs", {})
    drops[str(key)] = str(reason)[:400]


def attach_consumer_research(result: dict, description: str, geo: str, profile: dict,
                            opps: list, checkpoint=None) -> None:
    """Attach the STORM-style consumer research, or record why it is absent.

    Extracted from run_plan so it can be tested, and so the empty case stops being silent.
    D54 on the latest live run: `consumer_research` was recorded by the ledger as produced
    (skills/perspective.py:144) and appeared nowhere in the report, because the whole step was
    `if cr_payload:` with no else. A step that evaporates is indistinguishable from a step
    that was never meant to run.

    Deliberately does not fabricate a placeholder section -- an empty consumer_research block
    would read as "we asked and found nothing", which is a finding, not a gap."""
    cr_payload = build_consumer_research(description, geo, profile, opps,
                                         market_scale=result.get("market_scale"))
    if cr_payload:
        result["consumer_research"] = cr_payload
        _step_done(result, "consumer_research")
        if checkpoint:
            checkpoint()
        return
    record_dropped_output(
        result, "consumer_research",
        "build_consumer_research returned nothing — the multi-perspective simulation "
        "produced no usable synthesis for this venture")


def _competitor_count_note(fair_share_n, roster_n) -> str | None:
    """The sentence that reconciles the report's two competitor counts — or None if only one
    count exists (nothing to reconcile).

    MEASURED on run9: the SOM formula divided by (102+1) while the narrative said "30
    competitors" twelve times and the exec summary put 30 in a headline bullet. 102 is the raw
    OpenStreetMap venue count inside the catchment (the honest fair-share denominator); 30 is
    the named roster (osm_named_competitors limit=30) the sections profile. Neither is wrong —
    but unreconciled, the reader cannot tell which number the analysis used, and the silent
    choice of 102 over 30 cuts the obtainable-share figure 3.4x. One sentence, published next
    to the sizing notes, ends the ambiguity."""
    if not isinstance(fair_share_n, (int, float)) or isinstance(fair_share_n, bool):
        return None
    if not roster_n or fair_share_n <= roster_n:
        return None
    return (f"Competitor counts: {fair_share_n:,.0f} venues of this type inside the catchment "
            f"per OpenStreetMap — the fair-share SOM divides by this full count. The "
            f"competitive-landscape section profiles the nearest {roster_n:,.0f} of them by "
            f"name; narrative references to '{roster_n:,.0f} competitors' mean that profiled "
            f"subset.")


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
    # Disambiguate a bare neighborhood with the venture's geography so geocoding doesn't
    # pick the wrong city (e.g. "Highland Park" → Illinois instead of LA).
    geog = str((profile or {}).get("geography") or "").strip()
    if "," not in location and geog and geog.lower() not in ("us", "u.s.", "usa", "united states", "global", ""):
        location = f"{location}, {geog}"
    cat = (profile or {}).get("category") or ""
    # Use the correct OSM (key, value). Density count only — a coarse fallback here affects a
    # saturation note, not the competitor NAMES (those come from the strict geo_competitor_opps
    # helper, which skips on no-match rather than guessing a wrong category).
    _tag = _resolve_osm_tag(cat) or ("amenity", "restaurant")
    osm_key, osm = _tag
    radius_m = _radius_for_osm_value(osm)  # cycle38: walk-in cafe ~1.5km, not a flat 3km
    try:
        from skills.sizing.hyperlocal import size_hyperlocal
        ev = size_hyperlocal(address=location, category=cat or "food_away_from_home",
                             osm_value=osm, osm_key=osm_key, radius_m=radius_m)
    except Exception as e:
        log.warning("[plan] hyperlocal sizing failed (non-fatal): %s", e)
        return None
    if ev.skeleton or not ev.payload:
        return None
    p = ev.payload

    # Map the hyperlocal figures (which carry the real `formula`) so the report's
    # TAM/SAM/SOM "math" lines render instead of going blank — the template reads
    # block.calculation, but the hyperlocal payload keeps formulas in figures[].
    _figs = {f.get("label"): f for f in (p.get("figures") or []) if isinstance(f, dict)}

    def _block(v, full_label, fig_keys):
        if not (isinstance(v, (int, float)) and not isinstance(v, bool)):
            return {}
        formula = ""
        for k in fig_keys:
            if _figs.get(k, {}).get("formula"):
                formula = _figs[k]["formula"]
                break
        # cycle36 (audit): the low/high is a fixed ±30% MODELING band around the mid, not a
        # measured confidence interval. band_note is surfaced so the reader is not misled into
        # reading the range as researched uncertainty.
        return {"mid": v, "low": round(v * 0.7), "high": round(v * 1.3),
                "label": full_label, "calculation": formula,
                "band_pct": 30, "band_note": "Range is a ±30% modeling band around the estimate, not a measured confidence interval.",
                "unit": "annual revenue · trade area"}

    # Named geographic competitors — a local venture's rivals are the nearby venues
    # (OSM), not blocked web-search brands. Fetch the actual names so the report shows
    # real competitors instead of "0 found".
    geo_competitors: list[dict] = []
    try:
        from tools import get_tool
        g = get_tool("geocode_address").fn(location)
        if g.payload and g.payload.get("lat") is not None:
            ne = get_tool("osm_named_competitors").fn(
                lat=g.payload["lat"], lng=g.payload["lng"],
                osm_key=osm_key, osm_value=osm, limit=30)
            if not ne.skeleton and ne.payload:
                geo_competitors = ne.payload
    except Exception as e:
        log.warning("[plan] named geo-competitors failed (non-fatal): %s", e)

    val = p.get("validation") or {}
    notes = p.get("notes") or []
    return {
        "tam": _block(p.get("tam_usd"), "Total trade-area market", ["TAM_local"]),
        "sam": _block(p.get("sam_usd"), "Serviceable (reachable demand)", ["SAM_local"]),
        # "steady state", not "Year 1-3": the SOM is now the UNRAMPED ceiling the scenario
        # table climbs toward (the ramp was being applied twice — measured on run12, base
        # Year 1 landed at 36% of fair share and below this band's own floor while the label
        # claimed the band covered years 1-3).
        "som": _block(p.get("som_usd"), "Obtainable at steady state (one location)",
                      ["SOM_obtainable", "SOM_demand"]),
        "method": p.get("method"), "figures": p.get("figures"),
        "households": p.get("households"),
        # Carry the TRADE-AREA SCALE through to the stored report. size_hyperlocal publishes
        # these; this mapping dropped them, so D49 — the gate guarding the measured 372x
        # county-as-trade-area error — was N/A on all 16 stored reports while its synthetic
        # unit tests passed. A gate that cannot see real output is not a safeguard.
        # test_gate_reachability now enforces this for every gate.
        "scale": (scale_decision or {}).get("scale") or "hyperlocal",
        "trade_area_households": p.get("trade_area_households"),
        "radius_m": p.get("radius_m"),
        "catchment_km2": p.get("catchment_km2"),
        "households_sourced": p.get("households_sourced"),
        "density_geography": p.get("density_geography"),
        # And the SPEND half of the same TAM, for the same reason as the comment above — this
        # allowlist is where produced numbers go to die. size_hyperlocal publishes whether the
        # per-household spend was grounded in local income; without these three keys D56 fails
        # on every fresh report while its unit tests pass, which is precisely the D49 story
        # repeating one function later.
        "spend_per_hh_usd": p.get("spend_per_hh_usd"),
        "spend_per_hh_source": p.get("spend_per_hh_source"),
        "spend_income_adjustment": p.get("spend_income_adjustment"),
        "competitors": p.get("competitors") or len(geo_competitors),
        "geo_competitors": geo_competitors,
        # One sentence reconciling the OSM fair-share denominator with the profiled roster —
        # run9 divided SOM by (102+1) while the prose said "30 competitors" twelve times.
        "notes": notes + ([n] if (n := _competitor_count_note(
            p.get("competitors"), len(geo_competitors))) else []),
        "validation": val,
        "publishable": val.get("passed", True),
        # Surface the engine's honest confidence + caveats in the template's existing
        # "data quality" and "weakest assumptions" slots (otherwise they read "unknown").
        "data_quality": p.get("confidence") or "low",
        "weakest_assumptions": notes[:3],
        # G5-shallow / D11: geography-aware — a Lisbon cafe must not be told to
        # validate Portuguese household data against US-only sources.
        "sources_to_validate": validation_sources_for(location),
        "scale_decision": scale_decision,
        "_hyperlocal_location": location, "_osm_value": osm,
    }


def geo_competitor_opps(description: str, profile: dict, market_scale: dict | None,
                        limit: int = 30) -> list[dict]:
    """M1 (audit): the REAL competitors of a physical-local venture are the nearby venues
    (OpenStreetMap), not LLM-guessed national DTC brands. Returns them in the canonical
    `opps` shape (brand/name/rank/geo_sourced) so they can feed clustering, differentiators,
    personas, and pricing — or [] when this isn't a physical-local venture, has no location,
    or the category doesn't map to a known OSM amenity (fail safe: never fabricate a
    wrong-category set). Deterministic given (location, category); pure OSM/geocode, no LLM."""
    ms = market_scale or {}
    is_physical = bool((ms.get("signals") or {}).get("is_physical")) or ms.get("scale") in ("hyperlocal", "regional")
    if not is_physical:
        return []
    location = extract_location(description)
    if not location:
        return []
    geog = str((profile or {}).get("geography") or "").strip()
    if "," not in location and geog and geog.lower() not in ("us", "u.s.", "usa", "united states", "global", ""):
        location = f"{location}, {geog}"
    tag = _resolve_osm_tag((profile or {}).get("category") or "")
    if not tag:
        return []  # unknown category → skip, don't guess wrong-category competitors
    osm_key, osm_value = tag
    try:
        from tools import get_tool
        g = get_tool("geocode_address").fn(location)
        if not (g.payload and g.payload.get("lat") is not None):
            return []
        ne = get_tool("osm_named_competitors").fn(
            lat=g.payload["lat"], lng=g.payload["lng"],
            osm_key=osm_key, osm_value=osm_value, limit=limit)
        if ne.skeleton or not ne.payload:
            return []
        # Promote to the opps shape. No domain/signals (these are physical venues, not web
        # brands) — which is correct: downstream pricing then SKIPS web scraping instead of
        # mislabeling a competitor's bagged-goods/subscription price as a per-unit price.
        return [{**c, "rank": i + 1, "geo_sourced": True}
                for i, c in enumerate(ne.payload) if c.get("brand")]
    except Exception as e:
        log.warning("[plan] geo_competitor_opps failed (non-fatal): %s", e)
        return []


def _promote_geo_competitors(result: dict, description: str, profile: dict, geo: str) -> list:
    """M1 fix (audit): for a PHYSICAL-LOCAL venture, the real competitors are the nearby
    venues (OSM), not LLM-guessed national DTC brands. Classify scale early (cheap;
    reused by the later sizing step) and, if physical-local, PROMOTE the geo
    competitors to the canonical `opps` set NOW — so clustering, differentiators,
    personas, and pricing all analyze the actual local rivals.

    B1/D16 close-out fix: the promotion used to replace ranked_opportunities but leave
    competitor_density at its stale pre-promotion web-discovery value — a hyperlocal
    cafe promoted to 30 real nearby venues still reported density=12 (the LLM-guessed
    DTC brand count), caught live by D16 on the wave2.75 regen. Density now stays in
    sync with whichever set is canonical. Mutates result["discover"] in place;
    returns the current opps list (geo-promoted, or the original LLM set)."""
    disc = result.get("discover") or {}
    opps = (disc.get("synthesis", {}) or {}).get("ranked_opportunities", [])
    try:
        if result.get("market_scale") is None:
            from skills.sizing.classify import classify_market_scale
            result["market_scale"] = classify_market_scale(description, geo).payload
            if "market_scale" not in result["_steps_completed"]:
                _step_done(result, "market_scale")
        geo_opps = geo_competitor_opps(description, profile, result.get("market_scale"))
        if len(geo_opps) >= 3:
            opps = geo_opps
            disc.setdefault("synthesis", {})["ranked_opportunities"] = geo_opps
            disc["geo_sourced"] = True
            disc["category"] = (profile or {}).get("category", "")
            # One owner for all three roster-derived numbers. Setting only the count here
            # left active_signal_density/avg_opportunity_score describing the web set that
            # was just discarded — 6/6 geo reports shipped a cited momentum count over a
            # roster with no signal data.
            from discover import _set_canonical_density
            _set_canonical_density(disc)
            result["discover"] = disc
            log.info("[plan] M1: promoted %d geo competitors to the canonical set", len(geo_opps))
    except Exception as e:
        log.warning("[plan] early geo-competitor promotion failed (non-fatal): %s", e)
    return opps


def _surface_late_geo_competitors(result: dict, geo_competitors: list, category: str = "") -> None:
    """F3 (location path): once the hyperlocal trade-area sizing override runs, surface
    its REAL geographic competitors (OSM) when the early discover step found none at
    its expected key — so the report shows actual nearby rivals instead of "0
    competitors found". A SEPARATE code path from _promote_geo_competitors (which
    fires earlier, before web discovery runs at all).

    B1/D16 close-out fix: this used to overwrite ranked_opportunities + geo_sourced
    via a dict spread, but never touched competitor_density — so the report still
    cited the STALE pre-override density (a small LLM-guessed candidate count) against
    the 25-30 real geo competitors now on display. Caught live by D16 on the wave2.75
    regen (5dbf3f54, 94008e7c, 955a4b3b, a618db1a, c48497fa, e8baf9dd).

    Audit high #7. Two key mistakes, both now fixed:

    * it GUARDED on `discover.ranked_opportunities` / `discover.competitors`, keys nothing
      in the pipeline writes, so it could not see a roster the discover step had already
      produced — and would then override `competitor_density` (which IS read) against a
      roster it had not written;
    * it WROTE the roster to `discover.ranked_opportunities`, while every renderer reads
      `discover.synthesis.ranked_opportunities`, so the names never displayed.

    Inert in practice — measured across the 6 corpus reports where it fires, the names it
    wrote were the same set and order as the ones `_promote_geo_competitors` had already put
    at the rendered key — but the count-vs-roster divergence was one missing sibling write
    away.

    Names are surfaced ONLY when the category genuinely maps to an OSM amenity. The roster
    handed to us rides `size_by_scale`'s coarse tag fallback, which resolves an unmapped
    category to ("amenity", "restaurant") — deliberately, for a density estimate. Rendering
    that would print nearby restaurants as a dog groomer's competitor set, and D34 would
    pass them (bare {brand, name} entries carry no off_category or relevance to fail on).
    So this follows the rule `geo_competitor_opps` already documents for itself: fail safe,
    never fabricate a wrong-category set. When the category does not map, nothing changes —
    not even the density, because a count taken from a wrong-category query is itself a
    misleading number. Mutates result["discover"] in place; no-op otherwise."""
    disc = result.get("discover") or {}
    # Guard on the key that actually renders.
    if not geo_competitors or (disc.get("synthesis") or {}).get("ranked_opportunities"):
        return
    if not _resolve_osm_tag(category or ""):
        log.info("[plan] geo-competitors NOT surfaced: category %r has no OSM mapping, so "
                 "the nearby-venue roster would be the wrong category", category)
        return
    roster = [{**c, "rank": i + 1, "geo_sourced": True}
              for i, c in enumerate(geo_competitors) if c.get("brand")]
    if not roster:
        return
    # Mutate in place rather than rebinding: callers hold `disc` aliases (the 4Ps and
    # viability prompts read density off one), and a dict spread left them on the old value.
    disc.setdefault("synthesis", {})["ranked_opportunities"] = roster
    disc["geo_sourced"] = True
    disc["category"] = category
    from discover import _set_canonical_density
    _set_canonical_density(disc)   # B1/D16/D33: one roster owns every number taken from it
    result["discover"] = disc
    if "discover" not in result["_steps_completed"]:
        _step_done(result, "discover")
    log.info("[plan] geo-competitors surfaced: %d nearby venues", len(roster))


def build_provenance_summary(result: dict) -> dict | None:
    """Aggregate the raw run trace (result['_trace']) into a clean 'Data Provenance' view for the
    debug panel: per data-source (which tool, how many calls, real-sourced vs failed, a sample
    detail), and LLM usage (models, cache hits, tokens). Pure read; returns None if no trace."""
    events = (result or {}).get("_trace") or []
    if not events:
        return None
    tools: dict[str, dict] = {}
    llm = {"calls": 0, "cached": 0, "fresh": 0, "models": {}, "out_tok": 0}
    for e in events:
        if e.get("layer") == "tool":
            t = tools.setdefault(e["name"], {
                "tool": e["name"], "category": e.get("category", ""),
                "calls": 0, "sourced": 0, "failed": 0, "skeleton": 0,
                "source": e.get("source", ""), "detail": "", "error": ""})
            t["calls"] += 1
            # Count independently: a graceful skeleton may ALSO carry an explanatory error
            # (e.g. "no API key") — that's a fallback, not an unexpected failure.
            if e.get("error"):
                t["failed"] += 1
                t["error"] = e["error"]
            if e.get("skeleton"):
                t["skeleton"] += 1
            if e.get("sourced"):
                t["sourced"] += 1
                t["source"] = e.get("source", t["source"])
                if e.get("detail"):
                    t["detail"] = e["detail"]
            elif not t["detail"] and e.get("detail"):
                t["detail"] = e["detail"]
        elif e.get("layer") == "llm":
            llm["calls"] += 1
            llm["cached" if e.get("cached") else "fresh"] += 1
            m = e.get("model", "?")
            llm["models"][m] = llm["models"].get(m, 0) + 1
            llm["out_tok"] += int(e.get("out_tok") or 0)
    # Classify each tool's data origin for the panel (real source vs estimate/fallback vs failed).
    rows = []
    for t in sorted(tools.values(), key=lambda x: (-x["sourced"], x["tool"])):
        if t["sourced"]:
            status = "live"            # returned real data from its source
        elif t["skeleton"]:
            status = "skeleton"        # graceful fallback (may carry an explanatory error)
        elif t["failed"]:
            status = "failed"          # unexpected error, no graceful skeleton
        else:
            status = "skeleton"
        rows.append({**t, "status": status})
    return {
        "data_sources": rows,
        "llm": llm,
        "n_events": len(events),
        "n_tool_calls": sum(t["calls"] for t in tools.values()),
    }


try:  # provenance: record that this function produced a report key
    from skills.registry import records_production as _records_production
except Exception:  # pragma: no cover — never let provenance break an import
    def _records_production(_k):
        return lambda f: f


@_records_production("integrity")
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

    # Provenance — two DIFFERENT claims, which the old chip conflated (R4 rank 1):
    #   n_cited    = methods whose LLM-authored `source` string is non-empty. That is
    #                a citation the model WROTE ("US Census Bureau SUSB"), not a fetch.
    #   n_grounded = methods whose data_origin records a real fetch (census/bls/...).
    # The old chip counted the first and rendered it as "Sourced: 3/3" in green — on
    # 10/16 corpus reports whose every triangulation path was origin='llm', 7 of them
    # naming Census/BLS for numbers no fetch produced.
    methods = [tam.get(k) or {} for k in ("method_top_down", "method_bottom_up", "method_analog")]
    methods = [m for m in methods if isinstance(m.get("value_usd"), (int, float))]
    if not methods and (ms.get("method") or "") == "trade_area_catchment":
        # HYPERLOCAL SIZING HAS NO method_* TRIPLE — its sourcing lives in figures[].data_origin.
        # This branch was missing, so the trust box on every hyperlocal report rendered
        # "Sourced: 0/0 — no sources at all · Model-estimated origins: llm" DIRECTLY ABOVE TAM
        # math that was genuinely Census ACS x TIGERweb x BLS (run9, figures stamped
        # census/derived/osm). The one panel titled "how to trust these numbers" told a
        # skeptical buyer to trust nothing — the D53 under-claiming class in the UI layer.
        methods = [f for f in (ms.get("figures") or [])
                   if isinstance(f, dict) and isinstance(f.get("value_usd"), (int, float))]
    n_cited = sum(1 for m in methods if str(m.get("source") or "").strip())
    # 'derived' is arithmetic on a sibling figure (SAM = TAM x 35%): it counts toward the
    # total but NOT as grounded — claiming arithmetic as a fetch would be the OVER-claiming
    # mirror of the bug this branch fixes.
    n_grounded = sum(1 for m in methods
                     if str(m.get("data_origin") or "").strip().lower()
                     not in ("", "llm", "derived", "unattributed", "caller"))

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
        "provenance": {"n_grounded": n_grounded, "n_cited": n_cited,
                       "n_total": len(methods)},
        "data_origins": origins,
        "grounded": any(o in ("census", "bls", "acs") for o in origins),
    }


def assess_run_health(result: dict) -> dict:
    """Detect when a run was crippled by transient LLM/network failures so the report
    can say so LOUDLY instead of presenting $0 TAM / '(generation failed)' as if they
    were real findings. Pure read over the result; no mutation, no LLM. cycle36.

    The honest-numbers bar: a number that failed to compute must read as "temporarily
    unavailable — regenerate", never as a real $0 or blank a reader could mistake for signal.
    """
    failed: list[str] = []

    # 4Ps sections that the LLM failed to generate.
    fp = result.get("four_ps") or {}
    for sect in ("product", "price", "place", "promotion"):
        nar = str((fp.get(sect) or {}).get("narrative") or "")
        if "generation failed" in nar.lower():
            failed.append(f"4Ps · {sect.capitalize()}")
    if fp.get("error"):
        failed.append("4Ps plan")

    # Market sizing produced no headline TAM despite the step running.
    ms = result.get("market_sizing") or {}
    tam_mid = (ms.get("tam") or {}).get("mid")
    notes = " ".join(ms.get("notes") or []).lower()
    sizing_failed = (not isinstance(tam_mid, (int, float)) or tam_mid == 0) and (
        "not computed" in notes or "unavailable" in notes or bool(ms))
    if sizing_failed and ms:
        failed.append("Market sizing · TAM")

    # Consumer research WTP / synthesis empty.
    syn = (result.get("consumer_research") or {}).get("synthesis") or {}
    wtp = syn.get("willingness_to_pay")
    if result.get("consumer_research") and not wtp:
        failed.append("Consumer research · willingness-to-pay")

    # Viability never scored.
    if (result.get("viability") or {}).get("error"):
        failed.append("Viability score")

    # Any raw parse_error leaking into the persisted result is a hard tell.
    try:
        import json as _json
        if "_parse_error" in _json.dumps(result):
            if "LLM responses (parse_error)" not in failed:
                failed.append("LLM responses (parse_error)")
    except Exception:
        pass

    # Severity: TAM or >=2 sections failing = severe; a single failure = partial.
    severe = ("Market sizing · TAM" in failed) or (len(failed) >= 2)
    return {
        "degraded": bool(failed),
        "severity": "severe" if severe else ("partial" if failed else "ok"),
        "failed": failed,
        "n_failed": len(failed),
    }


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
        # biz_kind must travel here too: without it the modeled-price fallback is treated
        # as unit-unknown and declines to ground, so a refine would silently lose the
        # Census-grounded bottom-up that the first pass had.
        ms = ground_sizing_bottom_up(rep.get("market_sizing") or {}, description, profile,
                                     biz_kind=rep.get("business_model_kind"))
        ms = triangulate_sizing(ms)
        ms = gate_and_annotate_sizing(ms, rep.get("market_scale"))
        new = dict(rep); new["market_sizing"] = ms; return new

    def _regen_consumer(rep: dict) -> dict:
        cr = build_consumer_research(description, geo, profile, opps,
                                     market_scale=rep.get("market_scale"))
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
             operator_weights: dict | None = None, refine: bool = False,
             resume_from: dict | None = None, effort: str | None = None) -> dict:
    """
    Run the full market research pipeline on a raw description.

    If `progress` callback is provided, it's called with the partial result
    after every step so the UI can show live progress.
    If `refine=True`, runs the generator-evaluator-refine loop after the pipeline
    (independent judge + deterministic gate → regenerate weak sections). Opt-in
    because it adds LLM cost; default path is unchanged.
    If `resume_from` is given (see persistence.resume.resume(job_id)), the run is
    SEEDED with a killed run's outputs and every step whose evidence is intact is
    skipped rather than recomputed — Wave 3 item 4.
    """
    # Item 6: a run outside the job system had NO ledger record at all -- measured zero
    # transcript files for a direct plan.run_plan call -- so every provenance question about
    # a CLI, benchmark or corpus-regeneration run was unanswerable after the fact. Those are
    # precisely the runs a developer needs to trace. Idempotent: when the job system already
    # attached, this is a no-op and the job's transcript keeps ownership.
    _own_transcript = None
    try:
        from persistence import transcript as _tr
        _own_transcript = _tr.attach(f"direct-{int(time.time())}")
    except Exception:
        pass
    t_start = time.time()
    # W6-3: the effort dial. An unknown level resolves to STANDARD inside
    # effort_config, never down to quick — a typo must not silently thin a report the
    # operator paid extra for. `max_candidates` stays honoured when the caller passed
    # a non-default value explicitly; effort only widens it.
    from capabilities.effort import effort_config, resolve_effort
    _effort = resolve_effort(effort)
    _levers = effort_config(_effort)
    max_candidates = max(max_candidates, _levers["max_candidates"]) if effort else max_candidates

    # Wave 3 item 4: seed from a prior killed run. Steps are skipped individually via
    # _skip_step (intact-evidence only), so a partial/corrupt seed degrades to a normal
    # full run rather than propagating holes.
    result: dict = dict(resume_from or {})
    result.setdefault("_steps_completed", [])
    result["_effort"] = _effort
    if resume_from:
        log.info("[plan] resuming with %d completed step(s): %s",
                 len(result["_steps_completed"]), result["_steps_completed"])

    # Provenance trace (debugging): record every data-source + LLM call this run makes.
    try:
        import provenance as _trace
        _trace.reset()
    except Exception:
        _trace = None

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

    # --- Step 2: Extract profile --- (extracted → orchestrator/steps/profile.py)
    profile = run_profile_step(result, description, geo)
    if profile.get("error"):
        return {"error": f"Profile extraction failed: {profile['error']}", "profile": profile}
    checkpoint()

    # --- Step 3: Competitive intelligence --- (→ orchestrator/steps/competitors.py)
    disc = run_discover_step(result, profile, geo, max_candidates)
    checkpoint()

    opps = _promote_geo_competitors(result, description, profile, geo)

    competitor_domains = [o["domain"] for o in opps if o.get("domain")][:8]

    if not opps:
        # Degraded but not fatal — skip downstream steps that need competitors
        log.warning("[plan] no competitors found — proceeding with profile-only plan")

    # --- Step 3e: Firmographics (B2B only) --- (→ orchestrator/steps/firmographics.py)
    run_firmographics_step(result, profile, disc, opps, checkpoint=checkpoint)

    # --- Step 3c: Cluster competitors + detect whitespace (sklearn) ---
    if len(opps) >= 4:
        log.info("[plan] Step 3c: clustering competitors + PCA whitespace detection")
        # R4 rank 9: cluster the CANONICAL roster (the competitors the report displays),
        # never the larger `signals` pool. Clustering national ventures on `signals`
        # plotted ~20 dots for a roster of 9 — a third competitor count on a third
        # surface. The roster entries carry the same scraped descriptions, so the map
        # now positions exactly the competitors the report lists, and clustering's
        # n_input == len(roster) == competitor_density.
        cluster_input = opps
        clustering = cluster_competitors(cluster_input)
        if clustering.get("error"):
            # Measured: on a real OSM roster this is "need at least 4 competitors with
            # descriptions, got 2" (n_input=30). Silence made the competitor map vanish
            # between two runs of the same venture with nothing to explain it.
            record_dropped_output(result, "clustering",
                                  f"cluster_competitors: {clustering.get('error')}")
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
            _step_done(result, "clustering")
            checkpoint()


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
                target_count=30,
            )
            result["customer_universe"] = universe
            if universe.get("count", 0) > 0:
                _step_done(result, "customer_universe")
            checkpoint()
        except Exception as e:
            log.warning(f"[plan] customer universe failed (non-fatal): {e}")

    # --- PARALLEL PHASE: Steps that can run concurrently after discover ---
    # Decode taste for TOP-3 brands (not just top-1) to enable persona synthesis.
    # Plus place + pricing scrapes — all independent.
    top_3_comps = [o for o in opps if o.get("domain")][:3]
    # A roster with no domains silently skips FOUR sections -- audiences, personas, channels
    # and competitor prices -- because every one of them needs something to fetch. Measured:
    # run1 had 8/8 competitors with a domain and produced 3 taste decodes; run2 had 0/30
    # (OSM venues carried names only) and produced nothing, not even the honest
    # "cannot_decode" record the taste step writes when it tries and finds no voice. The
    # absence of a refusal is what made this hard to see: it looked like the step was never
    # meant to run.
    if opps and not top_3_comps:
        record_dropped_output(
            result, "audiences",
            f"no competitor carries a domain ({len(opps)} in the roster), so there was "
            "nothing to fetch customer voice from; audiences, personas, channel analysis "
            "and competitor pricing all depend on it")
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
        # W2-5: category relevance gate — off-category pages never anchor the median.
        return gather_competitor_prices(competitor_domains[:6],
                                        category=profile.get("category", ""))

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
        """Pull customer-voice sources in parallel — INDUSTRY-AWARE (cycle38). The dev forums
        (Stack Exchange / DEV.to / Lobsters) are only relevant to tech/dev/SaaS ventures; for a
        cafe or salon they return nothing but noise. They now run ONLY for tech ventures; every
        venture gets vertical_publication_mentions (industry trade press). All free, no key."""
        from sources import stackexchange_mentions, devto_mentions, lobsters_mentions, vertical_publication_mentions
        target = (top_3_comps[0]["brand"] if top_3_comps else profile.get("category", ""))
        if not target:
            return {}
        category = profile.get("category", "")
        is_tech = _is_tech_venture(profile)
        log.info("[plan] Step 6e: customer-voice sources for '%s' (tech_forums=%s)", target, is_tech)
        out = {"stackoverflow": [], "devto": [], "lobsters": [], "vertical_pubs": [], "_tech": is_tech}
        with ThreadPoolExecutor(max_workers=4) as p:
            futs = {"vertical_pubs": p.submit(vertical_publication_mentions, target, category, 10)}
            if is_tech:  # dev forums only help tech/dev/SaaS ventures
                futs["stackoverflow"] = p.submit(stackexchange_mentions, target, 12)
                futs["devto"] = p.submit(devto_mentions, target, 10)
                futs["lobsters"] = p.submit(lobsters_mentions, target, 10)
            for name, fut in futs.items():
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
            _step_done(result, "reddit_signal")
        checkpoint()

    # cycle25: persist HN customer voice as its own signal
    target_for_voice = top_3_comps[0]["brand"] if top_3_comps else profile.get("category", "")
    if hn_data:
        result["hn_signal"] = {
            "query": target_for_voice,
            "hits_found": len(hn_data),
            "hits": hn_data[:15],
        }
        _step_done(result, "hn_signal")
        checkpoint()

    # cycle27: persist Stack Exchange + DEV.to + Lobsters
    # cycle31-r2: + vertical_pubs for non-tech verticals
    if multisrc_data:
        _is_tech_run = bool(multisrc_data.get("_tech"))
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
            # Which sources were actually ASKED. _multisrc_task deliberately skips the dev
            # forums for non-tech ventures (cycle38) and recorded that as _tech — which this
            # allowlist then DROPPED, so a cafe's report showed "devto: 0, lobsters: 0,
            # stackoverflow: 0" for sources never queried, and the thin-signal flag below
            # docked its confidence for having no Stack Overflow presence. "Skipped as
            # irrelevant" and "asked and found nothing" must never be the same value —
            # the third instance of exactly this allowlist bug in this file.
            "queried": {
                "stackoverflow": _is_tech_run,
                "devto": _is_tech_run,
                "lobsters": _is_tech_run,
                "vertical_pubs": True,
            },
        }
        _step_done(result, "multi_source_signal")
        checkpoint()

    # Backwards-compat: keep `top_audience` as the first decoded profile
    top_audience = taste_results[0] if taste_results else {}
    if top_audience:
        result["audience"] = top_audience
        result["audiences"] = taste_results  # full set for transparency
        _step_done(result, "audience")
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
            _step_done(result, "personas")
            checkpoint()

    # --- Step 6c: STORM-style consumer research (multi-perspective) — cycle33 ---
    attach_consumer_research(result, description, geo, profile, opps, checkpoint=checkpoint)

    if competitor_pricing_data and competitor_pricing_data.get("competitors_with_prices", 0) > 0:
        result["competitor_pricing"] = competitor_pricing_data
        _step_done(result, "competitor_pricing")
        checkpoint()

    # --- Step 3d (MOVED, R4 rank 6): Differentiators + market gaps ---
    # This ran right after clustering — BEFORE competitor pricing, review themes, or
    # any scrape existed — under a prompt that mandated production. Name + 120-char
    # blobs in, ten "differentiators" out, asserting competitor pricing as unhedged
    # fact; strength pinned "high" on 16/16 and anchored the viability score. It now
    # runs HERE, after the evidence phase, and receives what that phase produced.
    try:
        from differentiators import extract_differentiators
        log.info("[plan] Step 3d (post-evidence): extracting differentiators + market gaps")
        _diff_evidence = {
            "competitor_pricing": {
                (d.get("domain") or "?"): {"price": d.get("median"), "unit": "unit",
                                            "n": d.get("count")}
                for d in (competitor_pricing_data.get("per_domain") or [])
                if isinstance(d, dict) and d.get("median") is not None
            },
            "review_themes": list((reddit_data or {}).get("themes") or [])[:6],
            "channels": [c.get("channel") for c in (channel_data.get("channels") or [])[:4]
                         if isinstance(c, dict)] if isinstance(channel_data, dict) else [],
        }
        diffs = extract_differentiators(
            profile=profile,
            our_features=profile.get("core_features", []),
            clustering=result.get("clustering") or {},
            competitors=opps,
            evidence=_diff_evidence,
        )
        if "error" not in diffs:
            result["differentiators"] = diffs
            _step_done(result, "differentiators")
            checkpoint()
    except Exception as e:
        log.warning(f"[plan] differentiators failed (non-fatal): {e}")

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
            _step_done(result, "max_diff")
            checkpoint()

    # --- Step 9b + Step 11 LLM recommendation in parallel (both need max_diff-ish inputs, but independent of each other) ---
    top_features = [f["feature"] for f in max_diff_result.get("ranked_features", [])[:5] if isinstance(f, dict)]

    # cycle37: classify market scale + business model BEFORE the PSM so the PSM simulates the right
    # monetization model (per-drink retail vs monthly subscription) rather than always SaaS tiers.
    if result.get("market_scale") is None:
        try:
            from skills.sizing.classify import classify_market_scale
            result["market_scale"] = classify_market_scale(description, geo).payload
            _step_done(result, "market_scale")
            log.info("[plan] Step 7a (early): market scale = %s",
                     (result.get("market_scale") or {}).get("scale"))
        except Exception as e:
            log.warning("[plan] early scale classification failed (non-fatal): %s", e)
    from business_model import classify_business_model, is_per_unit
    biz_kind = classify_business_model(profile, result.get("market_scale"))
    result["business_model_kind"] = biz_kind
    # cycle38: the economics/PSM unit must be model-derived and NEVER "/mo" for a per-unit
    # venture (infer_wtp_unit defaults to /mo → sized a $45 serum and a gym "per month").
    _psm_unit = unit_for_model(biz_kind, description, profile)
    # Only a true subscription uses the recurring (monthly) PSM frame; per-unit AND marketplace/
    # ad-supported price per transaction, not per month.
    _psm_recurring = (biz_kind == "subscription")
    log.info("[plan] business model = %s (psm unit=%s, recurring=%s)", biz_kind, _psm_unit, _psm_recurring)

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
            unit=_psm_unit,
            recurring=_psm_recurring,
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

    # W5 close-out: was a hand-rolled join whose timeout was COSMETIC. The
    # `future.result(timeout=90)` fired and set the degraded value, but exiting the
    # `with ThreadPoolExecutor(...)` calls shutdown(wait=True) — so the block waited
    # out the hung task anyway. A stalled provider call cost its full wall clock and
    # the log line claimed the pipeline had moved on. run_labeled releases the batch.
    _joined = run_labeled({"psm": (_psm_task, 90), "place": (_place_llm_task, 90)})
    psm_result = _joined["psm"]
    place_result = _joined["place"]
    for _k, _label in (("psm", "PSM"), ("place", "place recommendation")):
        if isinstance(_joined[_k], dict) and _joined[_k].get("error"):
            log.warning("[plan] %s failed: %s", _label, _joined[_k]["error"][:160])

    result["pricing"] = {"psm": psm_result}
    if not psm_result.get("error"):
        _step_done(result, "pricing")
        checkpoint()

    # C5 (Manus-parity): the user's stated price must not be silently dropped.
    # Reconcile it against the model's recommended optimal price, visibly.
    # R4 rank 16: in the venture's own unit, never a hardcoded "/mo".
    _recon_unit = f"/{_psm_unit}" if (is_per_unit(biz_kind) and _psm_unit) else "/mo"
    recon = reconcile_pricing(extract_stated_price(description),
                              psm_result.get("optimal_price_point"),
                              unit_label=_recon_unit)
    if recon:
        result["price_reconciliation"] = recon
        log.info("[plan] price reconciliation: stated=%s recommended=%s verdict=%s",
                 recon["stated_usd"], recon["recommended_usd"], recon["verdict"])

    # B3/D18: a SEPARATE reconciliation — the consumer-research WTP simulation vs the
    # PSM-recommended price. These can differ by 10-100x (a simulated individual's
    # casual WTP vs. an enterprise/B2B budget-holder's price point) and used to render
    # side by side with no comment. Disclose the mismatch; never fabricate agreement.
    _wtp_syn = (result.get("consumer_research") or {}).get("synthesis") or {}
    wtp_flag = reconcile_wtp_with_price(
        _wtp_syn.get("willingness_to_pay"),
        psm_result.get("optimal_price_point"),
        interviews=(result.get("consumer_research") or {}).get("interviews"))
    if wtp_flag:
        _wtp_syn["wtp_price_mismatch"] = wtp_flag
        log.info("[plan] WTP/price mismatch flagged: wtp=%s recommended=%s ratio=%sx",
                 wtp_flag["wtp"], wtp_flag["recommended"], wtp_flag["ratio"])

    if psm_result.get("optimal_price_point"):
        # cycle38: route ALL per-unit kinds (transactional/ecommerce/services/hybrid) through the
        # retail per-unit economics, not just literal "transactional".
        is_transactional = is_per_unit(biz_kind)
        # cycle36/37: cost structure is category-estimated + disclosed (not a hidden $5000/$2),
        # computed ONCE here and shared by break-even + unit economics.
        try:
            _opt = float(psm_result["optimal_price_point"])
        except (TypeError, ValueError):
            _opt = None
        from pricing import estimate_cost_structure
        _cost = estimate_cost_structure(
            profile.get("category", ""), _opt,
            market_scale=(result.get("market_scale") or {}).get("scale"),
        ) if _opt else None
        # Transactional retail prices per real unit (e.g. $6/drink), not the PSM monthly point.
        # Prefer an explicit per-unit stated price ("$6 per drink"), then a one-time
        # device/hardware price ("$199 device" — B2/D17: a hybrid hardware+subscription
        # venture used to fall through to the /mo app fee here, produce a negative
        # margin against real hardware COGS, error out of economics, and silently land
        # financials on the subscription model). Falls back to a monthly stated price,
        # then the PSM optimal.
        _unit_price = extract_unit_price(description)
        _device_price = extract_device_price(description)
        _stated = extract_stated_price(description)
        _unit_noun = _psm_unit  # cycle38: model-derived unit, never "/mo" for a per-unit venture
        # R4 rank 16: ONE disclosed price of record + provenance (which source won, the
        # PSM optimal, whether they materially differ) — so a second price can't hide.
        _por = price_of_record(_unit_price, _device_price, _stated, _opt, _unit_noun,
                               is_transactional)
        result["pricing"]["price_of_record"] = _por
        if is_transactional:
            _price_per_unit = _por["value"] if _por["value"] is not None else float(_opt)
        else:
            _price_per_unit = _opt

        # --- Break-even (subscription only — retail break-even lives in unit economics) ---
        if _cost and _opt and not is_transactional:
            try:
                result["pricing"]["break_even"] = compute_break_even(
                    _opt, monthly_fixed_cost=_cost["monthly_fixed_cost"],
                    variable_cost_per_customer=_cost["variable_cost_per_customer"],
                    cost_source=_cost["source"])
            except (TypeError, ValueError):
                pass

        # --- Per-unit pricing + competitor benchmark table (user feedback #3b) ---
        # D13's invariant, enforced UPSTREAM: a geo-sourced venture (local competitors from
        # OSM) gets NO scraped price benchmark. Their "pricing pages" are cafe/salon websites
        # where the scraper grabs whatever number it finds — MEASURED on run7, a $21 figure
        # (a bean bag or gift card) shipped as "Noe Cafe: $21 per drink, 4.0x our price".
        # Before this guard the pipeline BUILT the bad table and relied on the gate to block
        # the whole report; now it never builds one, and the reason is recorded instead of
        # the table silently missing.
        if (result.get("discover") or {}).get("geo_sourced"):
            record_dropped_output(
                result, "pricing_benchmark",
                "scraped price benchmarks are skipped for geo-sourced local ventures — "
                "venue websites rarely publish a clean per-unit price, and D13 blocks any "
                "report that ships one (measured: a $21 'per drink' row scraped from a "
                "cafe page)")
        else:
            try:
                from pricing import build_benchmark_table
                # _unit_noun == unit_for_model(biz_kind, ...) — already correct for every
                # kind (seat/account for subscription, booking for marketplace, the real
                # per-unit noun for transactional/etc). The old branch hand-rolled a
                # SEPARATE seat/account guess for "not is_transactional" that happened to
                # diverge from it for marketplace ("$450 per account" SaaS framing on a
                # per-booking price — R4 catch, 174ae091).
                bench = build_benchmark_table(
                    our_tiers=psm_result.get("recommended_tiers", []),
                    competitor_pricing=competitor_pricing_data,
                    pricing_unit=_unit_noun,
                    competitor_brands=opps[:8],
                    recurring=_pricing_is_recurring(biz_kind),  # D06: only true subscriptions
                )
                if "error" not in bench:
                    result["pricing"]["benchmark"] = bench
            except Exception as e:
                log.warning(f"[plan] pricing benchmark failed (non-fatal): {e}")

        # --- Step 10: economics — MODEL-AWARE (cycle37) ---
        # Transactional retail → contribution margin + break-even covers/day (no CLV/churn/SaaS).
        # Subscription → the original CLV + CAC + EVC decomposition.
        try:
            if is_transactional and _cost and _price_per_unit:
                from business_model import retail_unit_economics
                log.info("[plan] Step 10: retail unit economics (transactional, $%.2f/%s)",
                         _price_per_unit, _unit_noun)
                econ = retail_unit_economics(
                    price_per_unit=float(_price_per_unit),
                    variable_cost_per_unit=_cost["variable_cost_per_customer"],
                    monthly_fixed_cost=_cost["monthly_fixed_cost"],
                    unit=_unit_noun,
                    cost_source=_cost["source"],
                    category=profile.get("category", ""),
                    business_model=profile.get("business_model", ""),
                    kind=biz_kind,  # R6: model = the real kind, not hardcoded
                )
            elif biz_kind == "subscription":
                from economics import full_economics
                log.info("[plan] Step 10: CLV + CAC + EVC economics (subscription)")
                comp_prices = None
                if competitor_pricing_data and competitor_pricing_data.get("per_domain"):
                    comp_prices = [d["median"] for d in competitor_pricing_data["per_domain"] if d.get("median")]
                biz_model = (profile.get("business_model") or "").lower()
                unit = "seat" if "b2b" in biz_model or "saas" in biz_model else "account"
                econ = full_economics(
                    segment_summary=segment_summary,
                    product_summary=profile.get("summary", ""),
                    optimal_price_monthly=_opt,
                    pricing_unit=unit,
                    competitor_prices=comp_prices,
                )
            else:
                # cycle38: marketplace (take-rate on GMV) and ad-supported (eCPM on users) have a
                # different revenue basis than per-unit OR subscription. Rather than fabricate a
                # SaaS CLV:CAC (audit M12/M6 criticals), emit an HONEST labeled economics object
                # that names the right basis and the operator inputs it needs.
                log.info("[plan] Step 10: economics — %s (non per-unit, non-subscription)", biz_kind)
                if biz_kind == "marketplace":
                    econ = {"model": "marketplace",
                            "revenue_basis": "take-rate on third-party GMV (platform revenue = GMV × take-rate, NOT full GMV)",
                            "needs_operator_input": ["take-rate %", "avg transaction value", "transactions/period", "buyer & seller CAC"],
                            "note": "Per-subscriber CLV:CAC does not apply. Size revenue from GMV × take-rate; "
                                    "model two-sided unit economics (CAC for both sides) once the take-rate is set."}
                elif biz_kind == "ad_supported":
                    econ = {"model": "ad_supported",
                            "revenue_basis": "advertising (revenue = active users × sessions × impressions × eCPM × fill-rate)",
                            "needs_operator_input": ["eCPM", "fill rate", "sessions/MAU", "impressions/session", "content + ad-serving cost/user"],
                            "note": "Free to the user — there is no subscriber price, so subscriber CLV:CAC does not apply. "
                                    "Unit economics are ad-revenue-per-active-user minus cost-to-serve."}
                else:
                    econ = {"model": biz_kind,
                            "revenue_basis": "model-specific",
                            "note": f"Economics for '{biz_kind}' require operator-provided revenue inputs; "
                                    "subscriber CLV:CAC does not apply."}
            result["economics"] = econ
            if "error" not in econ:
                _step_done(result, "economics")
                checkpoint()
        except Exception as e:
            log.warning(f"[plan] economics computation failed (non-fatal): {e}")

    # cycle38: non-priced models (ad-supported, marketplace) often have no PSM optimal price, so
    # the priced block above is skipped — but we still owe the reader an HONEST economics object
    # naming the real revenue basis (never a fabricated SaaS CLV:CAC, and never silently blank).
    if not result.get("economics") and biz_kind in ("ad_supported", "marketplace"):
        if biz_kind == "ad_supported":
            result["economics"] = {"model": "ad_supported",
                "revenue_basis": "advertising (revenue = active users × sessions × impressions × eCPM × fill-rate)",
                "needs_operator_input": ["eCPM", "fill rate", "sessions/MAU", "impressions/session", "cost-to-serve/user"],
                "note": "Free to the user — no subscriber price, so subscriber CLV:CAC does not apply. "
                        "Unit economics = ad revenue per active user minus cost-to-serve."}
        else:
            result["economics"] = {"model": "marketplace",
                "revenue_basis": "take-rate on third-party GMV (platform revenue = GMV × take-rate, not full GMV)",
                "needs_operator_input": ["take-rate %", "avg transaction value", "transactions/period", "buyer & seller CAC"],
                "note": "Size revenue from GMV × take-rate; model two-sided CAC. Subscriber CLV:CAC does not apply."}
        _step_done(result, "economics")

    result["place"] = place_result
    if not place_result.get("error"):
        _step_done(result, "place")
        checkpoint()

    # --- Step 12: Validation gate ---
    log.info("[plan] Step 12: validation gate")
    val = _validation_gate(result)
    result["validation"] = val
    _step_done(result, "validation")
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
            business_model_kind=biz_kind,  # M4: forbid model bleed in the 4Ps narrative
            # RE-READ result["discover"] AT CALL TIME. `disc` is a local bound at the
            # discover step; the geo-roster promotion that sets competitor_density=30 and
            # geo_sourced runs later, and whether the local sees it depends on
            # mutation-vs-replacement semantics three functions away. run13's paired-count
            # rule never reached the prompts while the FINAL artifact carried the right
            # values — a call site must never depend on a stale binding when the source of
            # truth is one dict lookup away.
            competitor_density=(result.get("discover") or {}).get("competitor_density"),
            active_signal_density=(result.get("discover") or {}).get("active_signal_density"),
            # Volume ladder: SOM/day + break-even/day computed ONCE, injected into every
            # section — run9's 4Ps stated five incompatible daily targets (400/300/200/33
            # vs a 13.5/day sizing ceiling) because each section invented its own.
            market_sizing=result.get("market_sizing"),
        )

    # cycle33: classify market scale (numbers-right engine). Non-breaking — the
    # legacy estimate_market_size shape is preserved downstream; we annotate the
    # result with the scale decision and route physical ventures' caveats.
    # cycle37: reuse the scale already classified early (above). Only compute here if the early
    # pass failed, so we never double-append "market_scale" to _steps_completed.
    scale_decision = result.get("market_scale")
    if scale_decision is None:
        try:
            from skills.sizing.classify import classify_market_scale
            scale_decision = classify_market_scale(description, geo).payload
            result["market_scale"] = scale_decision
            _step_done(result, "market_scale")
            log.info("[plan] Step 7a: market scale = %s → %s",
                     scale_decision.get("scale"), scale_decision.get("sizing_skill"))
        except Exception as e:
            log.warning("[plan] scale classification failed (non-fatal): %s", e)

    # SIZING BEFORE 4PS — SEQUENCED ON PURPOSE, at a measured wall-clock cost. These two
    # used to run in parallel as "the pipeline's most expensive pair", and the overlap made
    # the narrative run AHEAD of the numbers it narrates: _four_ps_task reads
    # result["market_sizing"] at execution time, which was EMPTY mid-join, and the
    # hyperlocal override (the sizing that carries competitors + som) landed after the join
    # entirely. run14's _reminder_facts proved it: ms_competitors=null, ms_som_mid=null —
    # the volume ladder's SOM rung and the paired-competitor-count rule never once reached
    # a prompt. Sequencing costs the sizing's runtime (~30-90s, mostly cache-warm Census
    # calls) and buys every downstream narrative claim its inputs; it also hands the 4Ps
    # the geo roster and promoted density, which is what D22 wanted from the start.
    sizing = run_labeled({"sizing": (_sizing_task, 90)})["sizing"]
    if isinstance(sizing, dict) and sizing.get("error"):
        log.warning("[plan] market sizing failed: %s", sizing["error"][:160])

    if sizing and not sizing.get("error"):
        # C2: ground the bottom-up TAM in a live Census count BEFORE the gate, so
        # the report uses the real establishment count, not the LLM's guess.
        # F3: ARPU basis = stated price, else the modeled PSM optimal price, so the
        # Census-grounded bottom-up fires for most digital ventures (not only typed $/mo).
        _psm_price = (psm_result or {}).get("optimal_price_point")
        # biz_kind gates the modeled-price fallback: a per-unit price must not be
        # annualized into recurring revenue (see ground_sizing_bottom_up).
        sizing = ground_sizing_bottom_up(sizing, description, profile,
                                         arpu_monthly_fallback=_psm_price,
                                         biz_kind=biz_kind, result=result)
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
            # The override REPLACES the sizing, so it must be re-gated. Measured on a fresh
            # run: gate_and_annotate_sizing had already stamped scale_skill_ran and the
            # grounded/not-grounded disclosure onto the digital sizing, and assigning `hl`
            # here discarded both -- on exactly the path that now runs for physical ventures.
            # The gate is idempotent, so running it again on the new payload is the fix.
            hl = gate_and_annotate_sizing(hl, scale_decision)
            result["market_sizing"] = hl
            if "market_sizing" not in result["_steps_completed"]:
                _step_done(result, "market_sizing")
            log.info("[plan] hyperlocal sizing override (%s @ %s)",
                     (scale_decision or {}).get("scale"), hl.get("_hyperlocal_location"))
            _surface_late_geo_competitors(result, hl.get("geo_competitors") or [],
                                          category=(profile or {}).get("category", ""))
    except Exception as e:
        log.warning("[plan] hyperlocal override failed (non-fatal): %s", e)
        _step_done(result, "market_sizing")
        checkpoint()

    # 4Ps runs AFTER the sizing (including the hyperlocal override above) is in `result`,
    # so the section prompts carry the real SOM ladder and both competitor counts.
    four_ps = run_labeled({"four_ps": (_four_ps_task, 120)})["four_ps"]
    if isinstance(four_ps, dict) and four_ps.get("error"):
        log.warning("[plan] 4Ps synthesis failed: %s", four_ps["error"][:160])

    # Honesty gate: never credit a PSM simulation that didn't actually run (audit cycle36).
    four_ps = scrub_failed_psm_citations(four_ps, result.get("pricing"))
    result["four_ps"] = four_ps
    if not four_ps.get("error"):
        _step_done(result, "four_ps")
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
                _step_done(result, "segment_ranking")
            checkpoint()
        except Exception as e:
            log.warning(f"[plan] segment ranking failed (non-fatal): {e}")

    # --- Step 10b: Financial projections (deterministic, no LLM) ---
    # M3 fix (audit): consume the SINGLE canonical SOM from the FINAL market_sizing, not the
    # stale local `sizing` var. For physical ventures `result["market_sizing"]` was replaced by
    # the hyperlocal trade-area model AFTER `sizing` was computed, so reading `sizing` here made
    # financials + at-SOM economics use a different SOM than the report's headline → two
    # contradictory SOMs ("profitable at SOM" vs "every scenario loses money"). One source now.
    _som_blk = (result.get("market_sizing") or {}).get("som") or {}
    som_mid = _som_blk.get("mid")
    # W4-1: the scenarios ride the SOM BAND (low/mid/high) — the sizing model's own
    # venture-specific uncertainty — not a universal capture ladder on mid.
    som_low, som_high = _som_blk.get("low"), _som_blk.get("high")
    _mkt_scale = ((result.get("market_scale") or {}).get("scale") or "")
    optimal_price = psm_result.get("optimal_price_point")
    be = (result.get("pricing", {}) or {}).get("break_even", {}) or {}
    be_customers = be.get("break_even_customers")

    # cycle37 + G3: now that SOM is known, enrich transactional unit economics with the
    # at-SOM-volume profitability, pinned to the BASE scenario row so the claim can never
    # contradict the scenario table (D08/D23). See _enrich_economics_at_som.
    _econ = result.get("economics") or {}
    if _econ:
        result["economics"] = _enrich_economics_at_som(
            _econ, som_mid, som_high=som_high, som_low=som_low,
            category=profile.get("category", ""),
            business_model=profile.get("business_model", ""),
            market_scale=_mkt_scale)

    # W4-1: revenue-only models (marketplace, ad_supported) need no per-customer
    # price — gating them on optimal_price starved a sized venture of ANY financials
    # (3219f4db: SOM $2.5M, no projection at all).
    _fin_model = ("transactional" if is_per_unit(biz_kind)
                 else biz_kind if biz_kind in ("marketplace", "ad_supported")
                 else "subscription")
    _needs_price = _fin_model not in ("marketplace", "ad_supported")
    if som_mid and (optimal_price or not _needs_price):
        log.info("[plan] Step 10b: 3-year financial projections")
        # R4 rank 2: the venture's own published CAC feeds the break-even
        # feasibility check — a break-even year whose acquisition spend exceeds
        # that year's revenue is not claimable.
        _cac = (((result.get("economics") or {}).get("unit_economics") or {})
                .get("typical_cac_usd"))
        proj = project_three_year(
            som_mid=float(som_mid),
            optimal_price=float(optimal_price) if optimal_price else None,
            break_even_customers=be_customers,
            break_even_costs=be,  # cycle36: surface the cost assumptions in the report
            model=_fin_model,  # cycle37/38 + C3 + W4-1
            economics=result.get("economics"),
            som_low=som_low, som_high=som_high,
            market_scale=_mkt_scale,
            cac_usd=float(_cac) if isinstance(_cac, (int, float)) and _cac > 0 else None,
        )
        if not proj.get("error"):
            # R4 rank 5: a projection computed from a withheld SOM carries the
            # withhold with it — the data-layer decision the template banner renders.
            from financials import mark_derived_from_withheld
            result["financials"] = mark_derived_from_withheld(
                proj, result.get("market_sizing"))
            _step_done(result, "financials")
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
        # NOT `or 0`: an unmeasured momentum count coerced to zero reads as "no rival has
        # any web presence", which is a finding, not a gap — and it is the finding the
        # corpus acted on. None reaches the prompt as "not measured".
        active_density=disc.get("active_signal_density"),
        avg_score=disc.get("avg_opportunity_score"),
        audience_confidence=top_audience.get("confidence", 0) or 0,
        signal_count=signal_count,
        differentiators_strength=(result.get("differentiators") or {}).get("differentiation_strength"),
        differentiators_count=len((result.get("differentiators") or {}).get("differentiators", [])),
        customer_universe_count=(result.get("customer_universe") or {}).get("count"),
        economics_evc=(result.get("economics") or {}).get("evc", {}).get("verdict"),
        economics_clv=(result.get("economics") or {}).get("clv", {}).get("clv_usd"),
        market_sizing=result.get("market_sizing"),  # cycle36: score opportunity on the real TAM/scale
        business_model_kind=biz_kind,  # M4: forbid subscription/MRR bleed in viability narrative
        economics=result.get("economics"),
    )
    viability = _run_with_timeout(score_viability, timeout_s=90, label="viability", **viability_kwargs)
    if viability.get("error"):
        log.warning("[plan] viability errored on first try (%s) — retrying with 180s timeout",
                    viability.get("error"))
        viability = _run_with_timeout(score_viability, timeout_s=180, label="viability(retry)", **viability_kwargs)
    result["viability"] = viability
    if not viability.get("error"):
        _step_done(result, "viability")
        checkpoint()
    else:
        log.warning("[plan] viability FAILED twice — surfacing as validation flag")

    # cycle30: re-run validation gate at end so viability/segment/source flags
    # surface — the early gate ran before downstream signals existed.
    # cycle36 FIX: the final gate runs on the COMPLETE result, so it strictly supersedes
    # the early pass. We must NOT union the two flag lists — the early pass (run before
    # sizing+viability) emitted stale "Viability step was skipped" / "TAM 0/3 methods"
    # flags that the final pass correctly omits. Use the final flags; keep MIN confidence.
    final_val = _validation_gate(result)
    if result.get("validation"):
        prev = result["validation"]
        merged_conf = min(prev.get("confidence_score", 1.0), final_val.get("confidence_score", 1.0))
        result["validation"] = {"flags": final_val.get("flags") or [], "confidence_score": merged_conf}
    else:
        result["validation"] = final_val

    # cycle33: opt-in generator-evaluator-refine pass (Anthropic harness pattern).
    if refine:
        log.info("[plan] running generator-evaluator-refine loop")
        result = refine_pipeline_result(result, description, geo, profile, opps)
        if "refine" not in (result.get("_steps_completed") or []):
            result.setdefault("_steps_completed", []).append("refine")

    result["_duration_seconds"] = round(time.time() - t_start, 1)
    # Snapshot the provenance trace into the result so the report can render the debug panel.
    try:
        if _trace is not None:
            result["_trace"] = _trace.snapshot()
    except Exception:
        pass

    # W5-7: the run's plan as a first-class artifact. `_steps_completed` is a flat
    # list of names, so nothing could answer "what was this run SUPPOSED to do, and
    # what happened to each part of it?". The expensive half is SKIPS: a step skipped
    # because a backend was unconfigured produces a report indistinguishable from one
    # where that step ran and found nothing. Bookkeeping, so it never raises.
    try:
        from orchestrator.plan_artifact import PlanArtifact
        _declared = list(dict.fromkeys(result.get("_steps_completed") or []))
        _artifact = PlanArtifact(_declared)
        for _n in _declared:
            _artifact.finish(_n)
        result["_plan"] = {**_artifact.to_dict(), "summary": _artifact.summary()}
    except Exception as e:
        log.debug("[plan] plan artifact unavailable: %s", e)

    # W6: the research crew as an evidence stage — DEEP effort only. It has existed
    # since cycle33 but was reachable only via POST /research/crew, so its evidence
    # never reached a report. Returns None when the lever is off, which is distinct
    # from an error: a reader must be able to tell "not bought" from "bought and failed".
    try:
        from orchestrator.steps.crew import run_crew_step
        _brief = run_crew_step(result, description, geo, effort_levers=_levers)
        if _brief is not None:
            result["research_brief"] = _brief
            _step_done(result, "research_crew")
    except Exception as e:
        log.warning("[plan] research crew stage failed: %s", e)

    # W6-1: run the 22 invariants on THIS report before it ships. gates.py has only
    # ever swept a corpus after the fact — a developer's view. This is the buyer's:
    # what would have gone out wrong. Advisory by default (it annotates the report);
    # never allowed to fail the run, because a verifier that can crash a paid report
    # is a worse trade than one that occasionally misses.
    try:
        from report.verifier import verify_report
        # Render the page BEFORE verifying it. Measured: 10 invariants — every one
        # fail-severity — can only return a verdict when they can read the rendered report
        # (D02/D06/D24/D25/D27/D36/D41/D43/D45/D48), so verifying with html=None left the
        # pass blind to the whole class of defects that only exist once the report is a page,
        # while reporting a clean verdict. Best-effort: a render problem degrades coverage,
        # it must never fail a paid run.
        _html = None
        try:
            from report.render_html import render_report_html
            _html = render_report_html(result)
        except Exception as e:
            log.warning("[plan] pre-verification render failed, verifying without the "
                        "page (%d page-dependent gates will report as blind): %s",
                        10, e)
        vr = verify_report(result, _html, use_llm=_levers["verify_with_llm"])
        result["verification"] = {
            "summary": vr.summary(),
            "findings": [{"invariant": f.invariant, "severity": f.severity,
                          "detail": f.detail, "audit_class": f.audit_class}
                         for f in vr.findings],
        }
        if not vr.publishable:
            log.warning("[plan] verification found %d blocking issue(s)",
                        vr.summary().get("block", 0))
    except Exception as e:
        log.warning("[plan] verification pass failed: %s", e)

    # Item 6: release the ledger on the normal path. An early return leaves it behind, which
    # persistence.transcript.attach reclaims on the next direct run rather than going silent.
    try:
        from persistence import transcript as _tr_done
        _tr_done.detach(_own_transcript)
    except Exception:
        pass

    # W6-4: what this report cost to produce. Unanswerable before now, which made
    # pricing the product a guess instead of a margin calculation.
    try:
        from persistence import ledger as _ledger
        result["_cogs"] = _ledger.cogs()
    except Exception as e:
        log.debug("[plan] cogs unavailable: %s", e)
    return result
