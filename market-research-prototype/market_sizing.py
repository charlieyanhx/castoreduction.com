"""
Step 7 of spec: Market sizing — TAM/SAM/SOM estimation.

TAM = Total Addressable Market (everyone who could ever buy this)
SAM = Serviceable Addressable Market (the segment we can realistically reach)
SOM = Serviceable Obtainable Market (realistic capture in years 1-3)

Approach:
- Use LLM with grounding inputs: category, geography, competitor count, pricing,
  audience profile, competitor scale (IG followers as proxy for reach).
- Force the LLM to show its arithmetic so the operator can sanity-check.
- Return ranges (low/mid/high) not point estimates — honesty about uncertainty.
- Cite the assumptions explicitly so they can be challenged.
"""
from __future__ import annotations
from llm import call_json
from logger import get

log = get("market_sizing")


SIZING_PROMPT = """You are a VC associate at a tier-1 firm preparing the market-sizing
section of an investment memo. Produce TAM/SAM/SOM via THREE INDEPENDENT
methods (top-down + bottom-up + analog) with explicit reconciliation.
This is a FALSIFIABLE financial document — every number must be traceable.

PROFILE:
{profile}

CATEGORY: {category}
GEOGRAPHY: {geography}

COMPETITOR LANDSCAPE:
{competitors}

PRICING ANCHOR:
- Median competitor price: {price_anchor}
- PSM-recommended optimal: {optimal_price}
- Acceptable range: {price_range}

AUDIENCE:
{audience}

═══════════════════════════════════════════════════════════════════
METHODOLOGY — apply ALL THREE independently, then reconcile.
═══════════════════════════════════════════════════════════════════

**METHOD 1 — TOP-DOWN** (industry-report based)
  Cite a known category report (Gartner / IDC / Forrester / Statista / CB Insights)
  with a concrete TAM number, then adjust for our specific geo + segment.
  Example: "Global digital wellness market $X (Gartner 2024) × US share 35% × employer-sponsored slice 12% = $Y"

**METHOD 2 — BOTTOM-UP** (firm-count × ACV)
  Segment addressable accounts by NAICS / vertical / employee band using known
  public stats (BLS QCEW, Census SUSB, IRS SOI). For each segment compute:
  (firm count) × (seats per firm or units) × (ACV with tier logic) = segment TAM.
  Sum segments. Example: "US firms 200-2000 emp = 50k (BLS QCEW); 800 avg seats × $48 ACV = $1.92B"

**METHOD 3 — ANALOG** (comparable-company implied TAM)
  Pick 1-2 comparable companies (similar ICP / ACV / GTM) at Series B/C
  scale. Cite their disclosed ARR (S-1, press, Pitchbook). Derive implied
  TAM penetration. Example: "Calm for Business reached $50M ARR by Y3 = ~2% of TAM → implied TAM $2.5B"

═══════════════════════════════════════════════════════════════════

Return JSON in this EXACT order (SOM first so it survives truncation):

{{
  "som": {{
    "label": "Serviceable Obtainable Market — year 3 capture",
    "low": <number>, "mid": <number>, "high": <number>,
    "currency": "USD", "unit": "year 3 revenue",
    "method": "analog-anchored — cite Year-3 ARR of comparable",
    "calculation": "≤120 chars showing the math",
    "comparable_anchor": "company name + their year-3 ARR + source",
    "key_assumption": "the single biggest assumption that drives this number"
  }},
  "sam": {{
    "label": "Serviceable Addressable Market — what we can realistically reach",
    "low": <number>, "mid": <number>, "high": <number>,
    "currency": "USD", "unit": "annual revenue",
    "calculation": "≤120 chars — TAM × geo × regulatory × channel × ICP-fit waterfall",
    "serviceability_waterfall": "TAM ${{X}}B → US-only ({{Y}}%) → ICP-matching ({{Z}}%) → reachable via our channel ({{W}}%)",
    "key_assumption": "the single biggest assumption"
  }},
  "tam": {{
    "label": "Total Addressable Market",
    "low": <number>, "mid": <number>, "high": <number>,
    "currency": "USD", "unit": "annual revenue",
    "method_top_down": {{
      "value_usd": <number>,
      "unit": "revenue" or "gmv" — what value_usd MEASURES. For take-rate/commission models: total transaction volume is "gmv"; platform revenue (take rate applied) is "revenue". Mixing them 6x-inflates TAM.,
      "calculation": "≤100 chars: industry report × geo × segment %",
      "source": "name of report or analyst, e.g. 'Gartner Digital Wellness 2024'"
    }},
    "method_bottom_up": {{
      "value_usd": <number>,
      "unit": "revenue" or "gmv" — what value_usd MEASURES. For take-rate/commission models: total transaction volume is "gmv"; platform revenue (take rate applied) is "revenue". Mixing them 6x-inflates TAM.,
      "calculation": "≤100 chars: firm count × seats × ACV",
      "source": "BLS QCEW / Census SUSB / similar"
    }},
    "method_analog": {{
      "value_usd": <number>,
      "unit": "revenue" or "gmv",
      "calculation": "≤100 chars: comparable's ARR × implied penetration inverse",
      "source": "company name + filing/press source"
    }},
    "reconciliation": "1 sentence on whether the 3 methods converge or diverge, and which is most credible here"
  }},
  "segmentation": [
    {{"segment": "vertical or size band, e.g. 'Tech firms 200-500 employees'", "share_pct": <number 0-100>, "tam_usd": <number>}},
    {{"segment": "second segment", "share_pct": <number>, "tam_usd": <number>}}
  ],
  "growth_outlook": "1 sentence — secular tailwind or headwind, cite 1 driver",
  "growth_cagr_pct": <number>,
  "weakest_assumptions": ["3 most-vulnerable assumptions across the model — what would change my mind"],
  "data_quality": "low | medium | high",
  "sources_to_validate": ["3-5 specific reports/databases the operator should pull"]
}}

RULES:
- ALL THREE methods (top_down + bottom_up + analog) inside `tam` MUST be filled — even if estimated
- `som` MUST cite a comparable-company ARR anchor (no "1% of TAM" hand-waving)
- `sam` MUST show the serviceability waterfall (% drop-off at each step)
- USD figures, annualized, rounded sensibly
- If you don't know a specific industry report, cite the closest analog you do know — never fabricate report names"""


_METHOD_KEYS = ("method_top_down", "method_bottom_up", "method_analog")


def apply_tam_triangulation(tam_block: dict) -> None:
    """W4-1: the ONE way a TAM headline gets written from its methods.

    Replaces the three ad-hoc mean-recompute sites (first derivation, post-retry, and
    the cycle25 'reconcile') that each rewrote mid/low/high under their own formula
    while a hardcoded 'unweighted average' sentence stayed behind describing none of
    them. Every write now regenerates BOTH the numbers and the prose from
    report.forecast.triangulate — a number is never written without its sentence.
    Mutates tam_block in place; no-op when no method carries a numeric value.
    """
    from report.forecast import Method, triangulate as _tri
    methods = []
    for k in _METHOD_KEYS:
        m = tam_block.get(k) or {}
        v = m.get("value_usd")
        try:
            if v is not None:
                methods.append(Method(
                    name=k.replace("method_", ""), value_usd=float(v),
                    unit=(m.get("unit") or "revenue").lower(),
                    origin=(m.get("data_origin") or "llm").lower(),
                    formula=m.get("calculation") or "", source=m.get("source") or ""))
        except (TypeError, ValueError):
            continue
    if not methods:
        return
    s = _tri(methods)
    tam_block["mid"], tam_block["low"], tam_block["high"] = s.mid, s.low, s.high
    tam_block["reconciliation"] = s.derivation
    tam_block["range_basis"] = s.range_basis
    tam_block["n_independent_origins"] = s.n_independent
    excluded = {m.name for m in s.unit_conflict}
    for k in _METHOD_KEYS:
        if k.replace("method_", "") in excluded and tam_block.get(k):
            tam_block[k]["excluded_from_headline"] = True


def estimate_market_size(
    profile: dict,
    competitors: list[dict],
    audience: dict,
    competitor_pricing: dict,
    psm_result: dict,
) -> dict:
    """LLM-based TAM/SAM/SOM with explicit arithmetic + assumptions."""

    # Build compact inputs
    profile_blob = (
        f"Name: {profile.get('name','?')}\n"
        f"Summary: {(profile.get('summary') or '')[:400]}\n"
        f"Business model: {profile.get('business_model','?')}\n"
        f"Target customer: {(profile.get('apparent_target_customer') or '')[:300]}\n"
    )

    comp_blob = "\n".join(
        f"  - {c.get('brand','?')} (relevance: {c.get('relevance','?')}) — score {c.get('opportunity_score','?')}"
        for c in competitors[:6]
    ) if competitors else "  (no competitors found)"

    audience_blob = (
        f"Decoded from: {audience.get('brand','?')}\n"
        f"Confidence: {audience.get('confidence','?')}\n"
        f"Motivation: {(audience.get('purchase_motivation') or '')[:300]}\n"
        f"Life context: {audience.get('life_context',[])[:3]}\n"
    ) if audience else "  (no audience decoded)"

    price_anchor = competitor_pricing.get("category_median") if competitor_pricing else None
    price_anchor_str = f"${price_anchor}" if price_anchor else "unknown (competitors don't expose prices)"

    optimal = psm_result.get("optimal_price_point") if psm_result else None
    optimal_str = f"${optimal}" if optimal else "unknown"

    # R4 rank 21: the bottom-up method is firm-count × ACV, but it was fed the raw
    # monthly price as the ACV anchor with no period label — so a $14/mo SaaS was sized
    # on a ~$15 "ACV" instead of $168/yr, a 10-12x TAM understatement (4a755faa,
    # becc8783). Annualize the ACV explicitly for recurring models and label the period.
    acv_str = _acv_anchor(optimal, profile.get("business_model"))

    price_range = psm_result.get("acceptable_range") if psm_result else None
    price_range_str = f"${price_range[0]}–${price_range[1]}" if price_range else "unknown"

    # Iter 42 (issue 4): split TAM/SAM/SOM into 3 parallel focused calls.
    # Was producing TAM=null because LLM truncated after writing SOM+SAM.
    # Each call now has its own 2500-token budget and only one layer to fill.
    from concurrent.futures import ThreadPoolExecutor

    shared_ctx = SIZING_PROMPT.format(
        profile=profile_blob,
        category=profile.get("category", "unknown"),
        geography=profile.get("geography", "US"),
        competitors=comp_blob,
        audience=audience_blob,
        price_anchor=price_anchor_str,
        optimal_price=optimal_str,
        price_range=price_range_str,
    )

    # Iter 43: each per-layer prompt is now SELF-CONTAINED (no shared mega-template).
    # The previous approach included the giant 3-method TAM schema in every call,
    # confusing the LLM into ignoring the secondary asks (segmentation, weakest_assumptions etc).
    # cycle31-r2 (Discovery 1 fix): pass scope_hint to anchor TAM scoping to the
    # specific subsegment the venture targets, not the broadest defensible category.
    # Decagon scoped to ALL customer-service software ($20B) when the venture is
    # AI-agent-only ($1.5B). Without this hint, LLM defaults to broadest scoping.
    scope_hint = (profile.get("tam_scope_hint") or "").strip()
    scope_line = (
        f"\nSUBSEGMENT TO SCOPE TO (anchor TAM here, not on the broader category): {scope_hint}\n"
        if scope_hint and scope_hint.lower() not in ("broad horizontal — no narrower scope hint", "")
        else ""
    )
    base_ctx = (
        f"Profile: {profile_blob[:600]}\n"
        f"Category: {profile.get('category', 'unknown')}\n"
        f"{scope_line}"
        f"Geography: {profile.get('geography', 'US')}\n"
        f"Competitors: {comp_blob[:500]}\n"
        f"Audience: {audience_blob[:300]}\n"
        f"Pricing anchors: median ${price_anchor_str}, optimal ${optimal_str}, range {price_range_str}\n"
        f"ACV anchor for the BOTTOM-UP method: {acv_str}\n"
    )
    # cycle30: TAM was getting 1-2/3 methods filled even with retry. Split into
    # 3 separate single-method calls + 1 reconciliation call. Forces full filling.
    layer_prompts = {
        "tam_top_down": (base_ctx + """
Estimate TAM by the TOP-DOWN method ONLY: cite a known industry report and adjust
for the geo + segment.

Example output style: "Global wellness market $80B (Gartner 2024) × US share 35% × employer-sponsored slice 12% = $3.36B"

Output ONLY this:
{
  "method_top_down": {
    "value_usd": <num>,
    "calculation": "≤100 chars showing the arithmetic chain",
    "source": "Gartner / IDC / Forrester / Statista / etc — name the report"
  }
}
If you genuinely don't know a real report, label source "estimated from category norms"
and still produce a defensible number."""),
        "tam_bottom_up": (base_ctx + """
Estimate TAM by the BOTTOM-UP method ONLY: firm-count × ACV. Use BLS QCEW or
Census SUSB-style firm counts and our own pricing as ACV anchor.

Example output style: "US firms 200-2000 employees = 50k × 800 avg seats × $48 ACV = $1.92B"

Output ONLY this:
{
  "method_bottom_up": {
    "value_usd": <num>,
    "calculation": "≤100 chars: firm count × seats/units × ACV",
    "source": "BLS QCEW / Census SUSB / similar public stat"
  }
}
HARD REQUIREMENT: must produce a numeric value_usd."""),
        "tam_analog": (base_ctx + """
Estimate TAM by the ANALOG method ONLY: pick a comparable Series-B/C company,
cite their disclosed ARR, derive implied TAM penetration.

Example output style: "Calm for Business reached $50M ARR by Y3 (press 2023) = ~2% of TAM → implied TAM $2.5B"

Output ONLY this:
{
  "method_analog": {
    "value_usd": <num>,
    "calculation": "≤100 chars: comparable's ARR ÷ implied penetration",
    "source": "company name + filing/press source"
  }
}
HARD REQUIREMENT: must produce a numeric value_usd."""),
        "sam_seg": (base_ctx + """
Estimate SAM (Serviceable Addressable Market) AND produce TAM segmentation. Output BOTH blocks:
{
  "sam": {
    "label": "Serviceable Addressable Market", "low": <num>, "mid": <num>, "high": <num>,
    "currency": "USD", "unit": "annual revenue",
    "calculation": "≤120 chars",
    "serviceability_waterfall": "TAM $XB → US-only (Y%) → ICP-matching (Z%) → reachable (W%)",
    "key_assumption": "biggest assumption"
  },
  "segmentation": [
    {"segment": "vertical or size band — e.g. 'Tech firms 200-500 employees'", "share_pct": <num 0-100>, "tam_usd": <num>},
    {"segment": "second segment", "share_pct": <num>, "tam_usd": <num>},
    {"segment": "third segment", "share_pct": <num>, "tam_usd": <num>}
  ]
}
The 3 segmentation entries' share_pct MUST sum to ~100. tam_usd values MUST sum to your TAM mid estimate."""),
        "som": (base_ctx + """
Estimate SOM (Serviceable Obtainable Market — what we capture in year 3).
Anchor on a comparable company's actual year-3 ARR. Output ONLY this block:
{
  "som": {
    "label": "Serviceable Obtainable Market — year 3 capture",
    "low": <num>, "mid": <num>, "high": <num>,
    "currency": "USD", "unit": "year 3 revenue",
    "method": "analog-anchored",
    "comparable_anchor": "company name + their year-3 ARR + source",
    "key_assumption": "the single biggest assumption",
    "calculation": "≤120 chars showing the math (e.g. 'Calm B2B Y3 ARR $50M × ICP-fit 30% × wedge 20% = $3M')"
  }
}
HARD REQUIREMENT: low/mid/high are all numeric (no nulls, no ranges)."""),
        "meta": (base_ctx + """
Estimate market metadata. Output ALL fields — short ones first survive truncation:
{
  "growth_cagr_pct": <num — required, single value not a range>,
  "data_quality": "low | medium | high",
  "growth_outlook": "1 sentence — secular tailwind/headwind, cite 1 driver",
  "weakest_assumptions": [
    "weakest assumption 1 — ≤30 chars",
    "weakest assumption 2 — ≤30 chars",
    "weakest assumption 3 — ≤30 chars"
  ],
  "sources_to_validate": [
    "specific report or DB 1",
    "specific report or DB 2",
    "specific report or DB 3"
  ]
}
ALL fields REQUIRED. growth_cagr_pct must be a SINGLE number (e.g. 23, not "18-28")."""),
    }

    layer_results = {}
    def _run_layer(key):
        return key, call_json(
            system="You are a rigorous market analyst. Show arithmetic. Use ranges. Return only JSON.",
            user=layer_prompts[key],
            max_tokens=4500,  # iter 43-cycle19: bumped from 3000 — som_meta + sam_seg layers were truncating
        )
    with ThreadPoolExecutor(max_workers=6) as pool:  # cycle30: 4→6 (TAM split into 3 single-method calls)
        for key, r in pool.map(_run_layer, list(layer_prompts.keys())):
            layer_results[key] = r
            # iter 43-cycle20: surface what each layer actually returned so we can debug
            if isinstance(r, dict):
                log.info("[market_sizing/%s] returned keys=%s parse_error=%s",
                         key, list(r.keys())[:10], "_parse_error" in r)
                if "_parse_error" in r:
                    log.warning("[market_sizing/%s] _raw preview: %s", key,
                                (r.get("_raw") or "")[:300])

    # cycle30: assemble the TAM block from the 3 single-method calls
    def _coerce_value_usd(v):
        """LLM sometimes returns value_usd as {"min": x, "max": y} or "1.5B" string.
        Coerce to a single float (midpoint for ranges)."""
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict):
            mn = v.get("min") or v.get("low")
            mx = v.get("max") or v.get("high")
            if isinstance(mn, (int, float)) and isinstance(mx, (int, float)):
                return (float(mn) + float(mx)) / 2.0
            single = v.get("value") or v.get("amount") or v.get("usd")
            if isinstance(single, (int, float)):
                return float(single)
            return None
        if isinstance(v, str):
            import re as _re
            # strip $, B/M/K suffixes
            txt = v.replace("$", "").replace(",", "").strip()
            mult = 1
            if txt.lower().endswith("b"):
                mult = 1_000_000_000; txt = txt[:-1]
            elif txt.lower().endswith("m"):
                mult = 1_000_000; txt = txt[:-1]
            elif txt.lower().endswith("k"):
                mult = 1_000; txt = txt[:-1]
            try:
                return float(txt.strip()) * mult
            except ValueError:
                pass
        return None

    tam_block: dict = {
        "label": "Total Addressable Market",
        "currency": "USD",
        "unit": "annual revenue",
    }
    for method_key in ("tam_top_down", "tam_bottom_up", "tam_analog"):
        layer = layer_results.get(method_key) or {}
        if not isinstance(layer, dict) or "_parse_error" in layer:
            continue
        for k, v in layer.items():
            if k.startswith("method_") and isinstance(v, dict):
                coerced = _coerce_value_usd(v.get("value_usd"))
                if coerced is not None and coerced > 0:
                    # Normalize: store coerced scalar back into the block
                    block_copy = dict(v)
                    block_copy["value_usd"] = coerced
                    tam_block[k] = block_copy
    method_values = [
        tam_block[k]["value_usd"]
        for k in ("method_top_down", "method_bottom_up", "method_analog")
        if k in tam_block and isinstance(tam_block[k].get("value_usd"), (int, float))
    ]
    if method_values:
        apply_tam_triangulation(tam_block)

    # Merge fields from sam/som/meta layers. The TAM block we just built takes precedence.
    result = {"tam": tam_block} if method_values else {}
    for key, r in layer_results.items():
        if key.startswith("tam_"):
            continue  # already merged above
        if not isinstance(r, dict) or "_parse_error" in r:
            continue
        for k, v in r.items():
            if v is None:
                continue
            if k in result and result[k] and not v:
                continue
            result[k] = v

    if not result:
        return {
            "error": "Market sizing returned malformed JSON for all 3 layers",
            "_raw_preview": "",
        }

    # Iter 43 (issue B/N): post-process segmentation to enforce math consistency.
    # LLM regularly returns share_pct × TAM that doesn't reconcile (e.g. 40% × $3.4B
    # but tam_usd=$800K — off by 1000×). Recompute tam_usd = share_pct × parent_TAM.
    tam_mid = (result.get("tam") or {}).get("mid")
    seg = result.get("segmentation") or []
    if tam_mid and seg:
        try:
            tam_mid_v = float(tam_mid)
            for s in seg:
                share = s.get("share_pct")
                if share is not None:
                    try:
                        share_v = float(share)
                        # Recompute tam_usd from share_pct × tam_mid (authoritative)
                        s["tam_usd"] = round(share_v / 100.0 * tam_mid_v)
                    except (TypeError, ValueError):
                        pass
            # Also: if shares don't sum to ~100, normalize them
            total = sum(float(s.get("share_pct") or 0) for s in seg)
            if total > 0 and not (90 <= total <= 110):
                for s in seg:
                    sp = float(s.get("share_pct") or 0)
                    s["share_pct"] = round(sp / total * 100, 1)
                    s["tam_usd"] = round(s["share_pct"] / 100.0 * tam_mid_v)
        except (TypeError, ValueError):
            pass

    # Iter 43 (issue C): drop None/empty entries from list fields
    for k in ("weakest_assumptions", "sources_to_validate"):
        v = result.get(k)
        if isinstance(v, list):
            result[k] = [x for x in v if x is not None and (not isinstance(x, str) or x.strip())]

    # cycle30: TAM is now built from 3 SEPARATE single-method calls (see top of fn).
    # If any individual method failed, retry just that one (much cheaper than full retry).
    tam = result.get("tam") or {}
    missing_methods = [
        k for k in ("method_top_down", "method_bottom_up", "method_analog")
        if not tam.get(k) or not (tam.get(k, {}).get("value_usd"))
    ]
    if missing_methods and len(missing_methods) < 3:
        log.info("[market_sizing] retrying %d missing TAM methods: %s",
                 len(missing_methods), missing_methods)
        for method_key in missing_methods:
            layer_key = "tam_" + method_key.replace("method_", "")
            try:
                retry = call_json(
                    system="You are a rigorous market analyst. Return only JSON.",
                    user=layer_prompts[layer_key] + "\n\nIMPORTANT: previous attempt failed — must produce numeric value_usd this time.",
                    max_tokens=2000,
                )
                if isinstance(retry, dict) and "_parse_error" not in retry:
                    block = retry.get(method_key)
                    if isinstance(block, dict) and block.get("value_usd"):
                        tam[method_key] = block
                        log.info("[market_sizing] retry succeeded for %s", method_key)
            except Exception as e:
                log.warning("[market_sizing] retry for %s failed: %s", method_key, e)
        # Recompute mid/low/high after retries
        method_values = [
            tam[k]["value_usd"]
            for k in ("method_top_down", "method_bottom_up", "method_analog")
            if k in tam and tam[k].get("value_usd")
        ]
        if method_values:
            # W4-1: same single writer as everywhere else — the retry changed the
            # method set, so the prose must change with the numbers.
            apply_tam_triangulation(tam)
            result["tam"] = tam

    # cycle23: sam_seg layer also stochastic — sometimes returns segmentation: [],
    # which shows as an empty TAM segmentation table. Retry once if missing.
    if not (result.get("segmentation") or []):
        log.info("[market_sizing] sam_seg returned no segmentation — retrying")
        try:
            retry = call_json(
                system="You are a rigorous market analyst. Return only JSON.",
                user=layer_prompts["sam_seg"] + (
                    "\n\nIMPORTANT: the segmentation array is REQUIRED. Provide exactly 3 entries. "
                    "Their share_pct must sum to ~100. tam_usd values must be proportional to TAM."
                ),
                max_tokens=4500,
            )
            if isinstance(retry, dict) and "_parse_error" not in retry:
                rs_seg = retry.get("segmentation") or []
                if rs_seg:
                    result["segmentation"] = rs_seg
                    # also pick up sam if the retry produced a fuller version
                    if retry.get("sam") and not (result.get("sam") or {}).get("mid"):
                        result["sam"] = retry["sam"]
                    log.info("[market_sizing] sam_seg retry succeeded: %d segments", len(rs_seg))
        except Exception as e:
            log.warning("[market_sizing] sam_seg retry failed: %s", e)

    # cycle25: reconcile TAM headline (low/mid/high) with the per-method values.
    # If headline tam.mid disagrees with the methods (e.g. headline says $325M but
    # only top_down=$248M is filled), recompute mid as the average of available
    # method values so the table and the headline can never contradict.
    tam = result.get("tam") or {}
    method_values = []
    for k in ("method_top_down", "method_bottom_up", "method_analog"):
        m = tam.get(k) or {}
        v = m.get("value_usd")
        try:
            if v is not None:
                method_values.append(float(v))
        except (TypeError, ValueError):
            pass
    if method_values:
        # W4-1: no more 20%-tolerance "reconcile" under its own (mean) formula — the
        # designated headline-can't-contradict-the-table site tolerated a 20%
        # contradiction by design and would have reverted a correct median. One
        # writer, always: recompute unconditionally through the single owner.
        apply_tam_triangulation(tam)
        result["tam"] = tam

    # cycle22 + C1/D20: SAM's calculation/serviceability_waterfall are free-text
    # narratives the LLM frequently hallucinates incoherent with its OWN sam.mid
    # (wrong TAM, or a self-inconsistent percentage/dollar pair). Regenerate BOTH
    # strings from the canonical tam_mid + sam.mid so the narrative can never
    # disagree with the headline number. This is only a FIRST pass — the true last
    # word is _enforce_sizing_ordering, which re-syncs again after any clamp moves
    # sam.mid (a clamp here would otherwise leave these strings stale again).
    result["sam"] = _sync_sam_narrative(result.get("sam") or {}, tam_mid)

    # Iter 43-cycle21 (issue 2): coerce growth_cagr_pct to a number even if LLM
    # returned a range string like "18-28" or "20%" — render layer expects numeric.
    cagr = result.get("growth_cagr_pct")
    if isinstance(cagr, str):
        import re as _re
        nums = [float(x) for x in _re.findall(r"\d+(?:\.\d+)?", cagr)]
        if len(nums) >= 2:
            result["growth_cagr_pct"] = round(sum(nums) / len(nums), 1)
        elif len(nums) == 1:
            result["growth_cagr_pct"] = nums[0]
        else:
            result["growth_cagr_pct"] = None

    # Iter 36/40: attach macro + curated vertical anchors (non-fatal if they fail)
    try:
        from macro_anchors import fetch_anchors
        # Pick series relevant to the business model
        biz = (profile.get("business_model") or "").lower()
        cat = (profile.get("category") or "").lower()
        series_keys = ["us_gdp_nominal", "us_real_gdp_growth"]
        if "dtc" in biz or "consumer" in biz or "ecommerce" in biz.replace("-", ""):
            series_keys.append("ecommerce_sales_quarterly")
        if "b2b" in biz or "saas" in biz:
            series_keys.append("services_ppi")
        # International or global ventures get the world-growth anchor
        if "global" in cat or "international" in cat:
            series_keys.append("world_real_gdp_growth")
        anchors = fetch_anchors(series_keys, business_model=biz, category=cat)
        if anchors.get("series") or anchors.get("vertical_anchors"):
            result["macro_anchors"] = anchors
    except Exception as e:
        log.warning(f"[market_sizing] macro anchors failed (non-fatal): {e}")

    # cycle33 (Manus-benchmark fix): enforce SOM ≤ SAM ≤ TAM. The 3 layers are
    # independent LLM calls, so SAM could exceed TAM (caught live by the validation
    # gate: "SAM $3.0B > TAM $950M"). Clamp deterministically; record the fix.
    result = _enforce_sizing_ordering(result)

    return result


def _acv_anchor(optimal, business_model) -> str:
    """R4 rank 21: build the ACV anchor for the bottom-up TAM method, with an explicit
    annual period. A recurring model's monthly price must be ×12'd — feeding the raw
    monthly figure as 'ACV' under-sized a $14/mo SaaS by ~12x. Returns a labelled
    string the sizing prompt injects."""
    if not optimal:
        return "unknown"
    bm = str(business_model or "").lower()
    recurring = ("/mo" in bm or any(k in bm for k in
                 ("subscription", "saas", "member", "recurring", "per month", "monthly")))
    if recurring:
        acv = float(optimal) * 12
        return (f"${acv:,.0f}/yr ACV (the ${optimal}/mo price ANNUALIZED ×12 — use THIS "
                f"as the ACV in the bottom-up method, NEVER the monthly figure)")
    return f"${optimal} per unit/contract (one-time or per-purchase ACV)"


def _sync_sam_narrative(sam: dict, tam_mid) -> dict:
    """C1/D20: rewrite sam['calculation'] and sam['serviceability_waterfall'] from
    the canonical tam_mid + sam['mid'] so neither string can ever disagree with the
    headline SAM number (or with each other — a real R4 finding: one venture's
    waterfall showed a percentage and a dollar figure that didn't even agree with
    EACH OTHER, let alone with sam.mid). No-op when either input is unusable.
    Returns a NEW dict; does not mutate the input."""
    sam = dict(sam or {})
    sam_mid = sam.get("mid")
    if not tam_mid or not sam_mid:
        return sam
    try:
        tam_v, sam_v = float(tam_mid), float(sam_mid)
    except (TypeError, ValueError):
        return sam
    if not tam_v:
        return sam
    pct = sam_v / tam_v * 100.0
    # R4 rank 15: the ONE authoritative serviceable slice is sam.mid/tam.mid. The LLM's
    # key_assumption prose often states a different % (174ae091: SAM is 90% of TAM but
    # the assumption said "15%") and was rendered nowhere. Pin the computed slice so the
    # template renders THAT as authoritative, with key_assumption as supporting rationale.
    sam["serviceable_slice_pct"] = round(pct, 1)
    sam["calculation"] = (
        f"TAM {format_currency(tam_v)} mid × {pct:.1f}% serviceable slice "
        f"= {format_currency(sam_v)} SAM"
    )
    sam["serviceability_waterfall"] = (
        f"TAM {format_currency(tam_v)} → serviceable slice "
        f"~{pct:.1f}% (geo + ICP + channel) → SAM {format_currency(sam_v)}"
    )
    return sam


def _enforce_sizing_ordering(result: dict) -> dict:
    """Clamp SAM ≤ TAM and SOM ≤ SAM on the headline mids (and scale low/high to
    match). Independent LLM layers can violate the funnel; this guarantees it.
    Records any correction under result['_ordering_corrections']."""
    def _mid(block):
        try:
            v = (result.get(block) or {}).get("mid")
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        except (TypeError, ValueError):
            return None

    tam, sam, som = _mid("tam"), _mid("sam"), _mid("som")
    corrections = []

    def _clamp(block: str, value: float, ceiling: float, ceiling_name: str) -> None:
        ratio = ceiling / value if value else 0
        b = dict(result.get(block) or {})
        b["mid"] = round(ceiling * 0.9)  # sit just under the ceiling, not exactly on it
        for edge in ("low", "high"):
            if isinstance(b.get(edge), (int, float)) and not isinstance(b.get(edge), bool):
                b[edge] = round(float(b[edge]) * ratio)
        # cycle36 (audit): the original `calculation` described the pre-clamp value, so the
        # math line contradicted the displayed mid. Disclose the rewrite on the block itself
        # so the report never shows a number that silently disagrees with its own formula.
        b["clamp_note"] = (f"Adjusted to 90% of {ceiling_name} (${ceiling:,.0f}) to keep the "
                           f"funnel consistent — the model's raw {block.upper()} estimate "
                           f"(${value:,.0f}) exceeded {ceiling_name}.")
        result[block] = b
        corrections.append(
            f"{block.upper()} {value:,.0f} exceeded {ceiling_name} {ceiling:,.0f} "
            f"→ clamped to {b['mid']:,.0f} (90% of {ceiling_name})")

    if sam is not None and tam is not None and sam > tam:
        _clamp("sam", sam, tam, "TAM")
        sam = float(result["sam"]["mid"])
    if som is not None and sam is not None and som > sam:
        _clamp("som", som, sam, "SAM")

    # R4 rank 17: the mid clamp scales high by ratio=ceiling/value but only the mid gets
    # the 0.9 factor, so a SAM band wider than TAM's yields SAM.high > TAM.high despite
    # ordered mids (3/16 corpus: 174ae091 SAM.high 1,625M > TAM.high 1,402M). Cap every
    # EDGE down the funnel, not just the mid — process SAM<=TAM before SOM<=SAM.
    def _num_edge(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    for child, parent, pname in (("sam", "tam", "TAM"), ("som", "sam", "SAM")):
        cb, pb = result.get(child) or {}, result.get(parent) or {}
        if not (cb and pb):
            continue
        changed = dict(cb)
        for edge in ("low", "mid", "high"):
            cv, pv = _num_edge(changed.get(edge)), _num_edge(pb.get(edge))
            if cv is not None and pv is not None and cv > pv:
                changed[edge] = round(pv)
                corrections.append(f"{child.upper()}.{edge} {cv:,.0f} exceeded "
                                   f"{pname}.{edge} {pv:,.0f} → capped to {pv:,.0f}")
        result[child] = changed

    if corrections:
        log.warning("[market_sizing] ordering corrections: %s", corrections)
        result["_ordering_corrections"] = corrections
        # Disclose to the reader (weakest_assumptions renders in the report).
        wa = list(result.get("weakest_assumptions") or [])
        for c in corrections:
            wa.append("Funnel correction: " + c)
        result["weakest_assumptions"] = wa

    # C1/D20: this is the TRUE end of the sizing chain (matches the G2/D04 fix's own
    # "re-enforce at the end" philosophy) — re-sync the SAM narrative strings here,
    # AFTER any clamp above may have moved sam.mid, so they can never go stale again
    # regardless of which upstream step (raw LLM incoherence, triangulation, or this
    # very clamp) last touched the number.
    if result.get("sam"):
        result["sam"] = _sync_sam_narrative(result["sam"], tam)
    return result


# G5-shallow / D11: geography-aware validation-source recommendations. A Lisbon cafe
# was told to validate Portuguese household data against US Census ACS + BLS CEX —
# US-only sources (wave2.75 D11 warn, 94008e7c). Mirrors gates.py NON_US_MARKERS.
# The deep G5 (real Eurostat/INE data grounding + non-USD framing) stays deferred;
# this only makes the operator ADVICE honest for the venture's geography.
NON_US_MARKERS = (
    "portugal", "lisbon", "canada", "mexico", "brazil", "united kingdom", " uk", "london",
    "germany", "berlin", "france", "paris", "spain", "madrid", "italy", "japan", "tokyo",
    "china", "india", "australia", "singapore", "netherlands", "europe",
)


def is_non_us_geography(text: str) -> bool:
    """True when the free-text location/geography names a non-US market."""
    blob = (text or "").lower()
    return any(m in blob for m in NON_US_MARKERS)


def validation_sources_for(location: str) -> list[str]:
    """The 3 sources a hyperlocal operator should validate the trade-area inputs
    against — geography-aware. US (or unknown) keeps Census ACS + BLS CEX; a non-US
    venture is pointed at its national statistics office instead of US-only sources
    it cannot use. Do NOT use to claim data grounding — these are validation ADVICE
    strings; actual grounding status is disclosed separately per input."""
    if is_non_us_geography(location):
        where = (location or "").strip() or "the venture's market"
        return [
            f"national statistics office household data for {where} (e.g. Eurostat/INE in the EU)",
            f"national household expenditure survey for {where} (category spend/household)",
            "local single-unit revenue benchmarks (SOM anchor)",
        ]
    return [
        "US Census ACS (trade-area households)",
        "BLS Consumer Expenditure Survey (category spend/household)",
        "local single-unit revenue benchmarks (SOM anchor)",
    ]


def format_currency(n) -> str:
    """Format a number as $1.2B / $450M / $25K — for display."""
    if n is None:
        return "?"
    try:
        n = float(n)
    except (ValueError, TypeError):
        return str(n)
    # cycle36: show one decimal so $1.5M renders "$1.5M", not a misleading "$2M"
    # (the TAM card was rounding 1.5M → 2M while the math line said $100/hh/yr × 15k).
    if n >= 1_000_000_000:
        return f"${n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"${('%.1f' % (n/1_000_000)).rstrip('0').rstrip('.')}M"
    if n >= 1_000:
        return f"${('%.1f' % (n/1_000)).rstrip('0').rstrip('.')}K"
    # Sub-$1000: keep cents when the value isn't whole, so a $7.50 median WTP and an $8.00 high
    # don't both render "$8" (a per-drink band must show the real spread). cycle38.
    return f"${n:,.0f}" if float(n).is_integer() else f"${n:,.2f}"
