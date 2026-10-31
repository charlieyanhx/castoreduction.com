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

# Extended model types — each requires its own economics calculator.
HOURLY        = "hourly"          # lawyers, consultants, studios — unit is time
RETAINER      = "retainer"        # committed block of time/availability, billed regardless of use
CONSIGNMENT   = "consignment"     # goods aren't yours; you sell and keep a cut (galleries, estate sales)
WHOLESALE     = "wholesale"       # you're a B2B channel node; price set by retailer acceptance
FREEMIUM      = "freemium"        # free tier + paid upgrade; economics driven by conversion rate
RAZOR_BLADES  = "razor_blades"    # platform cheap/free, consumable expensive; blended LTV
AUCTION       = "auction"         # price discovered through bidding; algorithm can't recommend price
DYNAMIC       = "dynamic"         # price changes by demand/time/inventory (hotels, airlines, Uber surge)
PERFORMANCE   = "performance"     # zero until outcome achieved, then pre-agreed share (law contingency)
ANCHOR_DISCOUNT = "anchor_discount"  # high reference price, sell at discount; stated price is fiction

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
    services/hybrid) → uses retail_unit_economics, not subscription CLV:CAC.

    The extended model types (HOURLY, RETAINER, CONSIGNMENT, WHOLESALE, FREEMIUM,
    RAZOR_BLADES, AUCTION, DYNAMIC, PERFORMANCE, ANCHOR_DISCOUNT) all return False —
    each has its own dedicated economics calculator."""
    return (kind or "") in _PER_UNIT_KINDS


_HOURLY_KW = (
    "per hour", "hourly rate", "$/hour", "billable hour", "time and materials",
    "day rate", "per day", "equipment rental",
)
_RETAINER_KW = (
    "monthly retainer", "on retainer", "availability fee", "committed hours",
)
_CONSIGNMENT_KW = (
    "consignment", "we don't own the inventory", "sell for others", "estate sale",
    "gallery commission", "take a cut of sales we don't own",
)
_WHOLESALE_KW = (
    "wholesale", "sell to retailers", "distributor", "b2b channel", "wholesale price",
    "sell to boutiques", "reseller",
)
_FREEMIUM_KW = (
    "freemium", "free tier", "free plan", "free forever", "convert free users",
    "paid upgrade",
)
_RAZOR_BLADES_KW = (
    "razor and blades", "platform and consumable", "printer and ink",
    "device and subscription", "hardware and recurring",
)
_AUCTION_KW = (
    "auction", "bidding", "bid price", "discovered price", "clearing price",
    "ebay model",
)
_DYNAMIC_KW = (
    "dynamic pricing", "yield management", "surge pricing", "variable rate",
    "demand-based pricing", "hotel pricing",
)
_PERFORMANCE_KW = (
    "contingency", "performance fee", "outcome-based", "pay on results",
    "commission on results", "success fee",
)
_ANCHOR_DISCOUNT_KW = (
    "anchor price", "msrp and discount", "list price then discount", "rrp",
    "mark-down from reference",
)

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
    # These run BEFORE the extended types because ad_supported + freemium ("free tier") and
    # services + retainer can overlap — the tighter original signals take precedence.
    if has(_MARKETPLACE_KW) or _MARKETPLACE_RE.search(blob):
        return MARKETPLACE
    if has(_AD_KW) or _AD_RE.search(blob):
        return AD_SUPPORTED
    if has(_SERVICES_KW) or _SERVICES_RE.search(blob):
        return SERVICES

    # 1b. Extended model types — checked after the original three unambiguous signals.
    if has(_AUCTION_KW):
        return AUCTION
    if has(_DYNAMIC_KW):
        return DYNAMIC
    if has(_PERFORMANCE_KW):
        return PERFORMANCE
    if has(_RAZOR_BLADES_KW):
        return RAZOR_BLADES
    if has(_FREEMIUM_KW):
        return FREEMIUM
    if has(_CONSIGNMENT_KW):
        return CONSIGNMENT
    if has(_WHOLESALE_KW):
        return WHOLESALE
    if has(_ANCHOR_DISCOUNT_KW):
        return ANCHOR_DISCOUNT
    # "retainer" alone could match the services keyword list too; the services RE fires first
    # for "done-for-you + retainer". Here we only catch standalone retainer billing.
    if has(_RETAINER_KW):
        return RETAINER
    if has(_HOURLY_KW):
        return HOURLY

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
    # The unit noun is NOT part of the model signal. "project" sits in _MARKETPLACE_UNITS
    # because platforms broker projects — and so every services venture, whose unit noun is
    # "project" by default, was told to "sample rival take-rates ... validate against
    # comparable marketplaces". An agency has no take-rate. That is the same bleed this
    # function's docstring exists to prevent, running the other direction: a marketplace
    # was given cafe copy, and the fix that stopped it started giving agencies marketplace
    # copy. The MODEL decides; the unit noun only names things once the model is known.
    blob = f"{category} {business_model}".lower()

    if any(k in blob for k in _MARKETPLACE_KW):
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


#: A delivery person's fully-loaded monthly cost, and the revenue-per-head ceiling above
#: which a professional-services profit claim stops being credible. Both are ASSUMPTIONS and
#: both are named in the reason string, because the point is to make the reader check them —
#: top-tier consultancies run $200-300k revenue per employee, so $400k is generous.
_LOADED_MONTHLY_COST_PER_HEAD = 15_000.0
_SERVICES_REVENUE_PER_HEAD_CEILING = 400_000.0


def capacity_withhold_reason(kind: Optional[str], monthly_fixed_cost: Optional[float],
                             annual_revenue_usd: Optional[float]) -> Optional[str]:
    """"SOM volume needs more people than the fixed cost buys" — the services analogue of
    `multi_site_withhold_reason`.

    A services venture's CAPACITY IS ITS PEOPLE, and their salaries sit in FIXED cost. So
    `retail_unit_economics` holds $60k/mo flat while volume ramps and reports the extra
    projects as nearly free — contribution margin 66.7%, and a profit claim at a volume the
    team cannot staff.

    MEASURED on the audit's consultancy ($12,000/project, $4,000 delivery cost, $60,000/mo
    fixed, SOM $3.0M/yr):

        at-SOM volume   252 projects/year, $106,750/mo operating profit, claimed
        $60k/mo fixed   ~4 people at a loaded rate
        4 people        ~35 six-week engagements a year

    7.2x more work than the staff can do, with their salaries held flat. Nothing withheld.

    This does NOT model capacity or utilisation — that is a project, and the audit says not
    to start it here. It withholds the PROFIT CLAIM (volumes and revenue stay, they are
    sound) when revenue per implied head passes a benchmark no services firm reaches, and
    names both assumptions so a reader can substitute their own.
    """
    if (kind or "").strip().lower() != SERVICES:
        return None
    try:
        fixed = float(monthly_fixed_cost or 0)
        annual = float(annual_revenue_usd or 0)
    except (TypeError, ValueError):
        return None
    if fixed <= 0 or annual <= 0:
        return None
    heads = fixed / _LOADED_MONTHLY_COST_PER_HEAD
    if heads <= 0:
        return None
    per_head = annual / heads
    if per_head <= _SERVICES_REVENUE_PER_HEAD_CEILING:
        return None
    return (
        f"Profit at this volume assumes ${per_head:,.0f} of revenue per delivery head — "
        f"the ${fixed:,.0f}/mo fixed cost implies about {heads:.0f} people at "
        f"${_LOADED_MONTHLY_COST_PER_HEAD:,.0f} loaded, and professional-services firms run "
        f"$200-300k per employee. Delivery labour sits in FIXED cost here, so the model "
        f"treats extra engagements as nearly free and holds headcount flat while volume "
        f"grows. The volume and revenue stand; the profit claim is withheld until the "
        f"operator supplies real delivery capacity and a staffing plan.")


try:  # provenance: record that this function produced a report key
    from skills.registry import records_production as _records_production
except Exception:  # pragma: no cover — never let provenance break an import
    def _records_production(_k):
        return lambda f: f


#: Kinds that genuinely run a single physical site, for the fixed-cost fallback below.
_SINGLE_SITE_KINDS = (TRANSACTIONAL,)


def _fixed_cost_basis(cost_source: str, kind: Optional[str] = None) -> str:
    """What the monthly fixed cost IS, taken from whoever computed it.

    `estimate_cost_structure` describes the basis it used and passes it through as
    `cost_source` ("estimated: early-stage company overhead (team + infrastructure +
    tooling)"). This reads that rather than restating a cafe's cost structure for every
    venture. Falls back only when nothing was supplied, and then only claims rent for a
    kind that plausibly pays it.
    """
    src = (cost_source or "").strip()
    if src:
        # Strip the "estimated: " lead-in — the basis is the noun phrase after it.
        return src.split(":", 1)[1].strip() if ":" in src else src
    if (kind or "").strip().lower() in _SINGLE_SITE_KINDS:
        return "single-site rent + staff + utilities"
    return "monthly fixed cost as supplied (basis not stated)"


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
            # `estimate_cost_structure` ALREADY works out the right basis — a consultancy
            # gets "early-stage company overhead (team + infrastructure + tooling)" — and
            # it arrives here as `cost_source` and was dropped: two producers, zero
            # consumers. Hardcoding shop rent meant a DTC brand and an agency were both
            # told their fixed cost was a single site's rent and utilities.
            "fixed_cost_basis": _fixed_cost_basis(cost_source, kind),
        }
        # TWO reasons a profit claim at SOM volume can be unsafe, and they are independent:
        # the volume spans more SITES than the fixed cost covers (physical retail), or it
        # needs more PEOPLE than the fixed cost buys (services, where delivery labour is
        # itself the fixed cost). The second had no predicate, so a consultancy claimed
        # $106,750/mo at 7.2x the work its implied headcount can deliver.
        _withhold = (multi_site_withhold_reason(market_scale)
                     or capacity_withhold_reason(kind, monthly_fixed_cost,
                                                 annual_revenue_usd))
        if _withhold:
            # Show the volume, withhold the profit verdict, say why — with the SAME
            # sentence financials uses, from the same predicate.
            asv["profit_withheld_reason"] = _withhold
        else:
            asv["monthly_operating_profit_usd"] = monthly_profit
            asv["profitable_at_som"] = monthly_profit > 0
        out["at_som_volume"] = asv
    return out


def hourly_economics(
    hourly_rate: float,
    utilization_pct: float,
    weekly_capacity_hours: float,
    monthly_fixed_cost: float,
    variable_cost_rate: float = 0.0,
    unit: str = "hour",
) -> dict:
    """Time-based (hourly/daily) unit economics — lawyers, consultants, studios."""
    billable_hours_per_week = weekly_capacity_hours * (utilization_pct / 100)
    billable_hours_per_month = billable_hours_per_week * 4.33
    revenue_per_month = hourly_rate * billable_hours_per_month
    contribution_margin_per_hour = hourly_rate * (1 - variable_cost_rate)
    contribution_margin_pct = (1 - variable_cost_rate) * 100

    if contribution_margin_per_hour > 0:
        break_even_hours_per_month = monthly_fixed_cost / contribution_margin_per_hour
        break_even_utilization_pct = (break_even_hours_per_month / (weekly_capacity_hours * 4.33)) * 100
    else:
        break_even_hours_per_month = 0.0
        break_even_utilization_pct = 0.0

    monthly_operating_profit = revenue_per_month - monthly_fixed_cost - (revenue_per_month * variable_cost_rate)

    return {
        "model": HOURLY,
        "unit": unit,
        "hourly_rate": hourly_rate,
        "utilization_pct": utilization_pct,
        "billable_hours_per_month": round(billable_hours_per_month, 1),
        "revenue_at_utilization": round(revenue_per_month, 0),
        "contribution_margin_per_hour": round(contribution_margin_per_hour, 2),
        "contribution_margin_pct": round(contribution_margin_pct, 1),
        "monthly_fixed_cost": monthly_fixed_cost,
        "break_even_hours_per_month": math.ceil(break_even_hours_per_month),
        "break_even_utilization_pct": round(break_even_utilization_pct, 1),
        "monthly_operating_profit": round(monthly_operating_profit, 0),
        "profitable_at_utilization": monthly_operating_profit > 0,
    }


def freemium_economics(
    paid_arpu: float,
    conversion_rate_pct: float,
    variable_cost_per_paid_user: float,
    monthly_fixed_cost: float,
    free_to_paid_months: float = 6,
    unit: str = "paid user",
) -> dict:
    """Freemium economics — free tier + paid upgrade, driven by conversion rate."""
    conversion_rate = conversion_rate_pct / 100
    margin_per_paid = paid_arpu - variable_cost_per_paid_user

    if margin_per_paid > 0:
        break_even_paid_users = math.ceil(monthly_fixed_cost / margin_per_paid)
        break_even_total_users = math.ceil(break_even_paid_users / conversion_rate) if conversion_rate > 0 else None
    else:
        break_even_paid_users = None
        break_even_total_users = None

    return {
        "model": FREEMIUM,
        "unit": unit,
        "paid_arpu": paid_arpu,
        "conversion_rate_pct": conversion_rate_pct,
        "free_to_paid_months": free_to_paid_months,
        "margin_per_paid_user": round(margin_per_paid, 2),
        "monthly_fixed_cost": monthly_fixed_cost,
        "break_even_paid_users": break_even_paid_users,
        "break_even_total_users": break_even_total_users,
        "note": (
            f"Break-even requires {break_even_paid_users} paying users "
            f"({conversion_rate_pct}% of {break_even_total_users} total users)"
            if break_even_paid_users else
            "Cannot compute: margin per paid user \u2264 0"
        ),
    }


def consignment_economics(
    take_rate_pct: float,
    avg_sale_value: float,
    transaction_cost_rate: float,
    monthly_fixed_cost: float,
    unit: str = "consignment sale",
) -> dict:
    """Consignment economics — sell on behalf of others, keep a cut. No COGS."""
    take_rate = take_rate_pct / 100
    revenue_per_sale = avg_sale_value * take_rate
    net_per_sale = revenue_per_sale * (1 - transaction_cost_rate)

    if net_per_sale > 0:
        break_even_sales_per_month = math.ceil(monthly_fixed_cost / net_per_sale)
        break_even_gmv_per_month = break_even_sales_per_month * avg_sale_value
    else:
        break_even_sales_per_month = None
        break_even_gmv_per_month = None

    return {
        "model": CONSIGNMENT,
        "unit": unit,
        "take_rate_pct": take_rate_pct,
        "avg_sale_value": avg_sale_value,
        "revenue_per_sale": round(revenue_per_sale, 2),
        "net_per_sale": round(net_per_sale, 2),
        "cogs": 0,
        "contribution_margin_pct": 100 * (1 - transaction_cost_rate),
        "monthly_fixed_cost": monthly_fixed_cost,
        "break_even_sales_per_month": break_even_sales_per_month,
        "break_even_gmv_per_month": round(break_even_gmv_per_month, 0) if break_even_gmv_per_month else None,
        "note": "No cost of goods — inventory belongs to consignor. Margin is on take-rate only.",
    }


def wholesale_economics(
    wholesale_price: float,
    variable_cost_per_unit: float,
    monthly_fixed_cost: float,
    retail_price: Optional[float] = None,
    unit: str = "unit (wholesale)",
) -> dict:
    """Wholesale economics — B2B channel; economics computed at wholesale price, not retail."""
    margin_per_unit = wholesale_price - variable_cost_per_unit
    margin_pct = (margin_per_unit / wholesale_price * 100) if wholesale_price else 0
    break_even_units = math.ceil(monthly_fixed_cost / margin_per_unit) if margin_per_unit > 0 else None

    retail_note = None
    if retail_price and wholesale_price:
        retailer_margin = round((1 - wholesale_price / retail_price) * 100)
        retail_note = f"Retail price ${retail_price} — retailer takes {retailer_margin}% margin"

    return {
        "model": WHOLESALE,
        "unit": unit,
        "wholesale_price": wholesale_price,
        "retail_price": retail_price,
        "variable_cost_per_unit": variable_cost_per_unit,
        "margin_per_unit": round(margin_per_unit, 2),
        "margin_pct": round(margin_pct, 1),
        "monthly_fixed_cost": monthly_fixed_cost,
        "break_even_units_per_month": break_even_units,
        "retail_note": retail_note,
        "note": "Economics computed at wholesale price. End-consumer price is not the founder's price.",
    }


def razor_blades_economics(
    hardware_price: float,
    hardware_cost: float,
    consumable_price: float,
    consumable_cost: float,
    consumables_per_year: float,
    monthly_fixed_cost: float,
    unit: str = "customer",
) -> dict:
    """Razor + blades economics — platform cheap/free, consumable expensive; blended LTV."""
    hardware_margin = hardware_price - hardware_cost
    consumable_margin_per_unit = consumable_price - consumable_cost
    annual_consumable_margin = consumable_margin_per_unit * consumables_per_year
    monthly_consumable_margin = annual_consumable_margin / 12

    blended_3yr_ltv = hardware_margin + (annual_consumable_margin * 3)

    if monthly_consumable_margin > 0:
        break_even_customers = math.ceil(monthly_fixed_cost / monthly_consumable_margin)
    else:
        break_even_customers = None

    return {
        "model": RAZOR_BLADES,
        "unit": unit,
        "hardware_price": hardware_price,
        "hardware_cost": hardware_cost,
        "hardware_margin": round(hardware_margin, 2),
        "hardware_margin_pct": round(hardware_margin / hardware_price * 100, 1) if hardware_price else 0,
        "consumable_price": consumable_price,
        "consumable_cost": consumable_cost,
        "consumable_margin_per_unit": round(consumable_margin_per_unit, 2),
        "consumables_per_year": consumables_per_year,
        "annual_consumable_margin_per_customer": round(annual_consumable_margin, 2),
        "monthly_consumable_margin_per_customer": round(monthly_consumable_margin, 2),
        "blended_3yr_ltv": round(blended_3yr_ltv, 2),
        "monthly_fixed_cost": monthly_fixed_cost,
        "break_even_installed_base": break_even_customers,
        "note": "Hardware margin may be negative (loss leader). Profitability depends on installed base \u00d7 consumable attach rate.",
    }


def dynamic_economics(
    capacity_units: float,
    avg_rate_usd: float,
    avg_utilization_pct: float,
    variable_cost_per_unit: float,
    monthly_fixed_cost: float,
    unit: str = "booking",
) -> dict:
    """Dynamic / yield pricing economics — hotels, airlines, Uber surge."""
    utilization = avg_utilization_pct / 100
    monthly_bookings = capacity_units * utilization * 30
    monthly_revenue = monthly_bookings * avg_rate_usd
    contribution_per_unit = avg_rate_usd - variable_cost_per_unit
    monthly_contribution = monthly_bookings * contribution_per_unit
    monthly_profit = monthly_contribution - monthly_fixed_cost

    if contribution_per_unit > 0:
        break_even_utilization_pct = (monthly_fixed_cost / (capacity_units * 30 * contribution_per_unit)) * 100
    else:
        break_even_utilization_pct = None

    return {
        "model": DYNAMIC,
        "unit": unit,
        "capacity_units": capacity_units,
        "avg_rate_usd": avg_rate_usd,
        "avg_utilization_pct": avg_utilization_pct,
        "monthly_bookings": round(monthly_bookings, 1),
        "monthly_revenue": round(monthly_revenue, 0),
        "variable_cost_per_unit": variable_cost_per_unit,
        "contribution_per_unit": round(contribution_per_unit, 2),
        "monthly_contribution": round(monthly_contribution, 0),
        "monthly_fixed_cost": monthly_fixed_cost,
        "monthly_operating_profit": round(monthly_profit, 0),
        "break_even_utilization_pct": round(break_even_utilization_pct, 1) if break_even_utilization_pct is not None else None,
        "pricing_note": (
            "Price shown is average yield \u2014 actual price varies by demand, time, and "
            "inventory level. Model cannot recommend a single price; expected revenue per "
            "capacity unit is the operative metric."
        ),
    }


def performance_economics(
    avg_outcome_value: float,
    success_fee_pct: float,
    win_rate_pct: float,
    monthly_fixed_cost: float,
    variable_cost_per_engagement: float = 0,
    unit: str = "placement",
) -> dict:
    """Performance / contingency economics — zero until outcome, then pre-agreed share."""
    success_fee = avg_outcome_value * (success_fee_pct / 100)
    win_rate = win_rate_pct / 100
    expected_revenue_per_engagement = success_fee * win_rate
    expected_margin_per_engagement = expected_revenue_per_engagement - variable_cost_per_engagement

    engagements_to_break_even = (
        math.ceil(monthly_fixed_cost / expected_margin_per_engagement)
        if expected_margin_per_engagement > 0 else None
    )

    return {
        "model": PERFORMANCE,
        "unit": unit,
        "avg_outcome_value": avg_outcome_value,
        "success_fee_pct": success_fee_pct,
        "success_fee_per_win": round(success_fee, 2),
        "win_rate_pct": win_rate_pct,
        "expected_revenue_per_engagement": round(expected_revenue_per_engagement, 2),
        "variable_cost_per_engagement": variable_cost_per_engagement,
        "expected_margin_per_engagement": round(expected_margin_per_engagement, 2),
        "monthly_fixed_cost": monthly_fixed_cost,
        "engagements_to_break_even": engagements_to_break_even,
        "note": (
            f"Expected value model: {win_rate_pct}% win rate \u00d7 ${success_fee:,.0f} fee "
            f"= ${expected_revenue_per_engagement:,.0f} expected revenue per engagement taken on."
        ),
        "pricing_note": (
            "Price is zero until outcome \u2014 the algorithm cannot recommend a price. "
            "The operative decision is which engagements to take, not what to charge."
        ),
    }


def auction_economics(
    avg_comparable_price: float,
    auction_fee_pct: float,
    seller_premium_pct: float,
    monthly_fixed_cost: float,
    unit: str = "lot",
) -> dict:
    """Auction economics — price discovered through bidding, not set by the seller."""
    total_seller_rate = (auction_fee_pct + seller_premium_pct) / 100
    expected_revenue_per_lot = avg_comparable_price * (1 - total_seller_rate)

    return {
        "model": AUCTION,
        "unit": unit,
        "avg_comparable_price": avg_comparable_price,
        "auction_fee_pct": auction_fee_pct,
        "seller_premium_pct": seller_premium_pct,
        "expected_net_per_lot": round(expected_revenue_per_lot, 2),
        "monthly_fixed_cost": monthly_fixed_cost,
        "pricing_note": (
            "Price is not set by the seller \u2014 it is discovered through bidding. "
            "The algorithm models expected clearing price from comparables, not a "
            "recommended price. Actual clearing price may vary significantly."
        ),
    }


def anchor_discount_economics(
    anchor_price: float,
    sell_price: float,
    variable_cost: float,
    monthly_fixed_cost: float,
    unit: str = "unit",
) -> dict:
    """Anchor + discount economics — high reference price, sell at discount."""
    actual_margin = sell_price - variable_cost
    actual_margin_pct = (actual_margin / sell_price * 100) if sell_price else 0
    discount_pct = ((anchor_price - sell_price) / anchor_price * 100) if anchor_price else 0
    break_even_units = math.ceil(monthly_fixed_cost / actual_margin) if actual_margin > 0 else None

    return {
        "model": ANCHOR_DISCOUNT,
        "unit": unit,
        "anchor_price": anchor_price,
        "sell_price": sell_price,
        "discount_pct": round(discount_pct, 1),
        "variable_cost": variable_cost,
        "margin_per_unit": round(actual_margin, 2),
        "margin_pct": round(actual_margin_pct, 1),
        "monthly_fixed_cost": monthly_fixed_cost,
        "break_even_units_per_month": break_even_units,
        "note": (
            f"Economics computed on actual sell price ${sell_price}, not anchor ${anchor_price}. "
            f"The {discount_pct:.0f}% discount is a positioning mechanic, not a cost."
        ),
    }


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
        # Word boundaries, not bare substrings. _norm maps "/" to a space, so the pricing
        # keyword "/mo" degrades to the bigram "mo" — which substring-matched inside the word
        # "model". Measured on job d62bc04f: a brief reading "Business model: Undetermined /
        # early exploratory" set explicit=True and suppressed the disclosure telling the
        # reader the subscription classification was INFERRED, not stated.
        return any(_re2.search(rf"(?<!\w){_re2.escape(_norm(k))}(?!\w)", blob) for k in kws)

    # Did anything in the brief name a revenue shape at all? The union of every signal the
    # classifier can act on — if none of them fired, whatever came back is an inference.
    explicit = bool(
        _has(_SUBSCRIPTION_KW) or _has(_MARKETPLACE_KW) or _has(_AD_KW)
        or _has(_SERVICES_KW) or _has(_ONETIME_KW) or _has(_PER_VISIT_KW)
        or _MARKETPLACE_RE.search(blob) or _AD_RE.search(blob)
        or _SERVICES_RE.search(blob) or _ONETIME_RE.search(blob)
        or _RECURRING_RE.search(blob)
        or _has(_HOURLY_KW) or _has(_RETAINER_KW) or _has(_CONSIGNMENT_KW)
        or _has(_WHOLESALE_KW) or _has(_FREEMIUM_KW) or _has(_RAZOR_BLADES_KW)
        or _has(_AUCTION_KW) or _has(_DYNAMIC_KW) or _has(_PERFORMANCE_KW)
        or _has(_ANCHOR_DISCOUNT_KW))

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


def pricing_model_label(kind: str) -> str:
    """Human-readable label for each pricing model type, for use in reports."""
    return {
        TRANSACTIONAL: "Per-unit (transactional)",
        SUBSCRIPTION: "Subscription",
        ECOMMERCE: "E-commerce (one-time)",
        SERVICES: "Project / fixed-fee",
        HYBRID: "Hybrid (one-time + recurring)",
        MARKETPLACE: "Marketplace (take-rate on GMV)",
        AD_SUPPORTED: "Ad-supported (free to user)",
        HOURLY: "Time-based (hourly / daily)",
        RETAINER: "Retainer",
        CONSIGNMENT: "Consignment",
        WHOLESALE: "Wholesale (B2B channel)",
        FREEMIUM: "Freemium (free tier + paid upgrade)",
        RAZOR_BLADES: "Razor + blades (platform + consumable)",
        AUCTION: "Auction / discovered price",
        DYNAMIC: "Dynamic / yield pricing",
        PERFORMANCE: "Performance / contingency",
        ANCHOR_DISCOUNT: "Anchor + discount",
    }.get(kind, kind)
