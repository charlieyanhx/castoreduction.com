"""intake_tree.py — the next intake question depends on what kind of business this is.

WHY THIS EXISTS. Intake used to ask every venture the same eight fields and let a generic
LLM pick the next question; the only branch in the flow was one site-marker check. But the
pipeline classifies every venture into seven money-kinds and five market scales, and each
cell's ARITHMETIC consumes different facts: a cafe's ladder needs seats and a ticket price,
a SaaS ladder needs to know whether $1,450/mo is per person or per company (the
100-seats/month stock defect), a marketplace needs the take rate (the C10 guard was built,
documented, and never once given an input), a chain needs its location count, and a free
product must never be asked for a price (C7 shipped a validated three-tier price deck on
one). Those classifiers ran AFTER the founder was gone. This module runs them DURING the
conversation and lets their output pick the next question.

THE TREE IS CODE; THE LLM ONLY EXTRACTS. Questions live here as literals — deterministic,
testable, jargon-lintable — and the classifiers they branch on are the pipeline's own
(business_model.classify_with_confidence, and the deterministic override helpers from
skills.sizing.classify), so intake and pipeline cannot disagree about what a venture is.

THREE RULES, each earned by a shipped defect:
  1. THE FOUNDER NEVER SEES THE TAXONOMY. Every question is plain language with a concrete
     anchor ("monthly like Netflix", "a cut like Uber"). The orbital brief answered our
     "business model" field with "Undetermined" — our word, their confusion.
  2. LOW CONFIDENCE FORKS OUT LOUD. When the kind classifier is unsure, the next question
     IS the disambiguation, asked in examples. "Undetermined" silently became subscription
     and an entire seat-priced report followed (job d62bc04f).
  3. "NOT SURE" IS AN ANSWER. It records {"unknown": True} — an assumption the report must
     disclose — instead of forcing fake precision or re-asking forever.

WIRING HONESTY (the C10 lesson: a fact nobody consumes is collection theatre). Every pack
entry declares its consumer:
    consumer_kind="module"  a named function reads this field's value directly, or reads
                            the phrasing _synthesize_from_extracted emits for it
                            (brief.extract_price, plan.extract_location_count, ...)
    consumer_kind="brief"   the fact rides final_description as context for downstream
                            prompts — honest, useful, but NOT deterministically consumed
"""
from __future__ import annotations

import re
from typing import Any, Optional

import slots
from business_model import classify_with_confidence


# ------------------------------------------------------------------ "not sure" handling --
UNKNOWN = {"unknown": True}


def mark_unknown(extracted: dict, field: str) -> dict:
    """Record that the founder was asked and does not know. This is an ANSWER: the field
    stops being asked, and the report gains a labeled assumption instead of a silent
    default."""
    extracted[field] = dict(UNKNOWN)
    return extracted


def is_unknown(value: Any) -> bool:
    return isinstance(value, dict) and value.get("unknown") is True


def _answered(value: Any) -> bool:
    """Answered means usable-or-declined: a real value, or an explicit 'not sure'."""
    return value not in (None, "", []) or is_unknown(value)


# A quick pre-classifier textual signal for "not sure" arriving as prose.
_NOT_SURE_RE = re.compile(
    r"^\s*(?:i\s*)?(?:don'?t|do not|not)\s*(?:know|sure)|^\s*no idea|^\s*unsure|"
    r"^\s*skip\b|^\s*haven'?t decided", re.I)


def utterance_is_not_sure(text: str) -> bool:
    return bool(_NOT_SURE_RE.search(text or ""))


# Payment MECHANISM language in the founder's own words. Deliberately excludes bare
# numbers, currency, and periods-without-mechanism ("40 per day" answers a volume
# question, not a model question). MEASURED origin (taco-stand transcript, 2026-08-20):
# the extractor invented business_model="DTC / Food service / Retail stand" from a brief
# that said nothing about payment, and the classifier read the fabricated keywords as a
# STATED model — explicit=True, no fork, wrong pack.
_PAYMENT_WORDS_RE = re.compile(
    r"\b(pay(?:s|ing)?|paid|charge[sd]?|charging|subscri(?:be|bes|ption|bers?)|"
    r"per\s+(?:visit|item|order|month|year|hour|project|seat|user|unit|session|class|"
    r"taco|drink|cup|meal|ride)|one.?time|recurring|membership|fees?|commission|"
    r"take\s+a\s+cut|cut\s+of|free\s+for|advertis\w*|sponsors?\w*|tips?|donations?)\b",
    re.I)


def founder_payment_words(text: str | None) -> bool:
    """Did the FOUNDER name a payment mechanism, in their own words? The gate that stops
    the extractor's paraphrase from manufacturing explicitness."""
    return bool(_PAYMENT_WORDS_RE.search(text or ""))


# ------------------------------------------------------------------------- the question --
def _q(field: str, question: str, drives: str, consumer: str,
       consumer_kind: str = "module", **presentation) -> dict:
    """A planned question. `presentation` carries the form contract the client renders
    from (Wave D): input_kind (text | number | choice | location), options, write_in,
    unit_hint, optional, period_choices. Defaults keep every question a plain text ask."""
    out = {"field": field, "question": question, "drives": drives,
           "consumer": consumer, "consumer_kind": consumer_kind,
           "input_kind": "text", "optional": False}
    out.update(presentation)
    return out


# The form spec per field, applied in plan_questions so the packs stay about CONTENT and
# this table stays the one place the input contract lives. A field absent here keeps the
# plain text ask.
_INPUT_SPECS: dict[str, dict] = {
    "avg_ticket": {"input_kind": "number", "unit_hint": "$ per visit"},
    "avg_order": {"input_kind": "number", "unit_hint": "$ per order"},
    "avg_transaction": {"input_kind": "number", "unit_hint": "$ per transaction"},
    "rate_basis": {"input_kind": "number", "unit_hint": "$ per hour or per project"},
    "take_rate": {"input_kind": "number", "unit_hint": "% you keep"},
    "capacity": {"input_kind": "number", "unit_hint": "seats or stations"},
    "seats_per_account": {"input_kind": "number", "unit_hint": "people per customer"},
    "rent_estimate": {"input_kind": "number", "unit_hint": "$ per month"},
    "monthly_cost_estimate": {"input_kind": "number", "unit_hint": "$ per month"},
    "audience_threshold": {"input_kind": "number", "unit_hint": "users"},
    "expected_volume": {"input_kind": "number", "unit_hint": "sales",
                        "period_choices": ["per day", "per week", "per month"]},
    # A recurring price is meaningless without its period: $49 a month and $49 a year are
    # different businesses. The period is COLLECTED here rather than sniffed out of the
    # founder's phrasing downstream, which is the whole point of the typed slot.
    "pricing": {"input_kind": "number", "unit_hint": "$ per customer",
                "period_choices": ["per month", "per year"]},
    "site": {"input_kind": "location"},
    "named_competitors": {"optional": True},
    "status_quo": {"optional": True},
    # Extended model type fields
    "hourly_rate": {"input_kind": "number", "unit_hint": "$ per hour"},
    "utilization_pct": {"input_kind": "number", "unit_hint": "%"},
    "weekly_capacity_hours": {"input_kind": "number", "unit_hint": "hours per week"},
    "retainer_amount": {"input_kind": "number", "unit_hint": "$ per month"},
    "included_hours": {"input_kind": "number", "unit_hint": "hours/month", "optional": True},
    "overage_rate": {"input_kind": "number", "unit_hint": "$ per hour", "optional": True},
    "take_rate_pct": {"input_kind": "number", "unit_hint": "% commission"},
    "avg_sale_value": {"input_kind": "number", "unit_hint": "$ per sale"},
    "wholesale_price": {"input_kind": "number", "unit_hint": "$ per unit"},
    "retail_price": {"input_kind": "number", "unit_hint": "$ per unit", "optional": True},
    "retailer_margin_pct": {"input_kind": "number", "unit_hint": "%"},
    "paid_arpu": {"input_kind": "number", "unit_hint": "$ per month"},
    "conversion_rate_pct": {"input_kind": "number", "unit_hint": "%"},
    "free_to_paid_months": {"input_kind": "number", "unit_hint": "months"},
    "hardware_price": {"input_kind": "number", "unit_hint": "$"},
    "hardware_cost": {"input_kind": "number", "unit_hint": "$"},
    "consumable_price": {"input_kind": "number", "unit_hint": "$"},
    "consumable_cost": {"input_kind": "number", "unit_hint": "$"},
    "consumables_per_year": {"input_kind": "number", "unit_hint": "per year"},
    "avg_comparable_price": {"input_kind": "number", "unit_hint": "$"},
    "auction_fee_pct": {"input_kind": "number", "unit_hint": "%"},
    "seller_premium_pct": {"input_kind": "number", "unit_hint": "%"},
    "capacity_units": {"input_kind": "number", "unit_hint": "units"},
    "avg_rate_usd": {"input_kind": "number", "unit_hint": "$ per booking"},
    "avg_utilization_pct": {"input_kind": "number", "unit_hint": "%"},
    "avg_outcome_value": {"input_kind": "number", "unit_hint": "$"},
    "success_fee_pct": {"input_kind": "number", "unit_hint": "%"},
    "win_rate_pct": {"input_kind": "number", "unit_hint": "%"},
    "anchor_price": {"input_kind": "number", "unit_hint": "$ list price"},
    "sell_price": {"input_kind": "number", "unit_hint": "$ actual price"},
    "avg_discount_pct": {"input_kind": "number", "unit_hint": "%"},
}


def _apply_input_specs(plan: list[dict]) -> list[dict]:
    return [dict(q, **_INPUT_SPECS.get(q["field"], {})) for q in plan]


# THE CORE PACK — founder-only facts every venture is asked, because the pipeline cannot
# fetch what is in the founder's head and today it GUESSES each of these:
#   status quo        -> EVC invented "grid power purchase agreements" as the alternative
#   monthly cost      -> break-even shipped on an LLM-guessed $85,000/mo fixed cost
#   customer evidence -> the entire WTP was a simulated 40-buyer panel
#   competitors       -> discovery fabricated three brands on one domain
#   success target    -> the S-curve targets were derived from nothing the founder said
CORE_PACK: tuple[dict, ...] = (
    _q("status_quo",
       "Worth a thought if you have one: what do your customers do today instead, before "
       "you exist? Even 'nothing, they just put up with it' is a real answer, and it "
       "sharpens the whole value story.",
       "what we compare your price against; the value story is built on this",
       "economics.compute_evc reference alternative", "brief"),
    _q("monthly_cost_estimate",
       "Roughly what will it cost you to run each month? Even 'just me and a laptop' or "
       "a single rent guess counts. Not sure is fine too; we will use a benchmark range "
       "and label it.",
       "the break-even line; without your number we use a labeled benchmark",
       "financials break-even fixed cost", "brief"),
    _q("customer_evidence",
       "What feedback have you gotten so far, if any? A conversation, a waitlist "
       "signup, a sale, anything counts. None yet is a perfectly good answer.",
       "how much weight the price findings deserve; one real quote beats a simulation",
       "pricing WTP anchor + validation flags", "brief"),
    _q("named_competitors",
       "A bonus if you know one: which company does the closest thing to this? Most "
       "founders can't name one and that's fine, but even a single name anchors the "
       "whole competitive section.",
       "the competitor list starts from real names instead of guesses",
       "discover._union_named_competitors", "module"),
    _q("success_target",
       "If this works, what does the first year look like? A rough revenue figure or "
       "customer count you'd be happy with.",
       "whether the market the report finds is big enough for YOUR goal, not a generic one",
       "viability framing", "brief"),
)


# KIND PACKS — what the money-kind's arithmetic actually consumes.
KIND_PACKS: dict[str, tuple[dict, ...]] = {
    "transactional": (
        _q("capacity",
           "How many people could you serve at once? Seats, chairs, stations.",
           "the ceiling on daily sales; targets above what the room holds are fantasy",
           "business_model capacity checks", "brief"),
        _q("avg_ticket",
           "Roughly what does one visit cost a customer?",
           "every volume figure: daily targets, break-even, the whole ladder",
           "brief.extract_price via the synthesized brief", "module"),
        _q("rent_estimate",
           "What might the space cost you monthly? A guess from local listings is fine.",
           "the largest cost in the break-even math; today we guess it if you don't say",
           "financials break-even fixed cost", "brief"),
    ),
    "subscription": (
        # THE AMOUNT, not just its shape. This pack asked whether the fee is per company
        # or per person and never once asked what the fee IS, so a subscription venture
        # reached the run with a price basis and no price: nothing downstream could state
        # a break-even, and the free preview had nothing to show. Asking the scope of a
        # number without asking the number is the mirror image of the C10 defect, where an
        # input was consumed by nothing. Here a consumer waited for an input nobody asked.
        _q("pricing",
           "What does one customer pay you, and how often?",
           "every revenue figure, and the break-even line the whole report hangs off",
           "brief.extract_price via the synthesized brief", "module"),
        _q("pricing_unit_scope",
           "When a customer pays the monthly fee, is that for the whole company, or per "
           "person using it?",
           "the difference between 100 customers and 100 individual users; it changes "
           "every projection",
           "financials.ladder_inputs price basis", "brief"),
        _q("seats_per_account",
           "If it's per person: how many people at one customer would typically use it?",
           "what one customer is actually worth",
           "account value in financials", "brief"),
        _q("sales_motion",
           "Will you sell by talking to each customer yourself, or do they find it and "
           "sign up on their own?",
           "how fast customers can realistically arrive, and what each one costs to win",
           "GTM feasibility in viability", "brief"),
    ),
    "ecommerce": (
        _q("avg_order",
           "What does a typical order cost the customer?",
           "every revenue figure",
           "brief.extract_price via the synthesized brief", "module"),
        _q("unit_cost",
           "What does it cost YOU to make and ship one order, roughly?",
           "the profit on each sale; without it margins are a guess, and labeled as one",
           "financials margin inputs", "brief"),
        _q("channel",
           "Selling from your own website, through Amazon, or in shops?",
           "who takes a cut before you, and how customers find you",
           "place/GTM context", "brief"),
    ),
    "services": (
        _q("team_size",
           "How many people can actually do the work? Is it just you?",
           "the hard ceiling on revenue; hours don't scale past the people who bill them",
           "financials.capacity_withhold_reason", "module"),
        _q("rate_basis",
           "Do you charge by the hour, by the project, or a monthly amount? Roughly how "
           "much, for a typical job?",
           "every revenue figure, and how long each job ties a person up",
           "brief.extract_price via the synthesized brief", "module"),
    ),
    "marketplace": (
        _q("take_rate",
           "When someone pays $100 through you, how much do you keep?",
           "your actual revenue; the money moving through you is not the money you earn",
           "the GMV-vs-revenue guard (C10), built for exactly this input", "brief"),
        _q("side_first",
           "Which side do you need first: the people selling, or the people buying? "
           "Which is harder to get?",
           "the chicken-and-egg risk every marketplace lives or dies on",
           "viability risk framing", "brief"),
        _q("avg_transaction",
           "Roughly how big is one transaction through the platform?",
           "the volume the market size translates into",
           "brief.extract_price via the synthesized brief", "module"),
    ),
    "ad_supported": (
        _q("payer",
           "If users don't pay, who does? Advertisers, sponsors, someone else?",
           "where the money actually comes from; the report prices to THEM, not to users",
           "business_model.venture_has_a_customer_price routing", "brief"),
        _q("audience_threshold",
           "Roughly how many users would you need before that money starts arriving?",
           "the gap between launch and first revenue",
           "financials ramp context", "brief"),
    ),
    "hybrid": (
        _q("hybrid_legs",
           "So customers pay more than one way. Say the up-front part and the ongoing "
           "part separately, with rough numbers for each?",
           "both revenue streams get computed; last time the ongoing one was promised in "
           "prose and never made it into the math",
           "the C11 recurring-leg computation", "brief"),
    ),
    "hourly": (
        _q("hourly_rate",
           "What is your rate? (per hour or per day — say which)",
           "the core revenue driver; every break-even and projection hangs off this",
           "business_model.hourly_economics hourly_rate", "module",
           input_kind="number", unit_hint="$ per hour"),
        _q("utilization_pct",
           "What % of working hours do you expect to bill?",
           "the utilization assumption the break-even is computed at",
           "business_model.hourly_economics utilization_pct", "module",
           input_kind="number", unit_hint="%"),
        _q("weekly_capacity_hours",
           "How many hours per week are available to bill?",
           "the capacity ceiling; revenue cannot exceed rate × capacity × utilization",
           "business_model.hourly_economics weekly_capacity_hours", "module",
           input_kind="number", unit_hint="hours per week"),
    ),
    "retainer": (
        _q("retainer_amount",
           "What is the monthly retainer fee?",
           "the committed recurring revenue the whole model rests on",
           "financials projection retainer_amount", "module",
           input_kind="number", unit_hint="$ per month"),
        _q("included_hours",
           "How many hours are included in the retainer, if any?",
           "the implied hourly rate and value framing for the client",
           "break-even context included_hours", "brief",
           input_kind="number", unit_hint="hours/month", optional=True),
        _q("overage_rate",
           "What is the hourly overage rate if included hours are exceeded?",
           "upside revenue beyond the base retainer",
           "overage revenue context overage_rate", "brief",
           input_kind="number", unit_hint="$ per hour", optional=True),
    ),
    "consignment": (
        _q("take_rate_pct",
           "What % of each sale do you keep?",
           "your actual revenue per transaction — the GMV belongs to the consignor",
           "business_model.consignment_economics take_rate_pct", "module",
           input_kind="number", unit_hint="% commission"),
        _q("avg_sale_value",
           "What is the average value of a consignment sale?",
           "the transaction size the break-even and projections are built around",
           "business_model.consignment_economics avg_sale_value", "module",
           input_kind="number", unit_hint="$ per sale"),
    ),
    "wholesale": (
        _q("wholesale_price",
           "What price do you sell to retailers or distributors at?",
           "your actual revenue per unit — the retail price is the retailer's income, not yours",
           "business_model.wholesale_economics wholesale_price", "module",
           input_kind="number", unit_hint="$ per unit"),
        _q("retail_price",
           "What is the expected retail (end-consumer) price?",
           "context only — sets the retailer margin implied by your wholesale price",
           "business_model.wholesale_economics retail_price", "brief",
           input_kind="number", unit_hint="$ per unit", optional=True),
        _q("retailer_margin_pct",
           "What margin does the retailer take, roughly?",
           "sanity-check on the wholesale/retail spread; flags if the math doesn't work",
           "wholesale margin context retailer_margin_pct", "brief",
           input_kind="number", unit_hint="%"),
    ),
    "freemium": (
        _q("paid_arpu",
           "What is the monthly price of the paid tier?",
           "the revenue per converting user; the whole freemium model hinges on this",
           "business_model.freemium_economics paid_arpu", "module",
           input_kind="number", unit_hint="$ per month"),
        _q("conversion_rate_pct",
           "What % of free users do you expect to convert to paid?",
           "the conversion rate the break-even total-user count is derived from",
           "business_model.freemium_economics conversion_rate_pct", "module",
           input_kind="number", unit_hint="%"),
        _q("free_to_paid_months",
           "On average, how many months until a free user converts?",
           "the payback period for the free-tier acquisition cost",
           "business_model.freemium_economics free_to_paid_months", "brief",
           input_kind="number", unit_hint="months"),
    ),
    "razor_blades": (
        _q("hardware_price",
           "What is the price of the device or platform?",
           "the upfront hardware margin (may be a loss leader)",
           "business_model.razor_blades_economics hardware_price", "module",
           input_kind="number", unit_hint="$"),
        _q("hardware_cost",
           "What does the device or platform cost you to make?",
           "the hardware margin — negative means loss leader",
           "business_model.razor_blades_economics hardware_cost", "module",
           input_kind="number", unit_hint="$"),
        _q("consumable_price",
           "What is the price of the consumable or refill?",
           "the recurring revenue driver; this is where the model actually makes money",
           "business_model.razor_blades_economics consumable_price", "module",
           input_kind="number", unit_hint="$"),
        _q("consumable_cost",
           "What does the consumable cost you?",
           "the consumable margin per unit",
           "business_model.razor_blades_economics consumable_cost", "module",
           input_kind="number", unit_hint="$"),
        _q("consumables_per_year",
           "How many consumables does a typical customer buy per year?",
           "the annual recurring margin per installed customer",
           "business_model.razor_blades_economics consumables_per_year", "module",
           input_kind="number", unit_hint="per year"),
    ),
    "auction": (
        _q("avg_comparable_price",
           "What do comparable items typically sell for at auction?",
           "the expected clearing price; used as the base for fee calculations",
           "business_model.auction_economics avg_comparable_price", "module",
           input_kind="number", unit_hint="$"),
        _q("auction_fee_pct",
           "What % fee does the auction platform take?",
           "the platform's cut of the hammer price",
           "business_model.auction_economics auction_fee_pct", "module",
           input_kind="number", unit_hint="%"),
        _q("seller_premium_pct",
           "What seller's premium do you charge, if any?",
           "additional revenue on top of the base fee",
           "business_model.auction_economics seller_premium_pct", "module",
           input_kind="number", unit_hint="%"),
    ),
    "dynamic": (
        _q("capacity_units",
           "How many bookable units do you have (rooms, seats, slots, etc.)?",
           "the revenue ceiling at full utilization",
           "business_model.dynamic_economics capacity_units", "module",
           input_kind="number", unit_hint="units"),
        _q("avg_rate_usd",
           "What is your average rate per unit when booked?",
           "the average yield across demand conditions",
           "business_model.dynamic_economics avg_rate_usd", "module",
           input_kind="number", unit_hint="$ per booking"),
        _q("avg_utilization_pct",
           "What is your expected average utilization or occupancy?",
           "the utilization assumption the revenue projection is built around",
           "business_model.dynamic_economics avg_utilization_pct", "module",
           input_kind="number", unit_hint="%"),
    ),
    "performance": (
        _q("avg_outcome_value",
           "What is the average value of a successful outcome (deal size, settlement, etc.)?",
           "the base from which your fee is calculated",
           "business_model.performance_economics avg_outcome_value", "module",
           input_kind="number", unit_hint="$"),
        _q("success_fee_pct",
           "What % of the outcome value is your fee?",
           "the fee rate applied to wins to get expected revenue per engagement",
           "business_model.performance_economics success_fee_pct", "module",
           input_kind="number", unit_hint="%"),
        _q("win_rate_pct",
           "What % of engagements result in a successful outcome?",
           "the win rate that converts a potential fee into expected revenue",
           "business_model.performance_economics win_rate_pct", "module",
           input_kind="number", unit_hint="%"),
    ),
    "anchor_discount": (
        _q("anchor_price",
           "What is your reference or list price (the 'was' price)?",
           "the anchor from which the discount is computed; economics run on the sell price",
           "business_model.anchor_discount_economics anchor_price", "module",
           input_kind="number", unit_hint="$ list price"),
        _q("sell_price",
           "What is your actual selling price?",
           "the price economics are computed at — not the anchor",
           "business_model.anchor_discount_economics sell_price", "module",
           input_kind="number", unit_hint="$ actual price"),
        _q("avg_discount_pct",
           "What is the typical discount from list price? (confirm — derived from the prices above)",
           "the positioning mechanic; does not change the economics but frames the value story",
           "anchor discount context avg_discount_pct", "brief",
           input_kind="number", unit_hint="%"),
    ),
}


# ------------------------------------------------------------------ scale + modifiers --
# Mirrors intake._SITE_MARKERS (the battle-tested one): a bare digit counts — street
# numbers and ordinals ("NW 23rd") are the most common site signal, and the first draft's
# \d{1,5}\s+\w+ missed exactly them because ordinals have no space after the digits.
_SITE_RE = re.compile(
    r"\d|\bcorner\b|\bcross.?street|\bneighborhood\b|\bneighbourhood\b|"
    r"\bavenue\b|\bstreet\b|\bave\b|\bblvd\b|\broad\b|\brd\b|\bplaza\b|"
    r"\bdistrict\b|\bdowntown\b|\buptown\b|\bnw\b|\bne\b|\bsw\b|\bse\b", re.I)

_US_HINTS = re.compile(
    r"\b(us|usa|u\.s\.|united states|america)\b|"
    r"\b(al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|mn|ms|mo|mt|"
    r"ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|va|wa|wv|wi|wy)\b", re.I)

_LAUNCHED_RE = re.compile(r"\blaunch(?:ed)?\b|\blive\b|\bcustomers? already\b|\brevenue\b|"
                          r"\bgrowing\b|\bopen(?:ed)?\b|\bscaling\b", re.I)
_IDEA_RE = re.compile(r"\bidea\b|\bconcept\b|\bpre.?launch\b|\bnot (?:yet )?launch", re.I)

# Categories whose viability turns on approvals a founder often already knows about. The
# orbital report's FAA/FCC section was entirely model-recalled — right by luck, unanchored.
_REGULATED_RE = re.compile(
    r"health|medic|clinic|pharma|therap|diagnos|finance|financ|lend|insur|invest|bank|"
    r"crypto|food|beverage|alcohol|cannabis|child|kids|daycare|school|aviation|aerospace|"
    r"space|satellite|orbital|drone|energy|utility|utilities|weapon|firearm|legal advice",
    re.I)

MODIFIER_PACKS: dict[str, dict] = {
    "site": _q("site",
               "Which corner? A neighbourhood or cross-streets is enough. 'Melrose and "
               "Fairfax' beats 'Los Angeles'. A zip, a street address, or just the city "
               "all work too; I'll check what it resolves to. If you haven't picked yet, "
               "say so and I'll size the whole city so you can compare corners later.",
               "the walking-distance ring every local market figure is built from; "
               "without a corner you get the city-wide report instead",
               "plan.extract_location -> size_hyperlocal / size_citywide", "module"),
    "locations_count": _q("locations_count",
                          "How many locations are you planning, and where's the first one?",
                          "whether we size one neighbourhood or the whole footprint",
                          "plan.extract_location_count -> size_regional", "module"),
    "expected_volume": _q("expected_volume",
                          "On a normal day, how many sales do you expect, per machine or "
                          "location? Your own guess. It's printed as the founder's "
                          "estimate next to our model, never mixed in silently.",
                          "the reality check beside the model's volume estimate; D59 "
                          "blocks reports that hide this disagreement",
                          "size_hyperlocal som_anchor alternative", "module"),
    "local_anchor": _q("local_anchor",
                       "One honest limitation: our household-spending data is US-only, so "
                       "for your location we'll say so and estimate. Do you happen to know "
                       "any local figure, like what people typically spend on this in "
                       "your city?",
                       "replaces a US-average stand-in with a real local number",
                       "hyperlocal.spend_provenance disclosure", "brief"),
    "real_traction": _q("real_traction",
                        "You're already live. What are the real numbers so far? Customers, "
                        "monthly revenue, anything you track.",
                        "real figures anchor every projection; 15 actual customers beat "
                        "any forecast",
                        "SOM anchor context", "brief"),
    "regulatory": _q("regulatory",
                     "Are there rules, licenses or approvals you already know you'll need?",
                     "the risk section states what YOU know instead of guessing from afar",
                     "viability risks + kill criteria", "brief"),
}


# The taxonomy in the founder's words, ONE place (intake.py's card imports this). The
# card and the fork must never say "subscription" or "transactional": the orbital
# founder answered our vocabulary with "Undetermined".
KIND_IN_FOUNDER_WORDS = {
    "transactional": "customers pay per visit or per item, like a shop",
    "subscription": "customers pay a recurring fee, like Netflix",
    "ecommerce": "customers buy products you ship, like an online store",
    "services": "customers pay for your team's time, like a contractor",
    "marketplace": "you keep a cut of sales between other people, like Uber",
    "ad_supported": "free for users; advertisers or sponsors pay",
    "hybrid": "customers pay in more than one way (an up-front part and an ongoing part)",
}

# Wave D: the fork is a choice, not an essay. Options in founder words with a write-in
# escape, because a founder who knows very little should recognise their answer, not
# compose it.
KIND_OPTIONS = [{"value": k, "label": v} for k, v in KIND_IN_FOUNDER_WORDS.items()]

_KIND_BY_LABEL = {v.lower(): k for k, v in KIND_IN_FOUNDER_WORDS.items()}


def stated_kind(extracted: dict | None) -> Optional[str]:
    """The money-kind the founder PICKED, when they picked one from the closed set.

    MEASURED (2026-08-26): the fork's answer was written into business_model and then
    re-classified by running keywords over it, and the option's own label does not
    classify. "you keep a cut of sales between other people, like Uber" came back
    `subscription, explicit=False`, so needs_fork stayed True and the money question was
    asked again on every single turn, forever, no matter what the founder chose. A closed
    set has an answer; re-deriving it from the text of the answer is the re-extraction
    defect in its purest form.

    A write-in matches nothing here and correctly falls through to the classifier.
    """
    raw = slots.text((extracted or {}).get("kind_fork")).strip().lower()
    if not raw:
        return None
    return raw if raw in KIND_IN_FOUNDER_WORDS else _KIND_BY_LABEL.get(raw)


# ------------------------------------------------------------------------ classification --
_KIND_EXAMPLES = (
    "buy something once (like a shop or a device)",
    "pay monthly (like Netflix)",
    "pay for your time, per project or per hour (like a contractor)",
    "you keep a cut of each sale between other people (like Uber)",
    "free for users, with someone else paying (like ads)",
)

FORK_QUESTION = ("One thing I want to get right, since it changes all the math: how will "
                 "the money come in? For example: customers "
                 + "; ".join(_KIND_EXAMPLES)
                 + ". Or tell me if you haven't decided yet.")


def _blob(extracted: dict) -> str:
    """The venture as the PIPELINE will read it — same phrasing _synthesize_from_extracted
    emits ("Located in X.", "Business model: Y."), because the deterministic helpers key on
    those forms. Classifying a different string than the one the run will receive is how a
    preview and a pipeline drift apart; classifying the same string makes drift impossible.
    """
    ex = extracted or {}
    def _v(k):
        # slots.phrase, never the raw value. This string is the CLASSIFIER'S INPUT, so a
        # typed record interpolated here would inject the literal tokens value/unit/period/
        # kind/source into it, and a unit of "$ per month" carries the recurring signal
        # that once flipped a taco stand to hybrid mid-interview.
        v = ex.get(k)
        if v in (None, "", []) or is_unknown(v):
            return None
        return slots.phrase(k, v) or None
    parts = []
    if _v("product"):
        parts.append(str(_v("product")))
    if _v("target_customer"):
        parts.append(f"Target customer: {_v('target_customer')}.")
    if _v("business_model"):
        parts.append(f"Business model: {_v('business_model')}.")
    if _v("geography"):
        parts.append(f"Located in {_v('geography')}.")
    for k in ("pricing", "stage", "site", "locations_count"):
        if _v(k):
            parts.append(f"{str(_v(k))}.")
    return " ".join(parts)


def _venture_blob(extracted: dict) -> str:
    """The venture as ITSELF, with the customer it sells to removed.

    MEASURED (2026-08-26): `_VENUE_RE` matched the word "clinics" inside the TARGET
    CUSTOMER of "a scheduling tool that businesses pay for every month. Target customer:
    small clinics." A B2B scheduling product was therefore classified physical, then
    transactional, with explicit=True and no fork, so the founder was confidently offered
    a cafe's question pack and would have been told on the first screen that their market
    is a walking-distance ring.

    Same defect family as R8 (inputs measure the customer, not the vendor): who you SELL
    TO is not evidence about what you ARE. Only the physicality predicate uses this;
    `_blob` keeps the customer for everything else, because a regulated customer industry
    genuinely is evidence about regulatory exposure.
    """
    ex = dict(extracted or {})
    ex.pop("target_customer", None)
    return _blob(ex)


def classify_turn(extracted: dict, user_text: str | None = None) -> dict:
    """Run the pipeline's own deterministic classifiers on what intake knows so far.

    Same code as the run (classify_with_confidence; the sizing module's deterministic
    override helpers), so the preview and the pipeline cannot disagree. Kind is a PREVIEW —
    the run reclassifies on the final brief — which is why the confirmation card labels it
    'inferred' rather than asserting it.

    `user_text` is the founder's OWN messages, joined. When given, `explicit` requires
    founder payment language (or a founder-named venue, which grounds pay-per-visit):
    MEASURED (taco-stand transcript, 2026-08-20), the extractor's fabricated paraphrase
    carried model keywords and manufactured explicit=True, so the fork never fired and a
    taco stand was interviewed as a hybrid. Callers without founder words (the pipeline
    classifying a final brief) pass None and are unchanged.
    """
    from skills.sizing.classify import (_DIGITAL_RE, _VENUE_RE, _is_client_services,
                                        _is_multi_location, _is_physical_local)

    ex = extracted or {}
    desc = _blob(ex)
    # Physicality FIRST, then kind — the same order the pipeline uses. classify_business_model
    # branches on market_scale's is_physical signal (a physical venue with no recurring
    # signal is transactional); calling it without that signal sent a Portland coffee shop
    # down the digital branch and out as "subscription". A preview that calls the shared
    # classifier with a different shape than the pipeline is drift wearing a seatbelt.
    #
    # INTAKE accepts venue-ness alone, where the pipeline's _is_physical_local also
    # demands a location: pre-location is exactly when the SITE question must be planned,
    # and a "taco stand" with no address yet is still a taco stand.
    # Physicality asks "is the VENTURE a venue", so it reads the venture without the
    # customer. See _venture_blob: "Target customer: small clinics" made a scheduling tool
    # a clinic.
    vdesc = _venture_blob(ex)
    physical = ((_is_physical_local(vdesc)
                 or (bool(_VENUE_RE.search(vdesc)) and not _DIGITAL_RE.search(vdesc)))
                and not _is_client_services(vdesc))
    multi = _is_multi_location(desc)

    profile = {
        "business_model": "" if is_unknown(ex.get("business_model"))
                          else slots.text(ex.get("business_model")),
        "category": slots.text(ex.get("product")),
        "summary": desc,
        "name": None,
    }
    cls = classify_with_confidence(
        profile, market_scale={"signals": {"is_physical": physical}} if physical else None)
    kind = cls.get("kind") or "transactional"

    # str(value) on a typed record alternates all 50 state abbreviations against its dict
    # repr, and a source of "us_census" alone would flip non_us to False and ship US
    # household-spend data for a foreign city with no disclosure. Render, never repr.
    geo = "" if is_unknown(ex.get("geography")) else slots.text(ex.get("geography"))
    non_us = bool(geo) and not _US_HINTS.search(geo)

    stage = "" if is_unknown(ex.get("stage")) else slots.text(ex.get("stage"))
    launched = bool(_LAUNCHED_RE.search(stage)) and not _IDEA_RE.search(stage)

    # The fork: the classifier inferred rather than read. `explicit` is False when nothing
    # in the brief named a revenue shape (the orbital "Undetermined" case) — exactly when a
    # silent pick shipped a seat-priced report for a venture that never chose seats.
    explicit = bool(cls.get("explicit"))
    if user_text is not None and explicit and not physical \
            and not founder_payment_words(user_text):
        # The classifier's keywords fired on EXTRACTED text the founder never typed —
        # the extractor's paraphrase cannot manufacture explicitness. A venue is exempt:
        # "taco stand" is the founder's own word and grounds pay-per-visit.
        explicit = False
    # THE FORK'S ANSWER OUTRANKS THE CLASSIFIER, because the fork is only ever asked when
    # the classifier already said it was unsure. Reading the pick is also the only thing
    # that works: the option labels are written in the founder's words on purpose, and the
    # keyword classifier does not recognise its own labels.
    picked = stated_kind(ex)
    disclosure = cls.get("disclosure")
    if picked:
        kind, explicit, disclosure = picked, True, None

    needs_fork = (not explicit) and not is_unknown(ex.get("business_model"))

    return {
        "kind": kind,
        "explicit": explicit,
        "needs_fork": needs_fork,
        "fork_question": FORK_QUESTION if needs_fork else None,
        "is_physical": physical,
        "multi_location": multi,
        "non_us": non_us,
        "launched": launched,
        "regulated": bool(_REGULATED_RE.search(desc)),
        "disclosure": disclosure,
    }


# --------------------------------------------------------------------------- the plan --
def plan_questions(extracted: dict, cls: dict) -> list[dict]:
    """Every question THIS venture should be asked, in priority order. Deterministic."""
    ex = extracted or {}
    plan: list[dict] = []

    if cls.get("needs_fork"):
        plan.append(_q("kind_fork", cls.get("fork_question") or FORK_QUESTION,
                       "every financial table takes a different shape depending on this",
                       "business_model.classify_with_confidence", "module",
                       input_kind="choice", options=KIND_OPTIONS, write_in=True))

    plan.extend(KIND_PACKS.get(cls.get("kind") or "", ()))

    if cls.get("is_physical"):
        if cls.get("multi_location"):
            plan.append(MODIFIER_PACKS["locations_count"])
        else:
            plan.append(MODIFIER_PACKS["site"])
        # Wave D (operator spec Q7): the founder's own expected volume, number plus a
        # period. Printed as the founder's estimate next to the model, never mixed in;
        # this is also the input whose absence D59 keeps surfacing.
        plan.append(MODIFIER_PACKS["expected_volume"])
    elif cls.get("kind") in ("ecommerce", "marketplace"):
        plan.append(MODIFIER_PACKS["expected_volume"])
    if cls.get("non_us"):
        plan.append(MODIFIER_PACKS["local_anchor"])
    if cls.get("launched"):
        plan.append(MODIFIER_PACKS["real_traction"])
    if cls.get("regulated"):
        plan.append(MODIFIER_PACKS["regulatory"])

    plan.extend(CORE_PACK)

    # Wave D (operator spec Q13): a quantified year-one target earns the how question.
    # The survey listens; the REPORT is where likelihood gets checked, respectfully.
    tgt = ex.get("success_target")
    if _answered(tgt) and not is_unknown(tgt) and _DIGIT_RE.search(slots.text(tgt)):
        plan.append(_q("target_basis",
                       "How did you arrive at that number? A sentence is plenty; the "
                       "report will compare it against what the market data supports.",
                       "whether your target is read as a goal or a forecast",
                       "viability framing", "brief"))

    # A free product's pack must not inherit a price question from elsewhere (C7).
    if cls.get("kind") == "ad_supported":
        plan = [q for q in plan if q["field"] not in ("avg_ticket", "avg_order")]
    return _apply_input_specs(plan)


# A pack question is also satisfied by a GENERIC field that already answers it — the
# extractor often files "$6.50 a drink" under `pricing`, and re-asking a price the founder
# already gave reads as not listening, the opposite of rigour. The alias counts only when
# the generic value actually carries the substance (a digit, for a price).
_ALIASES: dict[str, tuple[str, ...]] = {
    "avg_ticket": ("pricing",), "avg_order": ("pricing",),
    "avg_transaction": ("pricing",), "rate_basis": ("pricing",),
    "site": ("geography",),
}
_DIGIT_RE = re.compile(r"\d")


def _alias_satisfies(field: str, ex: dict) -> bool:
    # Both predicates run on the RENDERED value, never on str(value). Against a typed
    # record's dict repr the digit test always passes (every repr carries digits) and
    # _SITE_RE matches on a bare \d, so a typed `pricing` would have silently satisfied
    # avg_ticket / avg_order / avg_transaction / rate_basis and a typed `geography` would
    # have suppressed the site question: four price questions and the corner question,
    # never asked, with nothing in the transcript to show why.
    for alias in _ALIASES.get(field, ()):
        v = ex.get(alias)
        if v in (None, "", []) or is_unknown(v):
            continue
        said = slots.text(v)
        if alias == "pricing" and not _DIGIT_RE.search(said):
            continue                     # "pay per drink" fills the slot, not the need
        if alias == "geography" and not _SITE_RE.search(said):
            continue                     # "Portland" is a list of sites, not a site
        return True
    return False


def next_question(extracted: dict, cls: dict) -> Optional[dict]:
    """The first planned question whose field is neither answered, declined, nor already
    covered by a generic field."""
    ex = extracted or {}
    if cls.get("needs_fork") and not _answered(ex.get("kind_fork")):
        for q in plan_questions(ex, cls):
            if q["field"] == "kind_fork":
                return q
    for q in plan_questions(ex, cls):
        if not _answered(ex.get(q["field"])) and not _alias_satisfies(q["field"], ex):
            return q
    return None


def tree_fields(extracted: dict, cls: dict) -> list[dict]:
    """The active pack as (field, label, state) for the UI's progress chips."""
    ex = extracted or {}
    out = []
    for q in plan_questions(ex, cls):
        v = ex.get(q["field"])
        state = ("assumed" if is_unknown(v)
                 else "done" if (_answered(v) or _alias_satisfies(q["field"], ex))
                 else "open")
        label = q["field"].replace("_", " ")
        out.append({"field": q["field"], "label": label, "state": state})
    return out
