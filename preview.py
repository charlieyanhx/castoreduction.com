"""preview.py — what we can tell the founder for free, before anyone pays.

WHY THIS EXISTS. The report costs real work: metered tools, a rate-limited LLM chain and
about six minutes of wall clock. Everything BEFORE it is nearly free, and until now that
free budget bought nothing. A founder filled in a survey and was asked for money holding
only a promise, which is the worst possible moment to ask.

So this module spends the free budget on an artifact. Every number here is computed from
what the founder typed, by arithmetic in this file, with ZERO model calls: the question
tree is code, business_model.classify_with_confidence is code, and the Fermi service model
is a table. That is not an accident of implementation, it is the same property that makes
the pipeline honest, reused: a system whose reasoning is code can show its reasoning away.

THREE RULES, and they are the same rules the report runs on.

  1. NEVER FLATTER. This does not say whether the idea is good. It has a paragraph of
     prose and no market data, so any verdict would be fabrication, and fabrication is the
     defect class this codebase spends its whole life removing. It says what KIND of
     venture this is, what would decide it, and what we would have to measure.
  2. STATE THE LIMITS OUT LOUD. Every figure here carries what it ignores. The break-even
     ignores cost of goods, because nobody has been asked for it yet. Saying so is the
     product demonstrating its actual differentiator on the one screen where a buyer is
     deciding whether to trust it.
  3. ARITHMETIC ONLY, AND PRINT IT. Every number below is one division or one
     multiplication, shown with its inputs, so the founder can check it on their phone. A
     figure they can re-run is worth more than a figure they have to believe.

WHAT IT DELIBERATELY DOES NOT DO. It does not size a market, count households, price
competitors or read the census. Those need the paid run, and the honest line between the
free screen and the paid one is exactly there: this screen is YOUR arithmetic, the report
is the outside world.
"""
from __future__ import annotations

import math
from typing import Optional

import slots

# The trade-area radius the hyperlocal sizer actually draws. Named here so the free screen
# promises the same ring the paid run measures.
TRADE_AREA_KM = 1.5
#: Days a month, for turning a monthly break-even into a daily target. The pipeline uses
#: 360 trading days a year for the same reason: round, stated, and not pretending to know
#: the venture's closing days.
DAYS_PER_MONTH = 30


# ---------------------------------------------------------------- which answers we need --
#: A COST field that is a COMPONENT rather than the total. The preview needs the total; the
#: components make the report better and can wait until after the run has started.
_COMPONENT_COSTS = ("rent_estimate", "unit_cost")


def preview_fields(plan: list[dict], cls: dict) -> list[dict]:
    """The questions whose answers this screen's arithmetic actually consumes.

    Everything else in the pack improves the REPORT and does nothing for the PREVIEW, so
    asking it before the founder has seen anything is friction spent at the worst moment.
    Tiering is by slot KIND, not by a hardcoded field list, so a new pack gets tiered
    correctly the day it is written: a price is a price whatever the venture calls it.
    """
    out = []
    for q in plan:
        field = q["field"]
        kind = slots.kind_of_field(field)
        if kind == slots.PRICE:
            out.append(q)
        elif kind == slots.COST and field not in _COMPONENT_COSTS:
            out.append(q)
        elif kind == slots.PLACE:
            out.append(q)
        elif kind == slots.COUNT and field == "capacity" and cls.get("is_physical"):
            out.append(q)
    return out


def deferred_fields(plan: list[dict], cls: dict) -> list[dict]:
    """The rest. Asked AFTER the run starts, where they fill the six minute wait with
    something the buyer is motivated to do rather than a spinner."""
    keep = {q["field"] for q in preview_fields(plan, cls)}
    return [q for q in plan if q["field"] not in keep]


# ------------------------------------------------------------------- what would decide it --
#: Per money-kind: the three things that actually decide whether the venture works, in the
#: founder's language, each labelled with whether WE can measure it or THEY have to say.
#: No adjectives, no verdict. This is the analyst naming the right questions, which is the
#: only credential that means anything before any data exists.
_WHAT_DECIDES: dict[str, tuple[dict, ...]] = {
    "transactional": (
        {"q": "How many people are actually within walking distance",
         "who": "us", "how": f"census households inside the {TRADE_AREA_KM} km ring"},
        {"q": "What the places already there charge, and how many there are",
         "who": "us", "how": "a venue census plus prices from their own pages"},
        {"q": "Whether the volume you need to break even fits in the room",
         "who": "you", "how": "your price, your monthly cost and your seat count"},
    ),
    "subscription": (
        {"q": "How many companies of your customer's shape actually exist",
         "who": "us", "how": "census firmographics for the industry you sell into"},
        {"q": "What the incumbents charge, per seat and per account",
         "who": "us", "how": "published pricing pages, read and compared"},
        {"q": "How many accounts you need before the maths works",
         "who": "you", "how": "your price and your monthly cost"},
    ),
    "ecommerce": (
        {"q": "How large the category is and who is already winning it",
         "who": "us", "how": "category sizing plus a competitor roster"},
        {"q": "What comparable products sell for",
         "who": "us", "how": "prices read from the listings themselves"},
        {"q": "What one order actually earns you after the cost of making it",
         "who": "you", "how": "your order value and your unit cost"},
    ),
    "services": (
        {"q": "How many organisations near you buy this kind of work",
         "who": "us", "how": "census firmographics for your service area"},
        {"q": "What the going rate is",
         "who": "us", "how": "published rates and comparable engagements"},
        {"q": "How much your team can physically deliver in a month",
         "who": "you", "how": "your rate and how many people can do the work"},
    ),
    "marketplace": (
        {"q": "How big the flow of money between the two sides could be",
         "who": "us", "how": "category sizing on the transactions themselves"},
        {"q": "What the platforms already doing this keep",
         "who": "us", "how": "published take rates, compared"},
        {"q": "What you actually earn, which is your cut and not the money moving through",
         "who": "you", "how": "your take rate and your transaction size"},
    ),
    "ad_supported": (
        {"q": "What attention like yours sells for",
         "who": "us", "how": "advertising rates for the category"},
        {"q": "Who else is selling the same audience",
         "who": "us", "how": "a competitor roster and their monetisation"},
        {"q": "How large the audience has to get before any money arrives",
         "who": "you", "how": "your audience threshold and your running cost"},
    ),
    "hybrid": (
        {"q": "How big each of your two revenue lines could be",
         "who": "us", "how": "sizing run separately for the up-front and the ongoing part"},
        {"q": "What comparable products charge on each line",
         "who": "us", "how": "published pricing, split the same way"},
        {"q": "How the two lines add up against your costs",
         "who": "you", "how": "your figures for each leg and your monthly cost"},
    ),
}

_HEADLINES: dict[str, str] = {
    "transactional": ("You are a walk-in venue, so your market is a walking-distance ring, "
                      "not a city. That single fact changes every number in the report."),
    "subscription": ("You charge on a recurring basis, so what matters is how many accounts "
                     "exist to win and what each one is worth over its life."),
    "ecommerce": ("You ship products, so your market is a category and a shelf you have to "
                  "get onto, not a neighbourhood."),
    "services": ("You sell your team's time, so your ceiling is the hours your people can "
                 "bill, and no amount of demand moves it."),
    "marketplace": ("You keep a cut of other people's transactions, so the money moving "
                    "through you is not the money you earn. Those get counted separately."),
    "ad_supported": ("Your users do not pay, so the report prices your ADVERTISERS and "
                     "sizes the audience you need before any revenue starts."),
    "hybrid": ("You charge in more than one way, so each revenue line gets sized and "
               "costed on its own before they are added up."),
}


# --------------------------------------------------------------------------- arithmetic --
def _price_slot(ex: dict) -> tuple[Optional[float], Optional[dict], Optional[str]]:
    """The founder's price, as a PRICE. Returns (value, slot, field).

    expect=PRICE is doing real work here. The whole reason this codebase has typed slots is
    that a monthly OPERATING COST once won a search like this one and got published as the
    venture's price with a "-95%" banner attached. A cost cannot win here, whatever field
    it was filed under.
    """
    for field in ("avg_ticket", "avg_order", "avg_transaction", "rate_basis", "pricing"):
        value = slots.number_in(ex, field, expect=slots.PRICE)
        if value and value > 0:
            return value, slots.slot_in(ex, field), field
    return None, None, None


def _monthly_cost(ex: dict) -> Optional[float]:
    value = slots.number_in(ex, "monthly_cost_estimate", expect=slots.COST)
    return value if value and value > 0 else None


def break_even(ex: dict) -> Optional[dict]:
    """The one number most founders have never worked out, from two they just gave us.

    Deliberately the crudest honest form: monthly cost divided by price, with NO cost of
    goods, because nobody has been asked for cost of goods yet. `ignores` says so in the
    payload rather than in a footnote, and the report is where it gets done properly. A
    preview that quietly assumed 100% margin and did not say so would be the same species
    of lie as the defects this product exists to avoid.
    """
    price, slot, field = _price_slot(ex)
    cost = _monthly_cost(ex)
    if not price or not cost:
        return None
    period = (slot or {}).get("period")
    unit = (slot or {}).get("unit") or "sale"
    if period == "month":
        # A recurring price: the question is how many customers, not how many visits.
        customers = math.ceil(cost / price)
        return {
            "shape": "recurring",
            "units": customers,
            "unit_noun": "paying customers",
            "per": "month",
            "price": price, "monthly_cost": cost, "price_field": field,
            "workings": f"${cost:,.0f} a month divided by ${price:,.2f} a month",
            "ignores": ("what it costs you to serve each account, which the report "
                        "prices properly"),
        }
    per_month = math.ceil(cost / price)
    per_day = per_month / DAYS_PER_MONTH
    return {
        "shape": "unit",
        "units": per_month,
        "unit_noun": slots._plural(unit, 2),
        "per": "month",
        "per_day": round(per_day, 1),
        "price": price, "monthly_cost": cost, "price_field": field,
        "workings": (f"${cost:,.0f} a month divided by ${price:,.2f} a {unit}, "
                     f"then across {DAYS_PER_MONTH} days"),
        "ignores": ("what each one costs you to make, which the report prices properly. "
                    "The real figure will be higher"),
    }


def capacity_ceiling(ex: dict, category: str | None) -> Optional[dict]:
    """What the room can physically serve in a day, and the assumptions behind it.

    Reuses plan._fermi_service_model, so the free screen and the paid report make the SAME
    assumption about turns and service hours. A preview that used different arithmetic to
    the report would be a demo of a different product.
    """
    seats = slots.number_in(ex, "capacity", expect=slots.COUNT)
    if not seats or seats <= 0:
        return None
    from plan import _fermi_service_model
    model = _fermi_service_model(category or slots.text_in(ex, "product"))
    per_day = round(seats * model["turns_hr"] * model["hours"])
    return {
        "per_day": per_day,
        "seats": int(seats),
        "workings": (f"{seats:.0f} {model['station_noun']} x {model['turns_hr']} turns an "
                     f"hour x {model['hours']} {model['hours_label']}"),
        "unit_noun": model["unit_noun"],
    }


def utilisation(be: dict | None, ceiling: dict | None) -> Optional[dict]:
    """Break-even as a share of what the room holds. The line that lands: not "you need 56
    a day" but "you need 56 a day out of a possible 270, so a fifth of capacity, every day".
    Stated as a fact with its inputs, never as a verdict."""
    if not be or not ceiling or be.get("shape") != "unit" or not ceiling.get("per_day"):
        return None
    share = be["per_day"] / ceiling["per_day"]
    return {
        "share": round(share, 3),
        "percent": round(share * 100),
        "over_capacity": share > 1,
        "workings": f"{be['per_day']:,.0f} a day against about "
                    f"{ceiling['per_day']:,} the room can serve",
    }


# ------------------------------------------------------------------------ the whole card --
def build(session: dict, plan: list[dict], cls: dict) -> dict:
    """Everything the free screen renders. No model calls, no network, no cost."""
    ex = (session or {}).get("extracted") or {}
    kind = cls.get("kind") or "transactional"
    category = slots.text_in(ex, "product")

    be = break_even(ex)
    ceiling = capacity_ceiling(ex, category)
    util = utilisation(be, ceiling)

    # Missing means NEVER ANSWERED. A declared "not sure" is an answer: the founder was
    # asked, said they do not know, and the report discloses it as an assumption. Counting
    # that as missing would re-ask forever, which is the behaviour rule 3 of the tree
    # exists to prevent.
    from intake_tree import is_unknown
    needed = preview_fields(plan, cls)
    missing_fields = [q["field"] for q in needed
                      if not slots.text_in(ex, q["field"])
                      and not is_unknown(ex.get(q["field"]))]

    # THE LIMITS, stated on the screen where a buyer decides whether to trust us. Every one
    # is a thing we genuinely cannot know yet, not a feature held back for effect.
    limits = []
    if be:
        limits.append(be["ignores"])
    if not be:
        limits.append("We cannot work out your break-even until we have both a price and a "
                      "monthly running cost")
    place = slots.text_in(ex, "site") or slots.text_in(ex, "geography")
    # A CITY IS NOT A SITE. The same predicate the confirmation card uses, because a bare
    # "Mission District, San Francisco" does not get a trade area: size_hyperlocal needs a
    # corner, and without one the run sizes the whole city. Promising a 1.5 km ring around
    # a neighbourhood would be the free screen describing a report the paid run will not
    # produce, which is the worst possible place to be inaccurate.
    from brief import _SITE_PRECISE_RE
    precise_site = bool(place) and bool(_SITE_PRECISE_RE.search(place))
    if cls.get("is_physical"):
        if precise_site:
            limits.append(f"Everything local is counted inside a {TRADE_AREA_KM} km ring "
                          f"around {place}, so a different corner gives different numbers")
        else:
            limits.append("Without a specific corner we size the whole city instead of a "
                          "trade area, which is a much rougher number and gets labelled "
                          "as one")
    if cls.get("non_us"):
        limits.append("Household spending data is US only, so for your location we "
                      "estimate it and say so rather than quoting it as measured")

    # THE KIND IS SHOWN AS A READING, NEVER AS AN ASSERTION, and it is always one click
    # from being corrected. Asking the fork only when the classifier is unsure is not
    # enough of a guard: MEASURED, a B2B scheduling tool was classified a walk-in venue
    # with explicit=True and needs_fork=False, so the founder would have been told
    # confidently and wrongly, on the first screen, that their market is a 1.5 km ring.
    # Wrong and correctable costs nothing. Wrong and assertive costs the sale.
    # PROVENANCE IS ABOUT THIS KIND, NOT ABOUT EXPLICITNESS IN GENERAL. `cls["explicit"]`
    # means the founder named SOME payment mechanism, which is not the same as naming THIS
    # one. MEASURED: "a scheduling tool for small physio clinics, they pay monthly per
    # practitioner" set explicit=True because "pay monthly" is payment language, the
    # classifier then read it as pay-per-visit off the word "clinic", and the card labelled
    # that reading "you said this". The founder had said the opposite. Only a pick from the
    # closed set counts as stated.
    from intake_tree import KIND_OPTIONS, stated_kind
    picked = stated_kind(ex)
    return {
        "kind": kind,
        "kind_said": _kind_in_words(kind),
        "provenance": "stated" if picked else "inferred",
        "confident": not cls.get("needs_fork"),
        "kind_options": list(KIND_OPTIONS),
        "reading": f"We read this as: {_kind_in_words(kind)}.",
        "headline": _HEADLINES.get(kind, _HEADLINES["transactional"]),
        "decides": list(_WHAT_DECIDES.get(kind, _WHAT_DECIDES["transactional"])),
        "place": place or None,
        "trade_area_km": TRADE_AREA_KM if cls.get("is_physical") else None,
        "break_even": be,
        "ceiling": ceiling,
        "utilisation": util,
        "limits": limits,
        "needs": [{"field": q["field"], "question": q["question"]} for q in needed],
        "missing": missing_fields,
        "has_arithmetic": bool(be or ceiling),
    }


def _kind_in_words(kind: str) -> str:
    from intake_tree import KIND_IN_FOUNDER_WORDS
    return KIND_IN_FOUNDER_WORDS.get(kind, kind)
