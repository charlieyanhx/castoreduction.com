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

import math
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


def venture_has_a_customer_price(kind: Optional[str]) -> bool:
    """Does an END CUSTOMER of this venture pay a price the report can recommend?

    False for ad_supported ALONE. A marketplace charges a take-rate, a subscription a fee,
    a cafe a menu price — every other kind has some number a buyer hands over, and pricing
    research is meaningful for all of them. An ad-supported product's user pays nothing, so
    a recommended price for them is not merely uncertain, it is a recommendation about a
    transaction that does not exist.

    MEASURED, rendering an ad_supported venture through the real template: the report ships
    a validated three-tier deck — "Value $3.49 / Standard $4.99 / Premium $11.99" and
    "Optimal price point: $4.99. Acceptable range: $3.49-$11.99" — two inches above its own
    sentence "Free to the user — there is no subscriber price." The pricing simulation runs
    unconditionally and the template gates on `psm.optimal_price_point or …`, never on the
    monetization model, so a number nobody will ever pay is presented with a method name
    attached to it.

    A separate predicate rather than `not is_per_unit(kind)`: that is True for subscription
    and marketplace too, and suppressing their pricing would delete the analysis those
    ventures most need. This is the narrower question, asked in one place because four
    sites need the same answer (audit C7).
    """
    return (kind or "").strip().lower() != AD_SUPPORTED


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
    "per session", "walk-in", "per ticket", "per cup", "pay-per-visit", "per bowl",
    "per meal", "per order",
)


import re as _re2

# ---------------------------------------------------------------------------------------
# Matching that reads how founders actually write, not how a keyword list was typed.
#
# MEASURED before this: 19 of 35 natural phrasings of the seven models classified correctly
# (54%), and EVERY miss fell through to `subscription` because that is the default. So a
# marketplace got CLV and churn instead of take-rate on GMV, and the report was internally
# consistent and entirely wrong. The literal lists recognised "advertising-supported" and
# missed "advertising supported"; recognised "monetized via ads" and missed "monetized with
# display advertising".
#
# Two changes. Punctuation stops being a distinct business model (_norm), and the SEMANTIC
# signal gets a pattern instead of an enumeration of its spellings. The literal lists stay —
# they encode specificity that was learned from real misroutes — and the patterns are
# additive.
# ---------------------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Lowercase, punctuation-insensitive, whitespace-collapsed.

    "ad-supported", "ad supported" and "ad—supported" are one concept; treating them as
    three is how a substring matcher accumulates near-duplicates and still misses the
    fourth spelling."""
    t = (text or "").lower()
    # The ASCII hyphen is U+002D and sits OUTSIDE the \u2010-\u2015 dash block — the first
    # version of this line omitted it, so "peer-to-peer" never normalised and the pattern
    # written to catch it could not fire. The common case was the one that got missed.
    t = _re2.sub(r"[-\u2010-\u2015_/]", " ", t)     # hyphen family, underscore, slash
    return _re2.sub(r"\s+", " ", t).strip()


# A take-rate on somebody else's transaction. Deliberately does NOT include bare "platform"
# or "connects" — those over-matched SaaS and news apps, which is why the literal list
# excluded them, and that judgement is preserved here.
_MARKETPLACE_RE = _re2.compile(
    r"take rate|take a cut|\btwo sided\b|\bpeer to peer\b|\bp2p\b|\bgmv\b|"
    r"\d+\s*%\s*(?:commission|take|of (?:each|every|the|all))|"
    r"commission (?:on|per|of|from)|"
    r"match(?:es|ing)? (?:supply and demand|buyers (?:and|with) sellers)|"
    r"connect(?:s|ing)? \w+ (?:with|and) (?:vetted |local )?\w+")

# Advertising as the REVENUE, not as a marketing channel. "we advertise on Instagram" is a
# channel and must not match — hence the required monetization context on every branch.
_AD_RE = _re2.compile(
    r"(?:free|no charge|no cost|zero cost)[^.]{0,40}?(?:\bads?\b|advertis|sponsor)|"
    r"(?:\bads?\b|advertis\w*|sponsor\w*)[^.]{0,30}?"
    r"(?:revenue|inventory|supported|funded|monetiz|pay us|pay for placement)|"
    r"monetiz\w*[^.]{0,30}?(?:\bads?\b|advertis|sponsor)")

_SERVICES_RE = _re2.compile(
    r"\bbill(?:s|ed|ing)?\b[^.]{0,30}?(?:hourly|by the hour|per (?:hour|project|engagement|day))|"
    r"per (?:project|engagement|deliverable)|\bretainer\b|"
    r"\b(?:agency|consultancy|consulting|professional services)\b|"
    r"(?:custom|bespoke) \w*\s*(?:implementation|integration|build|projects?)|"
    r"done for you")

_ONETIME_RE = _re2.compile(
    r"\bone time\b|single purchase|buy (?:it |the \w+ )?once|"
    r"sell(?:s|ing)? (?:physical|tangible) (?:goods|products?)|"
    r"customers? buy [^.]{0,25}once|\bdtc\b|direct to consumer|"
    r"\b(?:buy|purchase)\b[^.]{0,20}?\b(?:unit|device|hardware|kit|machine|equipment)\b")

# Recurring as a REVENUE shape. "recurring software fee" and "subscribe for analytics" are
# recurring; the literal list only had "recurring revenue" and "subscription".
_RECURRING_RE = _re2.compile(
    r"\bsubscrib\w+|\brecurring\b[^.]{0,25}?(?:fee|charge|payment|billing|revenue|software|"
    r"licen[cs]e)|(?:monthly|annual|yearly|per month|per year)[^.]{0,25}?"
    r"(?:fee|plan|pass|club|membership|licen[cs]e|retainer|contract)")


def classify_business_model(profile: dict, market_scale: Optional[dict] = None) -> str:
    """Deterministic monetization-model classifier (no LLM). Returns one of the seven kinds.

    A physical premise → transactional (or hybrid if it has BOTH drop-in and membership,
    or pure subscription if membership-only). A digital venture routes by monetization signal
    in specificity order: marketplace → ad-supported → services → (one-time+recurring=hybrid)
    → one-time=ecommerce → recurring=subscription → default subscription (preserves original
    behavior so nothing regresses)."""
    profile = profile or {}
    # Normalised once; every literal list below is normalised the same way, so punctuation
    # variants collapse instead of each needing its own entry.
    blob = _norm(f"{profile.get('business_model') or ''} {profile.get('category') or ''} "
                 f"{profile.get('summary') or ''}")
    ms = market_scale or {}
    is_physical = bool((ms.get("signals") or {}).get("is_physical")) or ms.get("scale") == "hyperlocal"

    def has(kws):
        return any(_norm(k) in blob for k in kws)

    membership_first = (has(("membership", "subscription-first", "members-only", "members only"))
                        or bool(_RECURRING_RE.search(blob)))
    per_visit = has(_PER_VISIT_KW)

    # 1. Unambiguous models that must win even if the venture is (mis)tagged physical: a take-rate
    # marketplace, a free ad-supported product, or an explicit B2B services/agency. These keyword
    # sets are specific enough that a cafe/salon/gym never matches them.
    if has(_MARKETPLACE_KW) or _MARKETPLACE_RE.search(blob):
        return MARKETPLACE
    if has(_AD_KW) or _AD_RE.search(blob):
        return AD_SUPPORTED
    if has(_SERVICES_KW) or _SERVICES_RE.search(blob):
        return SERVICES

    # 2. Physical premise serving local trade.
    if is_physical:
        if per_visit and membership_first:
            return HYBRID            # e.g. gym: $30 drop-in + monthly membership
        if membership_first:
            return SUBSCRIPTION      # members-only club
        return TRANSACTIONAL         # cafe, restaurant, salon, food truck

    # 3. Digital / non-premise venture — route by remaining monetization signal.
    recurring = has(_SUBSCRIPTION_KW) or bool(_RECURRING_RE.search(blob))
    onetime = has(_ONETIME_KW) or bool(_ONETIME_RE.search(blob))
    if per_visit and recurring:
        return HYBRID                # drop-in + membership, scale signal missing (mirror of §2)
    if onetime and recurring:
        return HYBRID                # hardware device + subscription
    if onetime:
        return ECOMMERCE             # one-time physical product / DTC
    if recurring:
        return SUBSCRIPTION
    # Venue/food-service fallback (D1/G1 root fix): a restaurant/cafe/per-visit venture that
    # reaches here only because the scale signal was missing (thin profile, or classifier
    # called before market_scale) must NOT default to subscription — that was the ecom_dtc
    # misroute class. Narrow on purpose: menu/visit pricing or an explicit food venue, with
    # WORD-BOUNDARY matching ("tea" must not match "teams").
    import re as _re
    if per_visit or any(_re.search(rf"(?<!\w){_re.escape(k)}(?!\w)", blob) for k in _FOOD_KW):
        return TRANSACTIONAL
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


def multi_site_withhold_reason(market_scale: str | None) -> str | None:
    """The ONE predicate for "SOM spans more sites than the fixed cost covers".

    Both the at-SOM economics block and the financials scenario table must make this
    judgement, and they must make it IDENTICALLY — two inline checks of the same
    condition is exactly how the at-SOM numbers drifted from the scenario table
    (fixed as D23). Regional and national_physical scales imply multiple sites;
    the cost model is one site's rent+staff+utilities, so a profit claim at those
    volumes would understate costs. Digital scales are excluded: their fixed cost is
    not site-bound (it is wrong for a different reason — the storefront cost prompt —
    which is rank 2's other half, not this predicate's job).
    """
    scale = (market_scale or "").lower()
    if "regional" in scale or "national_physical" in scale:
        return ("SOM spans multiple locations but fixed cost is single-site — a "
                "profit claim at this volume would understate costs.")
    return None


try:  # provenance: record that this function produced a report key
    from skills.registry import records_production as _records_production
except Exception:  # pragma: no cover — never let provenance break an import
    def _records_production(_k):
        return lambda f: f


@_records_production("economics")
def retail_unit_economics(
    price_per_unit: float,
    variable_cost_per_unit: float,
    monthly_fixed_cost: float,
    unit: str = "unit",
    est_visits_per_year: Optional[float] = None,
    annual_revenue_usd: Optional[float] = None,
    som_capture_frac: float = 1.0,
    cost_source: str = "",
    category: str = "",
    business_model: str = "",
    kind: str = TRANSACTIONAL,
    market_scale: str = "",
) -> dict:
    """Transactional retail unit economics — the honest analog of CLV:CAC for a per-visit business.

    Reports contribution margin per unit, break-even volume (per month AND per day — the number a
    cafe operator actually reasons about), and, when an annual SOM revenue is supplied, the implied
    monthly operating profit at that volume. No churn, no CLV, no "per account". The benchmark note
    is derived from the venture's category/model so it never references the wrong trade.

    G3 (D08): `som_capture_frac` scales the given revenue to the OBTAINABLE ceiling before the
    profitability claim is computed. plan.py passes the aggressive-scenario capture (60% of SOM,
    financials.Y3_CAPTURE) so "profitable at SOM" is claimed at the same volume the scenario
    table tops out at — never at a 100%-capture volume no scenario ever reaches.
    """
    margin = price_per_unit - variable_cost_per_unit
    out: dict = {
        "model": kind or TRANSACTIONAL,
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
    _margin_frac_disclosed = (out["contribution_margin_pct"] or 0) / 100.0
    be_units_month = monthly_fixed_cost / (price_per_unit * _margin_frac_disclosed) \
        if _margin_frac_disclosed else monthly_fixed_cost / margin
    # R4 rank 24: break-even is a THRESHOLD — you must sell at least this many units to
    # cover fixed cost. round() understated it (100.4 → "break even at 100" when 101 are
    # needed). Ceil the monthly figure and derive the daily rate from it.
    out["break_even_units_per_month"] = math.ceil(be_units_month)
    out["break_even_units_per_day"] = round(out["break_even_units_per_month"] / 30.0, 1)
    if est_visits_per_year:
        out["visits_per_year_assumed"] = est_visits_per_year
        out["annual_value_per_regular_usd"] = round(est_visits_per_year * margin, 2)
    if annual_revenue_usd:
        # Profit uses the SAME expression as financials' scenario rows — the rounded annual
        # ceiling and the 1-dp disclosed margin, rounded the same way — so the claim and the
        # aggressive Y3 row are bit-identical and can never disagree at the boundary.
        obtainable_annual = round(annual_revenue_usd * som_capture_frac)
        monthly_rev = obtainable_annual / 12.0
        monthly_units = monthly_rev / price_per_unit if price_per_unit else 0
        margin_frac = (out["contribution_margin_pct"] or 0) / 100.0
        monthly_profit = round(monthly_rev * margin_frac - out["monthly_fixed_cost"])
        asv = {
            "monthly_revenue_usd": round(monthly_rev),
            "monthly_units": round(monthly_units),
            "monthly_units_per_day": round(monthly_units / 30.0, 1),
            "som_capture_pct": round(som_capture_frac * 100, 1),
            "fixed_cost_basis": "single-site rent + staff + utilities",
        }
        _withhold = multi_site_withhold_reason(market_scale)
        if _withhold:
            # Show the volume, withhold the profit verdict, say why — with the SAME
            # sentence financials uses, from the same predicate.
            asv["profit_withheld_reason"] = _withhold
        else:
            asv["monthly_operating_profit_usd"] = monthly_profit
            asv["profitable_at_som"] = monthly_profit > 0
        out["at_som_volume"] = asv
    return out


def classify_with_confidence(profile: dict, market_scale: Optional[dict] = None) -> dict:
    """The kind, PLUS whether the brief actually said so.

    THE ROOT DEFECT THIS EXISTS FOR. classify_business_model ends in
    `return SUBSCRIPTION  # default preserves original SaaS behavior`, and that default is
    silent. MEASURED: 16 of 35 natural phrasings fell into it, so a marketplace was handed
    CLV, churn and MRR — a complete, coherent subscription model for a venture that never
    said it was recurring. Nothing in the report distinguished "the founder told us they
    charge monthly" from "we could not tell, so we assumed SaaS".

    Everything else in this codebase discloses that distinction. A sizing figure says
    whether it was fetched or estimated; the SOM anchor publishes its own method and its
    disagreement with the alternative. The monetization model — which picks the entire
    economic engine downstream — was the one load-bearing choice that never did.

    Returns {kind, explicit, signal, disclosure}. `kind` is exactly what
    classify_business_model returns, so callers can adopt this incrementally; `explicit` is
    False when nothing in the brief named a revenue shape, and `disclosure` is the sentence
    a report should carry when that happens.

    It does NOT refuse to classify. Blocking a report over a missing sentence would be worse
    than proceeding with a stated assumption — the default stays, it just stops being
    invisible.
    """
    kind = classify_business_model(profile, market_scale)
    profile = profile or {}
    blob = _norm(f"{profile.get('business_model') or ''} {profile.get('category') or ''} "
                 f"{profile.get('summary') or ''}")

    def _has(kws):
        return any(_norm(k) in blob for k in kws)

    # Did anything in the brief name a revenue shape at all? The union of every signal the
    # classifier can act on — if none of them fired, whatever came back is an inference.
    explicit = bool(
        _has(_SUBSCRIPTION_KW) or _has(_MARKETPLACE_KW) or _has(_AD_KW)
        or _has(_SERVICES_KW) or _has(_ONETIME_KW) or _has(_PER_VISIT_KW)
        or _MARKETPLACE_RE.search(blob) or _AD_RE.search(blob)
        or _SERVICES_RE.search(blob) or _ONETIME_RE.search(blob)
        or _RECURRING_RE.search(blob))

    ms = market_scale or {}
    if not explicit and ((ms.get("signals") or {}).get("is_physical")
                         or ms.get("scale") == "hyperlocal"):
        # A physical premise IS a monetization signal: you pay when you visit. That is an
        # inference from the venue, not from a stated model, but it is a grounded one.
        explicit = True

    disclosure = None
    if not explicit:
        disclosure = (
            f"Monetization model INFERRED as '{kind}' — the brief does not say how this "
            f"venture charges. Every figure below that depends on the revenue shape "
            f"(pricing, unit economics, lifetime value, the volume ladder) rests on that "
            f"assumption. If it is wrong, say how you charge and the numbers change.")
    return {"kind": kind, "explicit": explicit, "disclosure": disclosure}
