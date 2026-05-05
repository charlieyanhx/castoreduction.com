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

Each simulated buyer answers these 4 questions. Return JSON. CRITICAL: the most-actionable
fields (optimal_price_point, recommended_tiers, acceptable_range) come FIRST so they
survive any output truncation. The 4 medians come last.

**UNIT CONSISTENCY (this is the most-broken thing across runs):**
- ALL prices in this output MUST be in the same unit: dollars per seat per month
  (or, for DTC, dollars per unit). NEVER mix monthly and annual.
- recommended_tiers[*].price MUST be within ±50% of optimal_price_point. If a
  tier feels like it should be 10× larger, you're probably accidentally doing
  per-account or per-year. STOP and convert back to per-seat/per-month.
- A "Starter" tier is typically 60-80% of optimal_price_point.
- A "Pro" tier is typically right at optimal_price_point.
- An "Enterprise" tier is typically 130-200% of optimal_price_point.

{{
  "optimal_price_point": <number — dollars per seat per month>,
  "acceptable_range": [low_per_seat_per_month, high_per_seat_per_month],
  "point_of_marginal_cheapness": <number — same unit>,
  "recommended_tiers": [
    {{"name": "Starter",    "price": <number — same unit, ~60-80% of optimal>, "for_whom": "..."}},
    {{"name": "Pro",        "price": <number — same unit, ~optimal>,           "for_whom": "..."}},
    {{"name": "Enterprise", "price": <number — same unit, ~130-200% of optimal>,"for_whom": "..."}}
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


def simulate_van_westendorp(
    segment_summary: str,
    product_summary: str,
    top_features: list[str],
    competitor_prices: list[float] | None = None,
    n_buyers: int = 40,
) -> dict:
    """Simulate a Van Westendorp PSM. Returns acceptable range + optimal price + tiered recommendations."""

    comp_str = "unknown" if not competitor_prices else ", ".join(f"${p}" for p in competitor_prices[:10])
    features_str = ", ".join(top_features[:5]) if top_features else "core features"

    result = call_json(
        system="You simulate pricing research panels. Return only JSON with realistic numeric prices.",
        user=VW_PSM_PROMPT.format(
            n_buyers=n_buyers,
            segment_summary=segment_summary[:1000],
            product_summary=product_summary[:500],
            top_features=features_str,
            comp_prices=comp_str,
        ),
        max_tokens=3500,  # iter 41: bumped from 2000 — was truncating after `too_cheap`, losing optimal_price_point
    )

    if "_parse_error" in result:
        return {"error": "LLM returned malformed JSON", "_raw": result.get("_raw", "")[:400]}

    # Iter 42 (issue 10): sanity-check tier prices against optimal_price_point.
    # The LLM regularly mixes monthly/annual units, producing nonsensical
    # "Starter $3600/mo per seat" when optimal is $30. Auto-correct + flag.
    opp = result.get("optimal_price_point")
    tiers = result.get("recommended_tiers") or []
    if opp and tiers:
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

    return result


def build_benchmark_table(
    our_tiers: list[dict],
    competitor_pricing: dict | None,
    pricing_unit: str = "account",
    competitor_brands: list[dict] | None = None,
) -> dict:
    """
    Iter 35 step 3 (user feedback #3b): build a per-unit competitor benchmark table.

    Normalizes both our tiers and competitor medians to the same pricing unit
    (e.g. "$/month per seat") and computes each competitor as a multiple of
    our Pro/main tier, so the report can say "Competitor X charges 3.0× our Pro price".

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
        return f"${s}/month per {pricing_unit}"

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
        "our_tiers": our_tiers_labeled,
        "our_pro_price": our_pro_price,
        "our_pro_price_label": _label(our_pro_price),
        "rows": rows,
        "category_median": category_median,
        "category_median_label": _label(category_median) if category_median else None,
        "vs_category_median_pct": vs_category_pct,
        "n_competitors_with_prices": len(rows),
    }


def compute_break_even(monthly_price: float, monthly_fixed_cost: float = 5000, variable_cost_per_customer: float = 2) -> dict:
    """Simple break-even math. Default assumptions are placeholder — operator should override."""
    margin = monthly_price - variable_cost_per_customer
    if margin <= 0:
        return {"error": "price below variable cost", "break_even_customers": None}
    return {
        "break_even_customers": round(monthly_fixed_cost / margin),
        "monthly_fixed_cost_assumed": monthly_fixed_cost,
        "variable_cost_per_customer_assumed": variable_cost_per_customer,
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
