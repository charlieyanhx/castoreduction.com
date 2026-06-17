"""business_model.py — classify a venture's monetization model and compute model-appropriate
unit economics.

cycle37 (audit follow-up): the pricing → unit-economics → financials spine assumed B2B SaaS
*subscription* for every venture — CLV = monthly_price / churn, annual = price × 12, tiers
"per account/seat per month", CLV:CAC "B2B SaaS benchmark". A $6-per-drink walk-in cafe is
*transactional retail*, not a monthly subscription, so that framing produced numbers that make
no sense to a human (a cafe with an "Enterprise $58/mo tier" and a "CLV:CAC 3:1 SaaS" verdict).

This module routes each venture to the right economics:
  - transactional : physical retail / per-visit / per-unit (cafe, restaurant, salon, gym drop-in)
  - subscription  : recurring monthly/annual (SaaS, membership) — the original behavior
  - (ecommerce one-time DTC currently maps to transactional per-unit economics)

The classifier is deterministic (no LLM). The retail economics are pure math.
"""
from __future__ import annotations

from typing import Optional

# cycle38 (audit M4 Phase B): seven monetization models, deterministic keyword routing.
TRANSACTIONAL = "transactional"   # physical retail / per-visit / per-unit
SUBSCRIPTION = "subscription"     # recurring monthly/annual (SaaS, membership)
ECOMMERCE = "ecommerce"           # one-time physical product / DTC
SERVICES = "services"             # agency / consultancy / project or retainer
HYBRID = "hybrid"                 # one-time + recurring (e.g. hardware device + subscription)
MARKETPLACE = "marketplace"       # take-rate / commission on third-party GMV
AD_SUPPORTED = "ad_supported"     # free to user, monetized via advertising

# Kinds whose economics are per-unit (price × volume − costs) — they all route to
# retail_unit_economics. Subscription, marketplace, ad-supported have their own bases.
_PER_UNIT_KINDS = (TRANSACTIONAL, ECOMMERCE, SERVICES, HYBRID)


def is_per_unit(kind: Optional[str]) -> bool:
    """True if the model's revenue is price-per-unit × volume (transactional/ecommerce/
    services/hybrid) → uses retail_unit_economics, not subscription CLV:CAC."""
    return (kind or "") in _PER_UNIT_KINDS


_SUBSCRIPTION_KW = (
    "subscription", "saas", "membership", " member", "per month", "/mo", "per seat",
    "monthly recurring", "recurring revenue", "annual contract", "license", "mrr",
)
# Tight, unambiguous marketplace signals only — a take-rate/commission on third-party GMV or
# an explicit two-sided market. (Loose terms like "platform"/"matches"/"connects" over-matched
# SaaS and news apps, so they are deliberately excluded.)
_MARKETPLACE_KW = (
    "marketplace", "two-sided", "two sided", "take rate", "take-rate", "take rate on",
    "% commission", "commission on each", "commission per", "connects buyers", "connects sellers",
    "connects homeowners", "vetted handymen", "vetted providers", "gig economy platform",
)
_AD_KW = (
    "ad-supported", "ad supported", "ad-funded", "advertising-supported", "ad revenue",
    "supported by ads", "monetized through ads", "monetized via ads", "monetize via advertising",
    "ad-based", "free, ad", "free ad-",
)
_SERVICES_KW = (
    "agency", "consultancy", "consulting", "design studio", "creative studio", "dev shop",
    "development studio", "freelance", "retainer", "project-based", "project fee",
    "done-for-you", "professional services", "studio for", "studio serving",
)
_ONETIME_KW = (
    "one-time", "one time", "per unit", "per bottle", "per bag", "per box", "per device",
    "hardware", "device", "dtc", "direct-to-consumer", "direct to consumer", "e-commerce",
    "ecommerce", "online store", "single purchase", "sells physical", "physical product",
)
_PER_VISIT_KW = (
    "drop-in", "drop in", "per visit", "per class", "per cut", "per drink", "per plate",
    "per session", "walk-in", "per ticket", "per cup", "pay-per-visit",
)


def classify_business_model(profile: dict, market_scale: Optional[dict] = None) -> str:
    """Deterministic monetization-model classifier (no LLM). Returns one of the seven kinds.

    A physical premise → transactional (or hybrid if it has BOTH drop-in and membership,
    or pure subscription if membership-only). A digital venture routes by monetization signal
    in specificity order: marketplace → ad-supported → services → (one-time+recurring=hybrid)
    → one-time=ecommerce → recurring=subscription → default subscription (preserves original
    behavior so nothing regresses)."""
    profile = profile or {}
    bm = (profile.get("business_model") or "").lower()
    cat = (profile.get("category") or "").lower()
    blob = f"{bm} {cat} {(profile.get('summary') or '').lower()}"
    ms = market_scale or {}
    is_physical = bool((ms.get("signals") or {}).get("is_physical")) or ms.get("scale") == "hyperlocal"

    def has(kws):
        return any(k in blob for k in kws)

    membership_first = has(("membership", "subscription-first", "members-only", "members only"))
    per_visit = has(_PER_VISIT_KW)

    # 1. Unambiguous models that must win even if the venture is (mis)tagged physical: a take-rate
    # marketplace, a free ad-supported product, or an explicit B2B services/agency. These keyword
    # sets are specific enough that a cafe/salon/gym never matches them.
    if has(_MARKETPLACE_KW):
        return MARKETPLACE
    if has(_AD_KW):
        return AD_SUPPORTED
    if has(_SERVICES_KW):
        return SERVICES

    # 2. Physical premise serving local trade.
    if is_physical:
        if per_visit and membership_first:
            return HYBRID            # e.g. gym: $30 drop-in + monthly membership
        if membership_first:
            return SUBSCRIPTION      # members-only club
        return TRANSACTIONAL         # cafe, restaurant, salon, food truck

    # 3. Digital / non-premise venture — route by remaining monetization signal.
    recurring = has(_SUBSCRIPTION_KW)
    onetime = has(_ONETIME_KW)
    if onetime and recurring:
        return HYBRID                # hardware device + subscription
    if onetime:
        return ECOMMERCE             # one-time physical product / DTC
    if recurring:
        return SUBSCRIPTION
    return SUBSCRIPTION              # default preserves original SaaS behavior


# Food-service signals — a per-unit price here is a *menu* price, benchmarked against nearby venues.
_FOOD_KW = (
    "cafe", "café", "coffee", "espresso", "restaurant", "eatery", "diner", "bistro",
    "bakery", "bar", "pub", "brewery", "food", "drink", "beverage", "juice", "tea",
    "kitchen", "deli", "ice cream", "smoothie",
)
# A venue noun used in "validate against nearby ___" so a cafe still reads "nearby cafes"
# but a restaurant reads "nearby restaurants" — never the wrong trade.
_FOOD_VENUE = (
    (("cafe", "café", "coffee", "espresso", "tea"), "cafes"),
    (("restaurant", "eatery", "diner", "bistro", "kitchen", "deli"), "restaurants"),
    (("bakery",), "bakeries"),
    (("bar", "pub", "brewery"), "bars"),
)
# Marketplace UNIT nouns — used by benchmark_validation_note to detect a marketplace by its
# per-transaction unit even when the keyword is implicit. The marketplace KEYWORD list is the
# single tight `_MARKETPLACE_KW` defined above (shared with the classifier); a second, looser
# copy here used to shadow it and made the classifier tag SaaS/news apps ("platform") as
# marketplaces — removed.
_MARKETPLACE_UNITS = ("booking", "job", "gig", "task", "project", "transaction", "match", "ride")


def benchmark_validation_note(unit: str, category: str = "", business_model: str = "") -> str:
    """A business-model-aware sentence telling the operator how to validate the competitor
    per-unit price benchmark — and against whom.

    The economics spine is shared across ventures, so this note must NOT bleed cafe/menu copy
    into a marketplace or generic-retail report (audit: a two-sided handyman marketplace was
    told its 'per-booking price benchmark requires local menu scraping (not bagged-bean prices);
    operator should validate against nearby cafes'). The unit noun and the comparable set are
    derived from the venture's own category/model.
    """
    u = (unit or "unit").strip() or "unit"
    blob = f"{category} {business_model} {u}".lower()

    if any(k in blob for k in _MARKETPLACE_KW) or u in _MARKETPLACE_UNITS:
        return (
            f"Competitor benchmark requires sampling rival take-rates and per-{u} fees; "
            "operator should validate against comparable marketplaces and local service providers."
        )

    if any(k in blob for k in _FOOD_KW):
        venue = next((noun for kws, noun in _FOOD_VENUE if any(k in blob for k in kws)), "venues")
        return (
            f"Competitor per-{u} price benchmark requires scraping local menus (per-{u} prices, "
            f"not packaged-retail prices); operator should validate against nearby {venue}."
        )

    return (
        f"Competitor per-{u} price benchmark requires sampling rival list prices for the same {u}; "
        "operator should validate against direct local competitors."
    )


def retail_unit_economics(
    price_per_unit: float,
    variable_cost_per_unit: float,
    monthly_fixed_cost: float,
    unit: str = "unit",
    est_visits_per_year: Optional[float] = None,
    annual_revenue_usd: Optional[float] = None,
    cost_source: str = "",
    category: str = "",
    business_model: str = "",
) -> dict:
    """Transactional retail unit economics — the honest analog of CLV:CAC for a per-visit business.

    Reports contribution margin per unit, break-even volume (per month AND per day — the number a
    cafe operator actually reasons about), and, when an annual SOM revenue is supplied, the implied
    monthly operating profit at that volume. No churn, no CLV, no "per account". The benchmark note
    is derived from the venture's category/model so it never references the wrong trade.
    """
    margin = price_per_unit - variable_cost_per_unit
    out: dict = {
        "model": TRANSACTIONAL,
        "unit": unit,
        "price_per_unit": round(price_per_unit, 2),
        "variable_cost_per_unit": round(variable_cost_per_unit, 2),
        "contribution_margin_per_unit": round(margin, 2),
        "contribution_margin_pct": round(margin / price_per_unit * 100, 1) if price_per_unit else None,
        "monthly_fixed_cost": round(monthly_fixed_cost, 0),
        "cost_source": cost_source,
        "benchmark_note": benchmark_validation_note(unit, category, business_model),
    }
    if margin <= 0:
        out["error"] = "price is below variable cost per unit — no positive contribution margin"
        return out
    be_units_month = monthly_fixed_cost / margin
    out["break_even_units_per_month"] = round(be_units_month)
    out["break_even_units_per_day"] = round(be_units_month / 30.0, 1)
    if est_visits_per_year:
        out["visits_per_year_assumed"] = est_visits_per_year
        out["annual_value_per_regular_usd"] = round(est_visits_per_year * margin, 2)
    if annual_revenue_usd:
        monthly_rev = annual_revenue_usd / 12.0
        monthly_units = monthly_rev / price_per_unit if price_per_unit else 0
        monthly_contribution = monthly_units * margin
        monthly_profit = monthly_contribution - monthly_fixed_cost
        out["at_som_volume"] = {
            "monthly_revenue_usd": round(monthly_rev),
            "monthly_units": round(monthly_units),
            "monthly_units_per_day": round(monthly_units / 30.0, 1),
            "monthly_operating_profit_usd": round(monthly_profit),
            "profitable_at_som": monthly_profit > 0,
        }
    return out
