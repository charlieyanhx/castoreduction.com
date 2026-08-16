"""
Step 9 of spec: Pricing & feature analysis.

Simulates two classic market research methods via LLM:
  - Max-Diff (Best-Worst Scaling): rank features by importance
  - Van Westendorp Price Sensitivity Meter: find acceptable price range + optimal price

The LLM "simulates" a panel of buyers answering these questions based on the
decoded taste profile. Not a replacement for real surveys, but a decent
directional signal that's free and fast.
"""
from __future__ import annotations
import json
import math
import sys
import statistics

from llm import call_json
from logger import get

log = get("pricing")


MAX_DIFF_PROMPT = """You are simulating a Max-Diff (Best-Worst Scaling) exercise with a panel of {n_buyers} buyers from this target segment.

SEGMENT: {segment_summary}

PRODUCT CATEGORY: {category}

Features to rank (buyers repeatedly pick MOST and LEAST important from sets of 4):
{features}

Return JSON:
{{
  "panel_size": {n_buyers},
  "ranked_features": [
    {{"feature": "feature name", "importance_score": 0-100, "rank": 1}},
    ...
  ],
  "top_3_must_haves": ["feature names that cluster at top"],
  "deprioritize": ["feature names that cluster at bottom"],
  "notes": "any caveats about simulated rankings"
}}

The importance_score should total ~100 across all features (like real Max-Diff). Rank from highest importance to lowest."""


VW_PSM_PROMPT = """You are simulating a Van Westendorp Price Sensitivity Meter with {n_buyers} simulated buyers from this segment.

SEGMENT: {segment_summary}

PRODUCT: {product_summary}

TOP FEATURES: {top_features}

COMPETITOR PRICES (for reference): {comp_prices}

PRICING UNIT: every price in this output MUST be expressed {price_unit}. {recurring_note}

Each simulated buyer answers these 4 questions. Return JSON. CRITICAL: the most-actionable
fields (optimal_price_point, recommended_tiers, acceptable_range) come FIRST so they
survive any output truncation. The 4 medians come last.

**UNIT CONSISTENCY (this is the most-broken thing across runs):**
- ALL prices in this output MUST be {price_unit}. NEVER mix units.
- recommended_tiers[*].price MUST be within ±60% of optimal_price_point. If a tier feels
  like it should be 10× larger, you have the wrong unit — STOP and convert back.
- {tier_guidance}

{{
  "optimal_price_point": <number — {price_unit}>,
  "acceptable_range": [low, high],
  "point_of_marginal_cheapness": <number — same unit>,
  "recommended_tiers": [
    {{"name": "{tier1_name}", "price": <number — same unit, ~70% of optimal>, "for_whom": "..."}},
    {{"name": "{tier2_name}", "price": <number — same unit, ~optimal>,        "for_whom": "..."}},
    {{"name": "{tier3_name}", "price": <number — same unit, ~150-250% of optimal>,"for_whom": "..."}}
  ],
  "currency": "USD",
  "panel_size": {n_buyers},
  "notes": "caveats",
  "too_cheap":          {{"median": <number — same unit>, "q1": <number>, "q3": <number>}},
  "bargain":            {{"median": <number — same unit>, "q1": <number>, "q3": <number>}},
  "expensive_but_ok":   {{"median": <number — same unit>, "q1": <number>, "q3": <number>}},
  "too_expensive":      {{"median": <number — same unit>, "q1": <number>, "q3": <number>}}
}}

Base prices on typical purchases for this type of buyer in this category. Be realistic — don't anchor to $99 or $9.99 by default. ALL the top fields (optimal_price_point, recommended_tiers, acceptable_range) are REQUIRED — the report breaks without them."""


def simulate_max_diff(features: list[str], segment_summary: str, category: str, n_buyers: int = 30) -> dict:
    """Simulate a Max-Diff feature ranking. features = 10-20 distinct features."""
    if len(features) < 3:
        return {"error": "Need at least 3 features to rank"}

    features_blob = "\n".join(f"  - {f}" for f in features[:20])

    result = call_json(
        system="You simulate market research panels. Return only JSON.",
        user=MAX_DIFF_PROMPT.format(
            n_buyers=n_buyers,
            segment_summary=segment_summary[:1000],
            category=category,
            features=features_blob,
        ),
        max_tokens=4000,  # iter 41: bumped 2500→4000 — was truncating to 2 features (out of 11)
    )

    if "_parse_error" in result:
        return {"error": "LLM returned malformed JSON", "_raw": result.get("_raw", "")[:400]}
    return result


def _num(v):
    """A finite float, or None. Prices arrive as strings, nulls and "ask us"."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def annotate_tiers_against_range(psm: dict) -> dict:
    """Mark each recommended tier against the instrument's OWN acceptable range.

    MEASURED on runs 12-15, identical every time: the PSM reports an acceptable range of
    $4.25-$6.75, then recommends Value $3.85 (below its own floor, which is also the point
    of marginal cheapness) and Premium $9.50 (above the ceiling, above the too-expensive
    MEDIAN of $8.25, and above that band's q3 of $9.00 — so appreciably more than half the
    simulated panel would call it too expensive). Both shipped with flat "PSM PRICING
    OUTPUT" citations, while the kill criterion elsewhere in the same report treated the
    $4.25 floor as meaningful.

    ANNOTATE, NEVER CLAMP. An out-of-range tier can be sound strategy — a loss-leader that
    pulls commuter traffic, a halo SKU that anchors the menu and rarely sells. Dragging
    $9.50 down to the ceiling would destroy a real recommendation and hide that the
    instrument disagrees with it. What cannot be defended is showing it unqualified, so a
    reader cannot tell a deliberate halo SKU from a number the model drifted into.

    Degrades quietly: no range, a malformed range, an inverted range or a non-numeric price
    leaves the tier untouched rather than inventing a verdict.
    """
    if not isinstance(psm, dict):
        return psm
    tiers = psm.get("recommended_tiers")
    rng = psm.get("acceptable_range")
    if not isinstance(tiers, list) or not isinstance(rng, (list, tuple)) or len(rng) != 2:
        return psm
    lo, hi = _num(rng[0]), _num(rng[1])
    # An inverted range would mark every tier out-of-range, turning a broken instrument
    # into a page of alarms about the tiers, which are not the thing at fault.
    if lo is None or hi is None or lo > hi:
        return psm

    too_exp = (psm.get("too_expensive") or {}) if isinstance(psm.get("too_expensive"), dict) else {}
    too_exp_median = _num(too_exp.get("median"))
    too_cheap = (psm.get("too_cheap") or {}) if isinstance(psm.get("too_cheap"), dict) else {}
    too_cheap_median = _num(too_cheap.get("median"))

    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        p = _num(tier.get("price"))
        if p is None:
            continue
        if lo <= p <= hi:
            tier["range_status"] = "within"
            continue
        if p < lo:
            tier["range_status"] = "below_floor"
            note = (f"${p:,.2f} sits BELOW the panel's acceptable floor of ${lo:,.2f} "
                    f"(its point of marginal cheapness)")
            if too_cheap_median is not None and p <= too_cheap_median:
                note += (f" and at or under the too-cheap median of ${too_cheap_median:,.2f}, "
                         "where buyers start doubting quality")
            note += (" — treat as a deliberate loss-leader, not a core price point, and "
                     "expect it to dilute blended margin.")
        else:
            tier["range_status"] = "above_ceiling"
            note = (f"${p:,.2f} sits ABOVE the panel's acceptable ceiling of ${hi:,.2f}")
            if too_exp_median is not None and p >= too_exp_median:
                note += (f", and above the too-expensive median of ${too_exp_median:,.2f} — "
                         "more than half the simulated panel would reject it outright")
            note += (" — treat as a low-volume halo SKU, not a core price point, and do "
                     "not size volume from it.")
        tier["range_note"] = note
    return psm


def simulate_van_westendorp(
    segment_summary: str,
    product_summary: str,
    top_features: list[str],
    competitor_prices: list[float] | None = None,
    n_buyers: int = 40,
    unit: str = "seat",
    recurring: bool = True,
) -> dict:
    """Simulate a Van Westendorp PSM. Returns acceptable range + optimal price + tiered recommendations.

    cycle37: model-aware. recurring=True → monthly subscription per `unit` (SaaS/seat). recurring=False →
    a one-time per-`unit` retail price (e.g. per drink), with menu-style tiers and no monthly/annual
    truncation correction (which only makes sense for recurring pricing).
    """

    comp_str = "unknown" if not competitor_prices else ", ".join(f"${p}" for p in competitor_prices[:10])
    features_str = ", ".join(top_features[:5]) if top_features else "core features"

    if recurring:
        price_unit = f"in dollars per {unit} per month"
        recurring_note = "This is a recurring monthly subscription price."
        tier_guidance = ("A value tier is ~70% of optimal; the main tier sits at optimal; "
                         "a premium tier is ~150-200% of optimal.")
        tier_names = ("Starter", "Pro", "Enterprise")
    else:
        price_unit = f"as a one-time price per {unit} (a single purchase, NOT a monthly subscription)"
        recurring_note = (f"This is a per-{unit} retail transaction price. Do NOT invent monthly "
                          f"subscription tiers or 'per account' pricing — price a single {unit}.")
        tier_guidance = (f"Tiers are menu price points for one {unit}: a value option ~70% of optimal, "
                         f"a standard option at optimal, and a premium option ~150-250% of optimal "
                         f"(e.g. a rare/limited variant).")
        tier_names = ("Value", "Standard", "Premium")

    result = call_json(
        system="You simulate pricing research panels. Return only JSON with realistic numeric prices.",
        user=VW_PSM_PROMPT.format(
            n_buyers=n_buyers,
            segment_summary=segment_summary[:1000],
            product_summary=product_summary[:500],
            top_features=features_str,
            comp_prices=comp_str,
            price_unit=price_unit,
            recurring_note=recurring_note,
            tier_guidance=tier_guidance,
            tier1_name=tier_names[0], tier2_name=tier_names[1], tier3_name=tier_names[2],
        ),
        max_tokens=3500,  # iter 41: bumped from 2000 — was truncating after `too_cheap`, losing optimal_price_point
    )

    if "_parse_error" in result:
        return {"error": "LLM returned malformed JSON", "_raw": result.get("_raw", "")[:400]}

    # Iter 42 (issue 10): sanity-check tier prices against optimal_price_point.
    # The LLM regularly mixes monthly/annual units, producing nonsensical
    # "Starter $3600/mo per seat" when optimal is $30. Auto-correct + flag.
    # cycle37: the ÷12 "was annual" correction only applies to recurring pricing — skip it for
    # one-time per-unit retail (a $12 premium drink is not "$1/mo annualized").
    opp = result.get("optimal_price_point")
    tiers = result.get("recommended_tiers") or []
    if opp and tiers and recurring:
        try:
            opp_v = float(opp)
        except (TypeError, ValueError):
            opp_v = None
        if opp_v and opp_v > 0:
            cleaned = []
            warnings = []
            for tier in tiers:
                if not isinstance(tier, dict):
                    continue
                price = tier.get("price")
                if price is None or price == "":
                    warnings.append(f"Tier '{tier.get('name', '?')}' had no price — dropped.")
                    continue
                try:
                    p_v = float(price)
                except (TypeError, ValueError):
                    warnings.append(f"Tier '{tier.get('name', '?')}' price '{price}' wasn't numeric — dropped.")
                    continue
                # If tier price is >5× optimal, the LLM probably did annual (×12)
                # or per-account. Try ÷12 first; if that lands in range, use it.
                if p_v > opp_v * 5:
                    annualized = p_v / 12
                    if 0.3 * opp_v <= annualized <= 3 * opp_v:
                        warnings.append(f"Tier '{tier.get('name', '?')}': ${p_v} → corrected to ${annualized:.2f} (was annual, converted to monthly)")
                        tier["price"] = round(annualized, 2)
                        tier["_correction_note"] = f"original ${p_v} interpreted as annual"
                    else:
                        warnings.append(f"Tier '{tier.get('name', '?')}': ${p_v} >> optimal ${opp_v} — likely unit error, dropped.")
                        continue
                # If tier is way under (e.g. 0.1× optimal), also suspicious
                elif p_v < opp_v * 0.1:
                    warnings.append(f"Tier '{tier.get('name', '?')}': ${p_v} far below optimal ${opp_v} — dropped.")
                    continue
                cleaned.append(tier)
            result["recommended_tiers"] = cleaned
            if warnings:
                existing = result.get("notes") or ""
                result["notes"] = (existing + "  Sanity check: " + "; ".join(warnings))[:600]
                log.warning("[pricing] tier sanity-check applied: %s", warnings)

    # LAST, deliberately: the sanity check above rewrites prices (the ÷12 annual
    # correction) and drops tiers, so annotating before it would leave a tier labelled
    # against a price it no longer carries.
    return annotate_tiers_against_range(result)


try:  # provenance: record that this function produced a report key
    from skills.registry import records_production as _records_production
except Exception:  # pragma: no cover — never let provenance break an import
    def _records_production(_k):
        return lambda f: f


@_records_production("pricing_benchmark")
def build_benchmark_table(
    our_tiers: list[dict],
    competitor_pricing: dict | None,
    pricing_unit: str = "account",
    competitor_brands: list[dict] | None = None,
    recurring: bool = True,
) -> dict:
    """
    Iter 35 step 3 (user feedback #3b): build a per-unit competitor benchmark table.

    Normalizes both our tiers and competitor medians to the same pricing unit
    (e.g. "$/month per seat") and computes each competitor as a multiple of
    our Pro/main tier, so the report can say "Competitor X charges 3.0× our Pro price".

    D06 (wave0 gate finding): `recurring=False` prices the unit itself ("$6 per drink") —
    a per-unit venture must never render subscription framing ("$119/month per unit").
    The labels are baked into the result at pipeline time, so the model-awareness has to
    happen here, not in the template.

    competitor_pricing shape (from competitor_pricing.gather_competitor_prices):
        {per_domain: [{domain, median, min, max}, ...], category_median, ...}

    Returns:
        {
          pricing_unit: "account",
          our_tiers: [{name, price, price_label}],
          our_pro_price: X,
          rows: [{brand, domain, price, price_label, multiple_of_pro, delta_pct}],
          category_median: X,
          vs_category_median_pct: X,
        }
    """
    if not our_tiers:
        return {"error": "no tiers provided"}

    # Pick the "main" tier (Pro-equivalent): middle tier, or 2nd of N.
    tier_sorted = sorted(our_tiers, key=lambda t: float(t.get("price", 0) or 0))
    pro_tier = tier_sorted[len(tier_sorted) // 2] if tier_sorted else None
    our_pro_price = float(pro_tier.get("price", 0) or 0) if pro_tier else 0

    def _label(p):
        if not p:
            return "—"
        # Format as integer when whole, otherwise 2dp — "$29" not "$29.0"
        p_num = float(p)
        s = f"{p_num:.0f}" if p_num == int(p_num) else f"{p_num:.2f}"
        return f"${s}/month per {pricing_unit}" if recurring else f"${s} per {pricing_unit}"

    our_tiers_labeled = [
        {
            "name": t.get("name"),
            "price": float(t.get("price", 0) or 0),
            "price_label": _label(float(t.get("price", 0) or 0)),
            "for_whom": t.get("for_whom", ""),
        }
        for t in our_tiers
    ]

    # Build per-competitor rows
    per_domain = (competitor_pricing or {}).get("per_domain") or []
    brand_map = {}
    for c in (competitor_brands or []):
        if c.get("domain"):
            brand_map[c["domain"].lower()] = c.get("brand")

    rows = []
    for d in per_domain:
        med = d.get("median")
        if not med:
            continue
        domain = (d.get("domain") or "").lower()
        brand = brand_map.get(domain) or domain.split(".")[0].title()
        multiple = round(med / our_pro_price, 2) if our_pro_price > 0 else None
        delta_pct = round((med - our_pro_price) / our_pro_price * 100, 1) if our_pro_price > 0 else None
        rows.append({
            "brand": brand,
            "domain": domain,
            "price": med,
            "price_label": _label(med),
            "multiple_of_pro": multiple,
            "delta_pct": delta_pct,
            "cheaper_or_pricier": "pricier" if (delta_pct or 0) > 5 else ("cheaper" if (delta_pct or 0) < -5 else "parity"),
        })

    # Sort ascending by price for a readable table
    rows.sort(key=lambda r: r["price"])

    category_median = (competitor_pricing or {}).get("category_median")
    vs_category_pct = None
    if category_median and our_pro_price > 0:
        vs_category_pct = round((our_pro_price - category_median) / category_median * 100, 1)

    return {
        "pricing_unit": pricing_unit,
        # Carry `recurring` OUT, not just in: the labels below already respect it, but the
        # report template renders its own price strings and had no way to ask — so it
        # hardcoded "/mo per <unit>" and printed "$185.0/mo per booking" on a marketplace
        # (R4 panel, R5). Knowing the answer and not exposing it is what made the leak
        # survive the C3/D06-extend fix.
        "recurring": bool(recurring),
        "our_tiers": our_tiers_labeled,
        "our_pro_price": our_pro_price,
        "our_pro_price_label": _label(our_pro_price),
        "rows": rows,
        "category_median": category_median,
        "category_median_label": _label(category_median) if category_median else None,
        "vs_category_median_pct": vs_category_pct,
        "n_competitors_with_prices": len(rows),
    }


def estimate_cost_structure(category: str, monthly_price: float | None = None,
                            market_scale: str | None = None) -> dict:
    """Per-category estimate of the MONTHLY fixed cost and per-customer VARIABLE cost.

    cycle36 (audit): the old break-even used a universal hardcoded $5000/mo +
    $2/customer for EVERY venture. R4 rank 2 found the replacement's own blind spot:
    the prompt asked for "a SINGLE early-stage location" UNCONDITIONALLY, so a
    global-digital superconducting-tape company and a national ecommerce brand were
    costed as a storefront — rent + staff + utilities as their entire cost side.

    The COST MODEL now follows the venture's scale: digital/global ventures are asked
    for early-stage company overhead (team, infrastructure, tooling); physical ones
    keep the single-site model. `basis` names which model produced the number, and it
    flows into economics so downstream withholding logic and the reader both know
    what the fixed cost actually covers.

    Returns {monthly_fixed_cost, variable_cost_per_customer, sourced, source, basis}."""
    _digital = any(t in (market_scale or "").lower() for t in ("digital", "global"))
    if _digital:
        system = ("Estimate, for an EARLY-STAGE COMPANY (not a physical location) in the "
                  "given business category, the typical MONTHLY fixed operating cost — "
                  "core team payroll, infrastructure/hosting, and tooling, USD — and the "
                  "VARIABLE cost per customer/transaction (USD). Reply ONLY JSON: "
                  "{\"monthly_fixed_cost\": <number>, \"variable_cost_per_customer\": <number>}.")
        basis = "early-stage company overhead (team + infrastructure + tooling)"
    else:
        system = ("Estimate, for a SINGLE early-stage location/unit in the given "
                  "business category, the typical MONTHLY fixed operating cost (rent + "
                  "staff + utilities, USD) and the VARIABLE cost per customer/transaction "
                  "(USD). Reply ONLY JSON: {\"monthly_fixed_cost\": <number>, "
                  "\"variable_cost_per_customer\": <number>}.")
        basis = "single-site rent + staff + utilities"
    if category:
        try:
            from llm import call_json
            raw = call_json(
                system=system,
                user=f"Category: {category}" + (f"\nUnit price: ${monthly_price}" if monthly_price else ""),
                max_tokens=80,
            ) or {}
            f, v = raw.get("monthly_fixed_cost"), raw.get("variable_cost_per_customer")
            if (isinstance(f, (int, float)) and not isinstance(f, bool) and f > 0
                    and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0):
                return {"monthly_fixed_cost": float(f), "variable_cost_per_customer": float(v),
                        "sourced": False, "basis": basis,
                        "source": "LLM estimate (UNSOURCED — operator should validate)"}
        except Exception:
            pass
    return {"monthly_fixed_cost": 5000.0, "variable_cost_per_customer": 2.0, "sourced": False,
            "basis": basis,
            "source": "generic placeholder — operator should set real cost structure"}


def compute_break_even(monthly_price: float, monthly_fixed_cost: float = 5000,
                       variable_cost_per_customer: float = 2,
                       cost_source: str = "generic placeholder — operator should set real cost structure") -> dict:
    """Simple break-even math. Costs SHOULD be category-estimated (see estimate_cost_structure)
    and are echoed in the result so the report can disclose them — they are never silently
    hidden. Defaults remain a labeled placeholder for callers that omit them."""
    margin = monthly_price - variable_cost_per_customer
    if margin <= 0:
        return {"error": "price below variable cost", "break_even_customers": None}
    return {
        # CEIL, not round. 10.4 customers is 11 customers, or the venture is $30/mo short
        # and told it has broken even. R4 rank 24 fixed exactly this in
        # retail_unit_economics (pinned by test_breakeven_ceil.py) and the subscription
        # side kept rounding down for another eleven months.
        "break_even_customers": math.ceil(monthly_fixed_cost / margin),
        "monthly_fixed_cost_assumed": monthly_fixed_cost,
        "variable_cost_per_customer_assumed": variable_cost_per_customer,
        "cost_source": cost_source,
        "margin_per_customer": round(margin, 2),
    }


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    # Demo
    r = simulate_max_diff(
        features=["fast shipping", "eco packaging", "bundle discounts", "loyalty program", "free trial"],
        segment_summary="young urban professionals who value sustainability",
        category="meal prep containers",
    )
    print(json.dumps(r, indent=2))
