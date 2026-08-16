"""brief.py — reading the two facts out of a founder's own words.

Moved verbatim out of plan.py (#87 wave 3). Bodies byte-identical; plan.py re-exports.

WHY IT EXISTS AS ITS OWN MODULE, and why here rather than under skills/sizing/. These two
parsers are what turn a paragraph into the inputs that decide the numbers:

  extract_location      -> the address the trade area is drawn around
  extract_stated_price  -> the figure break-even, the planning target and the ceiling
                           are all computed from

Both are consumed by the SIZING family and by the PRICING reconciliation, and neither is
about sizing per se — they are brief-parsing. A top-level module is dependency-neutral:
plan.py and skills/sizing/ can each import it without the cycle that blocked #87's remaining
waves, which all reach one of these names.

THE SENTENCE-BOUNDARY TRIM IS LOAD-BEARING, not tidying. extract_location once returned
"Mission District of San Francisco. It" from "...in the Mission District of San Francisco. It
offers high-quality..." — the trailing fragment geocoded, so nothing failed loudly, and OSM
returned 0 competitors against the previous run's 102. The whole competitive census
disappeared because a regex ate a full stop. _SENTENCE_END therefore knows about "St. Louis",
"Mt. Vernon" and "$5.50", because each of those is a real address or price this pipeline sees.
"""
from __future__ import annotations

import re
from typing import Optional  # noqa: F401  (kept for the moved signatures)


_STATED_PRICE_RE = re.compile(
    r"\$\s*(\d[\d,]*\.?\d*)\s*(?:/|\s*per\s*)?\s*(?:mo|month|monthly|/mo\b|/month\b)",
    re.I)
def extract_stated_price(text: str) -> float | None:
    """Pull the user's stated monthly price from free text ($99/month, $99/mo, …).

    Returns the first monthly price found, or None. cycle33 / C5.
    """
    if not text:
        return None
    m = _STATED_PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


# --------------------------------------------------------------------------------------
# ONE price extractor, sharing ONE vocabulary with the unit resolver.
#
# MEASURED before this existed, over 20 ordinary ways a founder states their own price,
# through the real chain (unit_for_model -> extract_unit_price / extract_device_price /
# extract_stated_price -> price_of_record) with the PSM point pinned at $38:
#
#     PRICE OF RECORD CORRECT: 4/20 = 20%
#
# 13 of the 16 misses landed on `basis="PSM optimal"` with `differs_from_psm: False` — the
# report telling the founder the model agrees with a price it never read. Three returned a
# real number that was the wrong one:
#
#     "the hardware costs $249 up front, then $9 monthly"  -> $9   basis "stated price"
#     "buy the monitor for $329, subscribe at $12/mo"      -> $12  basis "stated price"
#
# That is the app fee sold as the hardware price — the exact defect extract_device_price
# (B2/D17) was written for, still live because _DEVICE_PRICE_RE requires the `$` BEFORE the
# device noun and "the hardware costs $249" puts the noun first. Against real hardware COGS
# that is a -700% margin, economics errors out, and financials falls back to subscription —
# churn and lifetime value on a one-time sale.
#
# The cause was four hand-maintained lists that disagreed with each other AND with the unit
# the same codebase picks: plan._UNIT_NOUN_RE could NAME a venture's unit as sprint, jar,
# engagement or kit, and no extractor could READ a price in any of them. A module that names
# a unit it cannot price is not missing a pattern; it is two parsers never introduced.
#
# UNIT_NOUNS is that single vocabulary. plan._UNIT_NOUN_RE is built from it, so a noun added
# here is nameable and priceable in the same commit, and the two cannot drift again.
# --------------------------------------------------------------------------------------
UNIT_NOUNS: tuple[str, ...] = (
    # food and drink
    "drink", "cup", "coffee", "latte", "espresso", "beverage", "meal", "plate", "dish",
    "entree", "cover", "bowl", "burrito", "taco", "sandwich", "salad", "pizza", "slice",
    "scoop", "cone", "pint", "glass", "pastry", "loaf", "cookie",
    # visits and appointments
    "visit", "ticket", "session", "class", "lesson", "drop-in", "ride", "trip",
    "haircut", "cut", "treatment", "appointment", "booking", "night", "room",
    # goods
    "item", "order", "box", "bag", "bottle", "jar", "unit", "device", "hardware",
    "kit", "pair", "board", "sensor", "monitor", "gadget", "appliance",
    # services
    "project", "engagement", "sprint", "retainer", "audit",
    # recurring seats
    "seat", "user", "account", "workspace", "licence", "license", "member",
    # measured
    "meter", "sq ft", "square foot", "hour", "day", "head", "person", "guest",
)

#: Symbol or code -> ISO code. Detection only: nothing here converts, and a non-USD figure
#: must be DISCLOSED as unconverted rather than quietly treated as dollars or dropped. The
#: lie was the silence, not the dollar sign.
_CURRENCIES = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR",
               "£": "GBP", "gbp": "GBP", "¥": "JPY", "jpy": "JPY"}
_CUR_RE = r"(?P<cur>[$€£¥]|\b(?:USD|EUR|GBP|JPY)\b)"
_AMT_RE = r"(?P<amt>\d[\d,]*(?:\.\d+)?)"
_MONTHLY_RE = r"(?:per\s+month|/\s*mo(?:nth)?\b|a\s+month|monthly)"
_YEARLY_RE = r"(?:per\s+year|/\s*yr\b|annually|a\s+year|per\s+annum|yearly)"
#: The verbs a founder puts between a unit and its price. Bounded, so "the box we ship to
#: 400 customers is $54" does not bind 400 to "box".
_LEADIN_RE = (r"(?:costs?|is|are|sells?\s+for|retails?\s+(?:at|for)|priced\s+at|at|for|"
              r"starts?\s+at|goes\s+for)")


def _noun_pattern(noun: str) -> str:
    """`box` must match "box" and "boxes"; `drink` must match "drink" and "drinks".

    A first version branched on the ending (`es?` after s/x/ch) and thereby stopped matching
    the SINGULAR of every such noun — box, class, glass and sandwich all became unpriceable,
    which is how a vocabulary quietly loses four entries. `(?:e?s)?` covers both.
    """
    return re.escape(noun) + "(?:e?s)?"


def _noun_alternation(unit_noun: str | None) -> str:
    """The venture's own noun first, then the shared vocabulary, longest first so
    "square foot" is not eaten by "foot"."""
    seen: set[str] = set()
    ordered: list[str] = []
    for noun in ([unit_noun] if unit_noun else []) + list(UNIT_NOUNS):
        n = (noun or "").strip().lower()
        if n and n not in seen:
            seen.add(n)
            ordered.append(n)
    ordered.sort(key=len, reverse=True)
    return "|".join(_noun_pattern(n) for n in ordered)


def extract_price(text: str, unit_noun: str | None = None) -> dict | None:
    """The one price reader. Returns {value, currency, basis, period, unit} or None.

    Order is the point, not just coverage. A per-unit price is looked for BEFORE any
    recurring phrase, and a match that is itself recurring is skipped, so a hybrid's
    one-time hardware price wins over the /mo app fee sitting in the same sentence. The
    recurring patterns run last and only when nothing per-unit was stated.

    `basis` names the unit it read so a reader can check the parse, and None means exactly
    that — no price in the brief — which the caller must surface rather than silently
    substituting a modelled figure.
    """
    if not text:
        return None
    alt = _noun_alternation(unit_noun)
    recurring = f"{_MONTHLY_RE}|{_YEARLY_RE}"
    patterns = (
        # price then unit: "$6 per drink", "$499 per seat per month", "EUR 3.50 per pastry"
        (rf"{_CUR_RE}\s*{_AMT_RE}\s*(?:/|per|a|an|each)\s*(?P<noun>{alt})\b", None),
        # unit then price: "the hardware costs $249", "each kit sells for $65"
        (rf"(?P<noun>{alt})\b(?:\s+\w+){{0,3}}?\s+{_LEADIN_RE}\s+{_CUR_RE}\s*{_AMT_RE}", None),
        # adjacent, no preposition: "$199 device", "$65 starter kit"
        (rf"{_CUR_RE}\s*{_AMT_RE}\s*(?:\w+\s+){{0,2}}?(?P<noun>{alt})\b", None),
        # recurring, LAST — a monthly figure is the price only if nothing per-unit is stated
        (rf"{_CUR_RE}\s*{_AMT_RE}\s*{_MONTHLY_RE}", "month"),
        (rf"{_CUR_RE}\s*{_AMT_RE}\s*{_YEARLY_RE}", "year"),
    )
    for pattern, fixed_period in patterns:
        for m in re.finditer(pattern, text, re.I):
            if fixed_period is None and re.search(recurring, m.group(0), re.I):
                continue        # this occurrence IS the recurring leg, not the unit price
            try:
                value = float(m.group("amt").replace(",", ""))
            except (TypeError, ValueError):
                continue
            noun = (m.groupdict().get("noun") or "").lower()
            period = fixed_period
            if period is None:
                tail = text[m.end():m.end() + 24]
                if re.match(rf"\s*{_MONTHLY_RE}", tail, re.I):
                    period = "month"
                elif re.match(rf"\s*{_YEARLY_RE}", tail, re.I):
                    period = "year"
            return {
                "value": value,
                "currency": _CURRENCIES.get((m.group("cur") or "$").lower(), "USD"),
                "basis": (f"stated price per {noun}" if noun
                          else f"stated {period or 'unit'} price"),
                "period": period,
                "unit": noun or None,
            }
    return None


#: Small counts are usually written as words in a brief ("a three-location chain").
_NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
}
#: Nouns that mean "a premises we operate". `unit` is DELIBERATELY absent — "6 units of
#: cold brew" is stock, not sites, and that false positive would multiply a TAM.
_PREMISES = r"(?:locations?|stores?|sites?|branches|branch|outlets?|shops?|cafes?|" \
            r"restaurants?|salons?|studios?|storefronts?|premises)"
_LOCATION_COUNT_RE = re.compile(
    rf"\b(\d{{1,3}}|{'|'.join(_NUMBER_WORDS)})[\s-]{_PREMISES}\b", re.I)


def extract_location_count(text: str) -> int | None:
    """How many premises the venture operates, or None when the brief does not say.

    Routes a multi-site venture to `size_regional` instead of publishing ONE site's trade
    area as its whole market. MEASURED before this existed, with the trade-area sizer
    mocked to a $4.0M single site: a three-location chain, a five-store bakery and a
    four-site rollout all published $4.0M and `n_locations: None`.

    Word numerals are read here and were refused for volume claims (#100) for a reason:
    this pattern REQUIRES a premises noun immediately after the number, so "three-location"
    matches while "one of the two channels" cannot. The noun list omits `unit` on purpose —
    "6 units of cold brew" is inventory, and mistaking it for six shops multiplies a TAM.

    A count of 1 returns None: one site is not a rollout, however it is phrased.
    """
    if not text:
        return None
    m = _LOCATION_COUNT_RE.search(text)
    if not m:
        return None
    raw = m.group(1).lower()
    try:
        n = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
    except (ValueError, KeyError):
        return None
    return n if n > 1 else None


_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z0-9][\w.'-]*(?:\s+[\w.'-]+){0,4}\s+"
    r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Plaza)\b",
    re.I)
_PLACE_RE = re.compile(
    # "in <Neighborhood>[, <City>][, <State>]" — capture the full comma-chain of
    # Capitalized localities so an ambiguous neighborhood keeps its city qualifier
    # (e.g. "Highland Park, Los Angeles" → not Highland Park, Illinois). A lowercase
    # word after a comma (", casual dinner") ends the chain.
    # Measured on a real run: "opening in the Mission District of San Francisco" extracted
    # NOTHING, because the lowercase article ended the match before it began and the city was
    # chained with "of" rather than a comma. The whole hyperlocal path hung on that miss.
    # "across" / "throughout" / "serving" were missing, and a MULTI-SITE brief is exactly
    # the shape that uses them: "5 locations across Austin, Texas" extracted nothing, so a
    # regional venture — the one that most needs a trade area — got no sizing at all. The
    # audit filed this as low BECAUSE the refusal is safe ("no street address was
    # available"); it is safe only for a single-site brief.
    r"\b(?:in|at|near|around|located in|across|throughout|serving)\s+(?:the\s+)?"
    r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}"
    r"(?:(?:,|\s+of)\s*[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,2}){0,2})")
def extract_location(text: str) -> str | None:
    """Best-effort physical-location extraction: a street address, else 'in <Place>'.

    Returns a geocodable-ish string or None. cycle35 — feeds hyperlocal routing.
    """
    if not text:
        return None
    m = _STREET_RE.search(text)
    if m:
        return m.group(0).strip()
    m = _PLACE_RE.search(text)
    return _trim_at_sentence_end(m.group(1)) if m else None
_SENTENCE_END = re.compile(
    r"""(?:
          (?<!\bSt)(?<!\bMt)(?<!\bFt)(?<!\bSte)(?<!\bPt)(?<!\bAve)(?<!\bRd)
          [.!?](?=\s+[A-Z])    # terminator + whitespace + capital: a new sentence...
                                # ...unless it followed a place abbreviation, because
                                # "St. Louis" and "Mt. Vernon" match that shape exactly
                                # and cutting there leaves "St".
        | [;\n\r]              # semicolon or newline: always a break
        | \s+[—–]\s+           # spaced em/en dash: an aside, not part of the place
        )""",
    re.X,
)
def _trim_at_sentence_end(place: str) -> str | None:
    """Cut a captured place at the first sentence boundary inside it.

    MEASURED on run16: "...in the Mission District of San Francisco. It offers..." captured
    "Mission District of San Francisco. It". That string still geocoded to San Francisco —
    so households looked right at 38,877 and nothing appeared wrong — while the OSM
    competitor query built from it returned ZERO venues against run15's 102, failing D07
    and D59 together. A location is only ever one sentence long; anything past the
    boundary is the next sentence leaking in.
    """
    if not place:
        return None
    m = _SENTENCE_END.search(place)
    if m:
        place = place[:m.start()]
    place = place.strip().rstrip(".,;:-—– ")
    return place or None
