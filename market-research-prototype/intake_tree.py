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
    "site": {"input_kind": "location"},
    "named_competitors": {"optional": True},
    "status_quo": {"optional": True},
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
        v = ex.get(k)
        return None if is_unknown(v) else v
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


def classify_turn(extracted: dict) -> dict:
    """Run the pipeline's own deterministic classifiers on what intake knows so far.

    Same code as the run (classify_with_confidence; the sizing module's deterministic
    override helpers), so the preview and the pipeline cannot disagree. Kind is a PREVIEW —
    the run reclassifies on the final brief — which is why the confirmation card labels it
    'inferred' rather than asserting it.
    """
    from skills.sizing.classify import (_is_client_services, _is_multi_location,
                                        _is_physical_local)

    ex = extracted or {}
    desc = _blob(ex)
    # Physicality FIRST, then kind — the same order the pipeline uses. classify_business_model
    # branches on market_scale's is_physical signal (a physical venue with no recurring
    # signal is transactional); calling it without that signal sent a Portland coffee shop
    # down the digital branch and out as "subscription". A preview that calls the shared
    # classifier with a different shape than the pipeline is drift wearing a seatbelt.
    physical = _is_physical_local(desc) and not _is_client_services(desc)
    multi = _is_multi_location(desc)

    profile = {
        "business_model": "" if is_unknown(ex.get("business_model"))
                          else (ex.get("business_model") or ""),
        "category": ex.get("product") or "",
        "summary": desc,
        "name": None,
    }
    cls = classify_with_confidence(
        profile, market_scale={"signals": {"is_physical": physical}} if physical else None)
    kind = cls.get("kind") or "transactional"

    geo = "" if is_unknown(ex.get("geography")) else str(ex.get("geography") or "")
    non_us = bool(geo) and not _US_HINTS.search(geo)

    stage = "" if is_unknown(ex.get("stage")) else str(ex.get("stage") or "")
    launched = bool(_LAUNCHED_RE.search(stage)) and not _IDEA_RE.search(stage)

    # The fork: the classifier inferred rather than read. `explicit` is False when nothing
    # in the brief named a revenue shape (the orbital "Undetermined" case) — exactly when a
    # silent pick shipped a seat-priced report for a venture that never chose seats.
    needs_fork = (not cls.get("explicit")) and not is_unknown(ex.get("business_model"))

    return {
        "kind": kind,
        "explicit": bool(cls.get("explicit")),
        "needs_fork": needs_fork,
        "fork_question": FORK_QUESTION if needs_fork else None,
        "is_physical": physical,
        "multi_location": multi,
        "non_us": non_us,
        "launched": launched,
        "regulated": bool(_REGULATED_RE.search(desc)),
        "disclosure": cls.get("disclosure"),
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
    if _answered(tgt) and not is_unknown(tgt) and re.search(r"\d", str(tgt)):
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
    for alias in _ALIASES.get(field, ()):
        v = ex.get(alias)
        if v in (None, "", []) or is_unknown(v):
            continue
        if alias == "pricing" and not _DIGIT_RE.search(str(v)):
            continue                     # "pay per drink" fills the slot, not the need
        if alias == "geography" and not _SITE_RE.search(str(v)):
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
