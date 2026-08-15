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
    r"\b(?:in|at|near|around|located in)\s+(?:the\s+)?"
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
