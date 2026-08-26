"""pricing_model.py — Pricing model classification + typed calculation.

ARCHITECTURE
------------
The LLM reads the venture description + competitor pricing + differentiators and
selects the right pricing model, extracting the parameters it can read from context.
Typed Python calculators then do the actual math — the LLM never writes formulas.

For parameters it cannot read (e.g. utilization_pct for a new business), the LLM
returns null and we substitute a market-data default with a disclosure note.

MODELS SUPPORTED
----------------
  standard     — fixed price per unit (seller sets it)
  cost_plus    — source at X, sell at X * markup multiplier
  evc          — competitor baseline + value of differentiators * capture rate
  time_based   — hourly/daily rate * utilization
  commission   — % of deal/transaction value
  consignment  — sell others' goods, keep a take-rate % of GMV
  subscription — recurring flat fee (delegated to existing PSM path)
  mixed        — weighted blend of two models (e.g. 60% cost_plus + 40% consignment)

OUTPUT SHAPE
------------
Every calculator returns the same keys so the report template doesn't branch:
  {
    "model":                  str,
    "price":                  float,   # the number customers pay / avg transaction
    "price_unit":             str,     # "item", "hour", "project", etc.
    "variable_cost":          float,
    "contribution_margin":    float,
    "contribution_margin_pct": float,
    "break_even_units_per_month": int,
    "break_even_units_per_day":  int,
    "params":                 dict,    # raw inputs used (for disclosure)
    "defaults_used":          list,    # params that were estimated, not stated
    "notes":                  list,    # human-readable disclosures
  }
"""
from __future__ import annotations

import math
from typing import Any

from llm import call_json
from logger import get

log = get("pricing_model")


# ---------------------------------------------------------------------------
# Market-data defaults — used when the LLM can't extract a param from the brief
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, dict[str, Any]] = {
    "cost_plus": {
        "markup_multiplier": 3.0,          # conservative retail default
        "avg_source_cost": None,           # must come from description or be flagged
        "variable_cost_other_pct": 0.05,   # packaging, payment processing (~5% of price)
    },
    "evc": {
        "capture_rate": 0.60,              # share 40% of value with customer
        "variable_cost_pct": 0.30,
    },
    "time_based": {
        "utilization_pct": 0.65,           # 65% of available hours billed
        "variable_cost_pct": 0.10,         # materials, software per hour
    },
    "commission": {
        "variable_cost_pct": 0.05,         # fulfilment, payment per deal
    },
    "consignment": {
        "take_rate_pct": 0.40,             # 40% take-rate is common in luxury resale
        "variable_cost_pct": 0.05,
    },
    "standard": {
        "variable_cost_pct": 0.35,
    },
}

_DEFAULT_LABELS = {
    "markup_multiplier":       "markup multiplier (retail default 3×; adjust if known)",
    "utilization_pct":         "utilization rate (industry default 65%)",
    "capture_rate":            "EVC capture rate (default 60%)",
    "variable_cost_pct":       "variable cost % (estimated)",
    "variable_cost_other_pct": "misc variable cost % (payment processing + packaging)",
    "take_rate_pct":           "consignment take-rate (luxury resale default 40%)",
}


def _fill_defaults(model: str, params: dict) -> tuple[dict, list[str]]:
    """Fill None params with market-data defaults. Returns (filled_params, defaults_used)."""
    defs = _DEFAULTS.get(model, {})
    filled = dict(params)
    defaults_used = []
    for k, default_val in defs.items():
        if filled.get(k) is None and default_val is not None:
            filled[k] = default_val
            label = _DEFAULT_LABELS.get(k, k)
            defaults_used.append(f"{k}: estimated as {default_val} ({label})")
    return filled, defaults_used


def _per_day(monthly: float) -> int:
    return max(1, math.ceil(monthly / 30))


# ---------------------------------------------------------------------------
# Calculators — one per model, pure math
# ---------------------------------------------------------------------------

def _calc_standard(price: float, variable_cost: float,
                   fixed_costs: float, **_) -> dict:
    cm = price - variable_cost
    cm_pct = round(cm / price * 100, 1) if price else 0
    be_mo = math.ceil(fixed_costs / cm) if cm > 0 else None
    return {
        "price": round(price, 2),
        "variable_cost": round(variable_cost, 2),
        "contribution_margin": round(cm, 2),
        "contribution_margin_pct": cm_pct,
        "break_even_units_per_month": be_mo,
        "break_even_units_per_day": _per_day(be_mo) if be_mo else None,
    }


def _calc_cost_plus(avg_source_cost: float, markup_multiplier: float,
                    variable_cost_other_pct: float, fixed_costs: float, **_) -> dict:
    price = avg_source_cost * markup_multiplier
    variable_cost = avg_source_cost + price * variable_cost_other_pct
    cm = price - variable_cost
    cm_pct = round(cm / price * 100, 1) if price else 0
    be_mo = math.ceil(fixed_costs / cm) if cm > 0 else None
    return {
        "price": round(price, 2),
        "variable_cost": round(variable_cost, 2),
        "contribution_margin": round(cm, 2),
        "contribution_margin_pct": cm_pct,
        "break_even_units_per_month": be_mo,
        "break_even_units_per_day": _per_day(be_mo) if be_mo else None,
    }


def _calc_evc(competitor_baseline_price: float, differentiator_value: float,
              capture_rate: float, variable_cost_pct: float,
              fixed_costs: float, **_) -> dict:
    price = competitor_baseline_price + differentiator_value * capture_rate
    variable_cost = price * variable_cost_pct
    cm = price - variable_cost
    cm_pct = round(cm / price * 100, 1) if price else 0
    be_mo = math.ceil(fixed_costs / cm) if cm > 0 else None
    return {
        "price": round(price, 2),
        "variable_cost": round(variable_cost, 2),
        "contribution_margin": round(cm, 2),
        "contribution_margin_pct": cm_pct,
        "break_even_units_per_month": be_mo,
        "break_even_units_per_day": _per_day(be_mo) if be_mo else None,
    }


def _calc_time_based(hourly_rate: float, utilization_pct: float,
                     hours_per_month: float, variable_cost_pct: float,
                     fixed_costs: float, **_) -> dict:
    # Price = effective hourly rate (what the business actually earns per available hour)
    effective_rate = hourly_rate * utilization_pct
    variable_cost = hourly_rate * variable_cost_pct
    cm = hourly_rate - variable_cost   # per billable hour
    cm_pct = round(cm / hourly_rate * 100, 1) if hourly_rate else 0
    # Break-even in billable hours per month
    be_hours = math.ceil(fixed_costs / cm) if cm > 0 else None
    be_mo = math.ceil(be_hours / utilization_pct) if (be_hours and utilization_pct) else None
    return {
        "price": round(effective_rate, 2),    # avg revenue per available hour
        "price_unit": "hour",
        "variable_cost": round(variable_cost, 2),
        "contribution_margin": round(cm, 2),
        "contribution_margin_pct": cm_pct,
        "break_even_units_per_month": be_mo,
        "break_even_units_per_day": _per_day(be_mo) if be_mo else None,
        "break_even_hours_billable": be_hours,
    }


def _calc_commission(avg_deal_size: float, commission_pct: float,
                     variable_cost_pct: float, fixed_costs: float, **_) -> dict:
    price = avg_deal_size * commission_pct   # revenue per deal
    variable_cost = price * variable_cost_pct
    cm = price - variable_cost
    cm_pct = round(cm / price * 100, 1) if price else 0
    be_mo = math.ceil(fixed_costs / cm) if cm > 0 else None
    return {
        "price": round(price, 2),            # revenue earned per deal
        "variable_cost": round(variable_cost, 2),
        "contribution_margin": round(cm, 2),
        "contribution_margin_pct": cm_pct,
        "break_even_units_per_month": be_mo,
        "break_even_units_per_day": _per_day(be_mo) if be_mo else None,
    }


def _calc_consignment(avg_gmv_per_item: float, take_rate_pct: float,
                      variable_cost_pct: float, fixed_costs: float, **_) -> dict:
    price = avg_gmv_per_item * take_rate_pct   # revenue per item sold
    variable_cost = price * variable_cost_pct
    cm = price - variable_cost
    cm_pct = round(cm / price * 100, 1) if price else 0
    be_mo = math.ceil(fixed_costs / cm) if cm > 0 else None
    return {
        "price": round(price, 2),
        "variable_cost": round(variable_cost, 2),
        "contribution_margin": round(cm, 2),
        "contribution_margin_pct": cm_pct,
        "break_even_units_per_month": be_mo,
        "break_even_units_per_day": _per_day(be_mo) if be_mo else None,
    }


_CALCULATORS = {
    "standard":    _calc_standard,
    "cost_plus":   _calc_cost_plus,
    "evc":         _calc_evc,
    "time_based":  _calc_time_based,
    "commission":  _calc_commission,
    "consignment": _calc_consignment,
}

# Default price unit noun per model
_UNIT_BY_MODEL = {
    "standard":    "unit",
    "cost_plus":   "item",
    "evc":         "unit",
    "time_based":  "hour",
    "commission":  "deal",
    "consignment": "item",
}


# ---------------------------------------------------------------------------
# LLM classification + parameter extraction
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM = """You identify the pricing model for a venture and extract the
parameters needed to compute its unit economics.

Rules:
- Choose the model that matches HOW prices are actually set, not the business model label.
- A vintage shop buying at estate sales and marking up 500% → cost_plus.
- A consultant billing hourly → time_based.
- A recruiter taking 20% of first-year salary → commission.
- A gallery selling artists' work for 50% → consignment.
- A SaaS tool priced relative to competitors plus unique features → evc.
- If the venture uses two models (e.g. own-inventory cost_plus AND consignment for others'
  goods), return model="mixed" with components=[{model, weight, params}, ...].
- Return null for any parameter you cannot read from the inputs — do NOT guess.
- fixed_costs_monthly: extract from description if stated; else return null.

Return ONLY JSON matching this shape:
{
  "model": "standard|cost_plus|evc|time_based|commission|consignment|subscription|mixed",
  "price_unit": "item|hour|deal|project|seat|booking|...",
  "params": {
    // standard: { "price": float, "variable_cost": float }
    // cost_plus: { "avg_source_cost": float|null, "markup_multiplier": float|null,
    //              "variable_cost_other_pct": float|null }
    // evc: { "competitor_baseline_price": float|null, "differentiator_value": float|null,
    //         "capture_rate": float|null, "variable_cost_pct": float|null }
    // time_based: { "hourly_rate": float|null, "utilization_pct": float|null,
    //               "hours_per_month": float|null, "variable_cost_pct": float|null }
    // commission: { "avg_deal_size": float|null, "commission_pct": float|null,
    //               "variable_cost_pct": float|null }
    // consignment: { "avg_gmv_per_item": float|null, "take_rate_pct": float|null,
    //                "variable_cost_pct": float|null }
    // mixed: omit params here — use components instead
  },
  "components": [   // only for model="mixed"
    { "model": str, "weight": float, "params": {...} }
  ],
  "fixed_costs_monthly": float|null,
  "reasoning": "1 sentence — why this model fits"
}"""


def classify_and_extract(
    description: str,
    competitor_pricing: dict | None = None,
    differentiators: dict | None = None,
    profile: dict | None = None,
) -> dict:
    """One LLM call: identify pricing model + extract parameters from context."""
    prof = profile or {}

    # Surface the pricing_mechanism hint the profile step extracted — gives the LLM a
    # strong prior so it doesn't have to re-derive it from scratch.
    mechanism_hint = prof.get("pricing_mechanism", "")
    hint_line = (f"\nPROFILE HINT — pricing_mechanism: {mechanism_hint}"
                 if mechanism_hint and mechanism_hint != "unknown" else "")

    # Pull competitor median price for EVC if available
    comp_median = None
    if competitor_pricing and isinstance(competitor_pricing, dict):
        comp_median = (competitor_pricing.get("median_price")
                       or competitor_pricing.get("benchmark_price"))

    # Pull top differentiators for EVC value estimation
    diff_items = []
    if differentiators and isinstance(differentiators, dict):
        diff_items = (differentiators.get("differentiators") or [])[:4]

    user_parts = [
        f"DESCRIPTION:\n{description[:3000]}",
        hint_line,
    ]
    if comp_median:
        user_parts.append(f"\nCOMPETITOR MEDIAN PRICE: ${comp_median}")
    if diff_items:
        user_parts.append(
            "\nKEY DIFFERENTIATORS (for EVC value estimation):\n"
            + "\n".join(f"- {d.get('name','')}: {d.get('description','')}"
                        for d in diff_items if isinstance(d, dict))
        )

    raw = call_json(
        system=_CLASSIFY_SYSTEM,
        user="\n".join(p for p in user_parts if p),
        max_tokens=800,
    ) or {}

    if "_parse_error" in raw:
        log.warning("[pricing_model] LLM classification failed: %s", raw.get("_parse_error"))
        return {"model": "unknown", "error": raw.get("_parse_error")}

    return raw


# ---------------------------------------------------------------------------
# Dispatcher — run the right calculator(s), blend if mixed
# ---------------------------------------------------------------------------

def compute_pricing_economics(
    classification: dict,
    fixed_costs: float | None = None,
) -> dict:
    """Run the typed calculator for the classified model. Returns the standard output shape."""
    model = (classification.get("model") or "unknown").lower()
    price_unit = classification.get("price_unit") or _UNIT_BY_MODEL.get(model, "unit")
    fc = fixed_costs or classification.get("fixed_costs_monthly") or 0.0
    notes: list[str] = []

    if classification.get("reasoning"):
        notes.append(f"Model selected: {classification['reasoning']}")

    if model == "subscription":
        # Subscription economics are handled by the existing PSM path — this module
        # hands off rather than duplicating that logic.
        return {
            "model": "subscription",
            "price_unit": price_unit,
            "delegated_to": "psm",
            "notes": notes,
            "defaults_used": [],
            "params": classification.get("params") or {},
        }

    if model == "unknown" or model not in _CALCULATORS and model != "mixed":
        return {
            "model": model,
            "price_unit": price_unit,
            "error": f"No calculator for model '{model}' — falling back to PSM",
            "notes": notes,
            "defaults_used": [],
            "params": classification.get("params") or {},
        }

    if model == "mixed":
        return _compute_mixed(classification, fc, price_unit, notes)

    params, defaults_used = _fill_defaults(model, classification.get("params") or {})

    if fc:
        params["fixed_costs"] = fc

    # Check that mandatory params have values after defaults
    if model == "cost_plus" and params.get("avg_source_cost") is None:
        notes.append(
            "avg_source_cost not stated — cannot compute cost-plus price. "
            "State your typical sourcing cost (e.g. 'I source pieces for $3-8 on average')."
        )
        return {
            "model": model, "price_unit": price_unit,
            "error": "missing avg_source_cost",
            "notes": notes, "defaults_used": defaults_used,
            "params": params,
        }

    try:
        result = _CALCULATORS[model](**params)
    except Exception as e:
        log.warning("[pricing_model] calculator %s failed: %s", model, e)
        return {
            "model": model, "price_unit": price_unit,
            "error": str(e), "notes": notes,
            "defaults_used": defaults_used, "params": params,
        }

    result["model"] = model
    result["price_unit"] = result.get("price_unit") or price_unit
    result["params"] = params
    result["defaults_used"] = defaults_used
    result["notes"] = notes + (
        [f"Estimated parameter: {d}" for d in defaults_used] if defaults_used else []
    )
    return result


def _compute_mixed(classification: dict, fixed_costs: float,
                   price_unit: str, notes: list[str]) -> dict:
    """Weighted blend of two sub-models. Each calculator runs on its own params;
    outputs are blended by weight. Break-even uses blended contribution margin."""
    components = classification.get("components") or []
    if not components:
        return {"model": "mixed", "error": "no components specified",
                "notes": notes, "defaults_used": [], "params": {}}

    results = []
    all_defaults: list[str] = []
    total_weight = sum(float(c.get("weight", 1)) for c in components)

    for comp in components:
        sub_model = (comp.get("model") or "").lower()
        weight = float(comp.get("weight", 1)) / total_weight
        sub_params, sub_defaults = _fill_defaults(sub_model, comp.get("params") or {})
        sub_params["fixed_costs"] = fixed_costs * weight   # split fixed costs by weight

        if sub_model not in _CALCULATORS:
            continue
        try:
            sub_result = _CALCULATORS[sub_model](**sub_params)
            results.append((weight, sub_result, sub_params, sub_defaults))
            all_defaults.extend(sub_defaults)
        except Exception as e:
            log.warning("[pricing_model] mixed sub-model %s failed: %s", sub_model, e)

    if not results:
        return {"model": "mixed", "error": "all sub-models failed",
                "notes": notes, "defaults_used": [], "params": {}}

    # Weighted blend
    blended_price = sum(w * r.get("price", 0) for w, r, _, _ in results)
    blended_vc = sum(w * r.get("variable_cost", 0) for w, r, _, _ in results)
    blended_cm = sum(w * r.get("contribution_margin", 0) for w, r, _, _ in results)
    blended_cm_pct = round(blended_cm / blended_price * 100, 1) if blended_price else 0
    be_mo = math.ceil(fixed_costs / blended_cm) if blended_cm > 0 else None

    return {
        "model": "mixed",
        "price_unit": price_unit,
        "price": round(blended_price, 2),
        "variable_cost": round(blended_vc, 2),
        "contribution_margin": round(blended_cm, 2),
        "contribution_margin_pct": blended_cm_pct,
        "break_even_units_per_month": be_mo,
        "break_even_units_per_day": _per_day(be_mo) if be_mo else None,
        "components": [
            {"model": comp.get("model"), "weight": comp.get("weight")}
            for comp in components
        ],
        "params": {"components": [c.get("params") for _, _, c, _ in results]},
        "defaults_used": all_defaults,
        "notes": notes + ([f"Estimated: {d}" for d in all_defaults] if all_defaults else []),
    }
