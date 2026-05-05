"""
Unit economics — CLV, CAC target, and EVC (Economic Value to Customer).

Spec step 10 requires CLV + CAC_ratio (CLV:CAC ≥ 3:1). User feedback (iter 35)
requires EVC: reference value (next-best alternative cost) + differentiation
value (what we add) = total EVC, then price positioning vs that EVC.

All three calculations share inputs (price, churn, contract length), so they
live in one module. Pure math where possible; one LLM call to estimate the
segment-specific churn / contract / expansion numbers from competitor signals.
"""
from __future__ import annotations
import json

from llm import call_json
from logger import get

log = get("economics")


CLV_ESTIMATE_PROMPT = """Estimate segment-specific unit economics for this B2B SaaS target.

SEGMENT: {segment}
PRODUCT: {product}
OPTIMAL PRICE (from Van Westendorp PSM): ${price}/month per {unit}
COMPETITOR TIER OBSERVED: {comp_summary}

Return JSON (be realistic — base on benchmarks for this segment type, don't hand-wave):
{{
  "avg_contract_months": 12,
  "monthly_churn_pct": 4.5,
  "annual_expansion_rate_pct": 15,
  "typical_cac_usd": 1200,
  "reference_alternative_name": "what the customer would use instead (incumbent tool or manual process)",
  "reference_alternative_annual_cost_usd": 2400,
  "differentiation_value_annual_usd": 3600,
  "differentiation_value_reasoning": "1 sentence — WHY we save them $X/year or earn them $X/year vs reference. Concrete, quantitative.",
  "confidence": "low | medium | high",
  "notes": "any caveats, e.g. 'churn estimate uncertain; varies with contract length'"
}}

Definitions:
- reference_alternative_annual_cost_usd: what the customer PAYS TODAY for the status quo (incumbent tool, consultant, manual process cost) — annual dollars.
- differentiation_value_annual_usd: incremental $/year we create vs that reference — could be cost savings (fewer hours, cheaper stack) OR revenue lift (better conversion, retention). Be honest if it's <= reference — that's a red flag for the founder.
- typical_cac_usd: fully-loaded acquisition cost per paid customer for this segment (content+paid+sales salary allocation).

Be realistic — monthly churn >10% is a red flag, contracts <6mo rare in B2B, expansion >25%/yr is unusually strong."""


def estimate_unit_economics(
    segment_summary: str,
    product_summary: str,
    optimal_price_monthly: float,
    pricing_unit: str = "account",
    competitor_prices: list[float] | None = None,
) -> dict:
    """
    One LLM call to estimate churn, contract length, expansion, CAC, reference
    alternative cost, and differentiation value. Returns structured dict or error.
    """
    comp_summary = (
        f"competitor monthly prices ~ {[f'${p}' for p in competitor_prices[:5]]}"
        if competitor_prices else "competitor pricing unknown"
    )
    result = call_json(
        system="You are a B2B SaaS unit-economics analyst. Return only JSON with realistic numeric estimates.",
        user=CLV_ESTIMATE_PROMPT.format(
            segment=(segment_summary or "")[:800],
            product=(product_summary or "")[:400],
            price=optimal_price_monthly,
            unit=pricing_unit,
            comp_summary=comp_summary,
        ),
        max_tokens=900,
    )
    if "_parse_error" in result:
        return {"error": "unit economics LLM returned malformed JSON", "_raw": result.get("_raw", "")[:300]}
    return result


def compute_clv(
    monthly_price: float,
    monthly_churn_pct: float,
    annual_expansion_pct: float = 0.0,
    avg_contract_months: int | None = None,
) -> dict:
    """
    Standard B2B SaaS CLV:
        CLV = (monthly_price / monthly_churn_rate) × (1 + expansion_rate)

    With a floor of contract_length if churn is near zero (rare edge case).
    Returns full breakdown for the report.
    """
    if monthly_price <= 0:
        return {"error": "price must be positive"}
    churn = (monthly_churn_pct or 0) / 100.0
    expansion = (annual_expansion_pct or 0) / 100.0
    if churn <= 0.001:
        # Near-zero churn → cap at contract length to avoid /0
        months = avg_contract_months or 36
        base_clv = monthly_price * months
        method = "contract-floor (churn too low to be realistic)"
    else:
        months_avg = 1.0 / churn
        base_clv = monthly_price * months_avg
        method = f"1/churn = {months_avg:.1f} months"
    clv_with_expansion = base_clv * (1 + expansion)
    return {
        "clv_usd": round(clv_with_expansion, 2),
        "base_clv_usd": round(base_clv, 2),
        "expansion_uplift_usd": round(clv_with_expansion - base_clv, 2),
        "months_retained": round(1.0 / churn, 1) if churn > 0.001 else None,
        "method": method,
        "inputs": {
            "monthly_price": monthly_price,
            "monthly_churn_pct": monthly_churn_pct,
            "annual_expansion_pct": annual_expansion_pct,
        },
    }


def compute_cac_target(clv_usd: float, target_ratio: float = 3.0) -> dict:
    """
    Spec rule-of-thumb: healthy B2B SaaS has CLV:CAC ≥ 3:1.
    So max payable CAC = CLV / ratio. Returns target + status vs typical_cac.
    """
    if clv_usd <= 0:
        return {"error": "CLV must be positive"}
    max_cac = clv_usd / target_ratio
    return {
        "max_sustainable_cac_usd": round(max_cac, 2),
        "target_ratio": target_ratio,
        "rule": f"CLV:CAC ≥ {target_ratio}:1 (healthy B2B SaaS benchmark)",
    }


def compute_evc(
    our_annual_price_usd: float,
    reference_alternative_annual_cost_usd: float,
    differentiation_value_annual_usd: float,
    differentiation_reasoning: str = "",
) -> dict:
    """
    Economic Value to Customer (EVC) decomposition:
        EVC = reference_value + differentiation_value

    Then verdict on pricing:
        - If our price <= 50% of EVC → under-priced, leaving money on the table
        - 50-80% of EVC → healthy room for customer ROI
        - 80-100% of EVC → pricing-at-value, thin ROI margin
        - >100% of EVC → over-priced, will lose to cheaper alternatives

    All inputs in USD/year. Our price is also annualized (monthly × 12 in caller).
    """
    if reference_alternative_annual_cost_usd < 0 or differentiation_value_annual_usd < 0:
        return {"error": "reference and differentiation values must be non-negative"}
    total_evc = reference_alternative_annual_cost_usd + differentiation_value_annual_usd
    # Iter 41: when LLM returns 0/0 (model played it ultra-conservative, or
    # didn't fill the unit_economics fields), instead of erroring out the
    # entire EVC block, return a degraded-but-visible result so the report
    # still renders the section + flags it explicitly for the operator.
    if total_evc <= 0:
        return {
            "our_annual_price_usd": round(our_annual_price_usd, 2),
            "reference_value_usd": 0,
            "differentiation_value_usd": 0,
            "total_evc_usd": 0,
            "price_as_pct_of_evc": None,
            "customer_annual_roi_usd": None,
            "customer_roi_multiple": None,
            "verdict": "data-thin",
            "verdict_detail": (
                "EVC could not be computed — the unit-economics LLM did not return a "
                "reference-alternative cost or differentiation value. This often happens "
                "for genuinely novel products with no clear incumbent comparator. Operator "
                "should manually estimate: (a) what a customer pays today for the next-best "
                "alternative, and (b) the dollar value of our incremental capability."
            ),
            "differentiation_reasoning": differentiation_reasoning or "",
        }
    price_as_pct_of_evc = our_annual_price_usd / total_evc * 100
    customer_roi_usd = total_evc - our_annual_price_usd
    customer_roi_multiple = total_evc / our_annual_price_usd if our_annual_price_usd > 0 else None

    if price_as_pct_of_evc < 50:
        verdict = "under-priced"
        verdict_detail = "Strong ROI story but likely leaving money on the table. Consider a 20-40% price increase before launch."
    elif price_as_pct_of_evc < 80:
        verdict = "healthy"
        verdict_detail = "Customer captures comfortable ROI; easy sales case."
    elif price_as_pct_of_evc < 100:
        verdict = "priced-at-value"
        verdict_detail = "Thin ROI margin for the customer. Sales will be harder; every cent of differentiation must be defensible."
    else:
        verdict = "over-priced"
        verdict_detail = "Price exceeds total economic value delivered. Customer will rationally choose the reference alternative."

    return {
        "our_annual_price_usd": round(our_annual_price_usd, 2),
        "reference_value_usd": round(reference_alternative_annual_cost_usd, 2),
        "differentiation_value_usd": round(differentiation_value_annual_usd, 2),
        "total_evc_usd": round(total_evc, 2),
        "price_as_pct_of_evc": round(price_as_pct_of_evc, 1),
        "customer_annual_roi_usd": round(customer_roi_usd, 2),
        "customer_roi_multiple": round(customer_roi_multiple, 2) if customer_roi_multiple else None,
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "differentiation_reasoning": differentiation_reasoning,
    }


def sensitivity_analysis(
    optimal_price_monthly: float,
    base_churn_pct: float,
    base_expansion_pct: float,
    reference_value_usd: float,
    differentiation_value_usd: float,
) -> dict:
    """
    Iter 36: fragility map for the unit economics + EVC.

    Runs the CLV and EVC math across:
      - churn scenarios: half, base, double, triple
      - differentiation value scenarios: half, base, double
      - price scenarios: -20%, base, +20%

    The founder sees "if churn doubles, CLV halves to $X and EVC flips to over-priced".
    That's the fragility they need to manage.
    """
    if optimal_price_monthly <= 0:
        return {"error": "price must be positive"}

    # Coerce numeric inputs — LLM estimates sometimes return strings
    def _f(x, default=0.0):
        try:
            return float(x) if x is not None else default
        except (TypeError, ValueError):
            return default

    base_churn_pct = _f(base_churn_pct, 5.0)
    base_expansion_pct = _f(base_expansion_pct, 0.0)
    reference_value_usd = _f(reference_value_usd, 0.0)
    differentiation_value_usd = _f(differentiation_value_usd, 0.0)

    churn_scenarios = {
        "half_churn": max(0.5, base_churn_pct * 0.5),
        "base": base_churn_pct,
        "double_churn": base_churn_pct * 2,
        "triple_churn": base_churn_pct * 3,
    }
    churn_rows = []
    for label, c in churn_scenarios.items():
        clv = compute_clv(optimal_price_monthly, c, base_expansion_pct)
        churn_rows.append({
            "scenario": label,
            "monthly_churn_pct": round(c, 1),
            "clv_usd": clv.get("clv_usd"),
            "months_retained": clv.get("months_retained"),
        })

    # EVC fragility — vary differentiation value
    annual_price = optimal_price_monthly * 12
    diff_scenarios = {
        "half_diff": differentiation_value_usd * 0.5,
        "base": differentiation_value_usd,
        "double_diff": differentiation_value_usd * 2,
    }
    diff_rows = []
    for label, dv in diff_scenarios.items():
        evc = compute_evc(annual_price, reference_value_usd, dv)
        if "error" not in evc:
            diff_rows.append({
                "scenario": label,
                "differentiation_value_usd": round(dv, 0),
                "total_evc_usd": evc["total_evc_usd"],
                "price_as_pct_of_evc": evc["price_as_pct_of_evc"],
                "verdict": evc["verdict"],
                "customer_annual_roi_usd": evc["customer_annual_roi_usd"],
            })

    # Price sensitivity — how does verdict change if we move price ±20%
    price_rows = []
    for pct_change in (-20, -10, 0, 10, 20):
        new_monthly = optimal_price_monthly * (1 + pct_change / 100)
        evc = compute_evc(new_monthly * 12, reference_value_usd, differentiation_value_usd)
        if "error" not in evc:
            price_rows.append({
                "price_change_pct": pct_change,
                "monthly_price_usd": round(new_monthly, 2),
                "verdict": evc["verdict"],
                "customer_annual_roi_usd": evc["customer_annual_roi_usd"],
                "price_as_pct_of_evc": evc["price_as_pct_of_evc"],
            })

    # Identify the first scenario in each axis where verdict flips to over-priced
    break_points = {}
    for row in diff_rows:
        if row["verdict"] == "over-priced" and "diff_break" not in break_points:
            break_points["differentiation_break"] = row["scenario"]
    for row in price_rows:
        if row["verdict"] == "over-priced" and "price_break" not in break_points:
            break_points["price_break"] = f"+{row['price_change_pct']}%"

    return {
        "churn_sensitivity": churn_rows,
        "differentiation_sensitivity": diff_rows,
        "price_sensitivity": price_rows,
        "break_points": break_points,
        "headline_risk": _headline_risk(churn_rows, diff_rows),
    }


def _headline_risk(churn_rows: list[dict], diff_rows: list[dict]) -> str:
    """One-sentence summary of the biggest fragility."""
    if churn_rows:
        base = next((r for r in churn_rows if r["scenario"] == "base"), None)
        double = next((r for r in churn_rows if r["scenario"] == "double_churn"), None)
        if base and double and base["clv_usd"] and double["clv_usd"]:
            drop_pct = round((1 - double["clv_usd"] / base["clv_usd"]) * 100)
            return f"If churn doubles from base, CLV drops {drop_pct}% to ${double['clv_usd']}."
    return "Sensitivity computed across churn, differentiation, and price axes."


def full_economics(
    segment_summary: str,
    product_summary: str,
    optimal_price_monthly: float,
    pricing_unit: str = "account",
    competitor_prices: list[float] | None = None,
) -> dict:
    """
    Convenience wrapper: one LLM call + all the pure math. Returns the union:
      {unit_econ, clv, cac_target, evc}
    """
    if not optimal_price_monthly or optimal_price_monthly <= 0:
        return {"error": "valid optimal_price_monthly required"}
    ue = estimate_unit_economics(segment_summary, product_summary, optimal_price_monthly, pricing_unit, competitor_prices)
    if "error" in ue:
        return {"error": ue["error"], "stage": "unit_economics"}

    clv = compute_clv(
        monthly_price=optimal_price_monthly,
        monthly_churn_pct=ue.get("monthly_churn_pct", 5),
        annual_expansion_pct=ue.get("annual_expansion_rate_pct", 0),
        avg_contract_months=ue.get("avg_contract_months", 12),
    )
    cac = compute_cac_target(clv.get("clv_usd", 0)) if "error" not in clv else {"error": "CLV failed"}

    annual_price = optimal_price_monthly * 12
    evc = compute_evc(
        our_annual_price_usd=annual_price,
        reference_alternative_annual_cost_usd=ue.get("reference_alternative_annual_cost_usd", 0),
        differentiation_value_annual_usd=ue.get("differentiation_value_annual_usd", 0),
        differentiation_reasoning=ue.get("differentiation_value_reasoning", ""),
    )

    # Iter 36: sensitivity analysis layered on top (pure math, no LLM)
    sens = sensitivity_analysis(
        optimal_price_monthly=optimal_price_monthly,
        base_churn_pct=ue.get("monthly_churn_pct", 5),
        base_expansion_pct=ue.get("annual_expansion_rate_pct", 0),
        reference_value_usd=ue.get("reference_alternative_annual_cost_usd", 0),
        differentiation_value_usd=ue.get("differentiation_value_annual_usd", 0),
    )

    return {
        "pricing_unit": pricing_unit,
        "monthly_price_usd": optimal_price_monthly,
        "annual_price_usd": annual_price,
        "unit_economics": ue,
        "clv": clv,
        "cac_target": cac,
        "evc": evc,
        "sensitivity": sens,
    }
